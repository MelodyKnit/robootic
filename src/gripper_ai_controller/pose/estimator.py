"""CUDA-only Torchvision Keypoint R-CNN adapter and safe monochrome frame conversion."""

from abc import ABC, abstractmethod
import os
from pathlib import Path
import tempfile
from typing import Any, Optional, Sequence, Tuple
from urllib.request import urlopen

from PIL import Image

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D


class PoseEstimatorError(RuntimeError):
    """Report an inference dependency, model, or input failure without leaking SDK internals."""


KEYPOINT_RCNN_WEIGHTS_URL = (
    "https://download.pytorch.org/models/keypointrcnn_resnet50_fpn_coco-fc266e95.pth"
)
"""Official Torchvision COCO Keypoint R-CNN weight location for explicit local download."""


class PoseEstimator(ABC):
    """The small synchronous boundary used by the async pose tracking service."""

    @abstractmethod
    def infer(self, frame: ImageFrame) -> Tuple[PoseCandidate, ...]:
        """Return all person candidates detected in one normalized camera frame."""


def frame_to_rgb_image(frame: ImageFrame) -> Image.Image:
    """Convert one normalized frame to a Pillow RGB image without a Python pixel loop.

    Pillow performs the Mono8 channel replication in native code. This preserves the
    monochrome luminance data expected by an RGB-trained model without inventing color.
    """

    if not frame.healthy or frame.pixel_payload is None:
        raise PoseEstimatorError("A healthy camera frame with pixels is required for pose inference.")
    if frame.width is None or frame.height is None or frame.width <= 0 or frame.height <= 0:
        raise PoseEstimatorError("Camera frame dimensions are required for pose inference.")
    pixel_count = frame.width * frame.height
    if frame.pixel_format == "rgb8":
        expected_length = pixel_count * 3
        if len(frame.pixel_payload) != expected_length:
            raise PoseEstimatorError("The RGB camera payload does not match its declared dimensions.")
        return Image.frombytes("RGB", (frame.width, frame.height), frame.pixel_payload)
    if frame.pixel_format == "mono8":
        if len(frame.pixel_payload) != pixel_count:
            raise PoseEstimatorError("The monochrome camera payload does not match its declared dimensions.")
        return Image.frombytes("L", (frame.width, frame.height), frame.pixel_payload).convert("RGB")
    raise PoseEstimatorError("The camera pixel format is not supported for pose inference.")


def frame_to_rgb_bytes(frame: ImageFrame) -> bytes:
    """Return RGB bytes for compatibility with callers that inspect conversion output."""

    return frame_to_rgb_image(frame).tobytes()


def resize_image_for_inference(image: Image.Image, maximum_side: int) -> Image.Image:
    """Downscale an image proportionally when its longest side exceeds the configured cap."""

    if maximum_side <= 0:
        raise PoseEstimatorError("The pose inference maximum image side must be positive.")
    longest_side = max(image.size)
    if longest_side <= maximum_side:
        return image
    scale = float(maximum_side) / float(longest_side)
    target_size = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    resampling = getattr(Image, "Resampling", Image)
    return image.resize(target_size, resampling.BILINEAR)


def download_keypoint_rcnn_weights(destination: str) -> str:
    """Download official model weights only after an explicit CLI request to a local path.

    The file is streamed to a sibling temporary file and atomically replaced, so an
    interrupted download never leaves a partial checkpoint at the configured path.
    """

    destination_path = Path(destination)
    temporary_path = None
    try:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="{0}.".format(destination_path.name),
            suffix=".tmp",
            dir=str(destination_path.parent),
        )
        with os.fdopen(descriptor, "wb") as handle:
            with urlopen(KEYPOINT_RCNN_WEIGHTS_URL, timeout=30) as response:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
    except OSError as error:
        raise PoseEstimatorError("The local Keypoint R-CNN weights file could not be written.") from error
    except Exception as error:
        raise PoseEstimatorError("The official Keypoint R-CNN weights could not be downloaded.") from error
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return str(destination_path)


class TorchvisionKeypointRcnnEstimator(PoseEstimator):
    """Run the pinned COCO Keypoint R-CNN model only on a CUDA-capable Torch installation.

    The estimator never downloads weights automatically. Operators explicitly obtain the
    documented official weights into ``localstore/`` and select their relative path in a
    local configuration file, keeping model artifacts out of Git and global caches.
    """

    def __init__(
        self,
        weights_path: Optional[str],
        device: str = "cuda",
        inference_max_side: int = 768,
        torch_cpu_threads: int = 2,
        torch_interop_threads: int = 1,
    ) -> None:
        """Store explicit model configuration without importing Torch or allocating GPU memory."""

        self.weights_path = weights_path
        self.device = device
        self.inference_max_side = inference_max_side
        self.torch_cpu_threads = torch_cpu_threads
        self.torch_interop_threads = torch_interop_threads
        self._torch = None  # type: Optional[Any]
        self._model = None  # type: Optional[Any]

    def infer(self, frame: ImageFrame) -> Tuple[PoseCandidate, ...]:
        """Run one CUDA inference and map only COCO person candidates into typed contracts."""

        torch, model, pil_to_tensor = self._load_model()
        source_image = frame_to_rgb_image(frame)
        image = resize_image_for_inference(source_image, self.inference_max_side)
        tensor = pil_to_tensor(image).to(device=self.device, dtype=torch.float32).div_(255.0)
        with torch.inference_mode():
            prediction = model([tensor])[0]
        scale_x = float(source_image.width) / float(image.width)
        scale_y = float(source_image.height) / float(image.height)
        return self._map_prediction(
            prediction,
            source_image.width,
            source_image.height,
            scale_x,
            scale_y,
        )

    def _load_model(self) -> Tuple[Any, Any, Any]:
        """Lazily import CUDA dependencies and load only a caller-provided local weight file."""

        if self._model is not None and self._torch is not None:
            from torchvision.transforms.functional import pil_to_tensor

            return self._torch, self._model, pil_to_tensor
        if self.device != "cuda":
            raise PoseEstimatorError("Pose inference is configured for CUDA only; CPU fallback is disabled.")
        if not self.weights_path:
            raise PoseEstimatorError("A local Keypoint R-CNN weights path is required before pose inference.")
        weights_file = Path(self.weights_path)
        if not weights_file.is_file():
            raise PoseEstimatorError("The configured local Keypoint R-CNN weights file is unavailable.")
        try:
            import torch
            from torchvision.models.detection import keypointrcnn_resnet50_fpn
            from torchvision.transforms.functional import pil_to_tensor
        except ImportError as error:
            raise PoseEstimatorError("CUDA Torch and Torchvision dependencies are not installed.") from error
        if not torch.cuda.is_available():
            raise PoseEstimatorError("CUDA is unavailable; CPU pose inference is intentionally disabled.")
        try:
            torch.set_num_threads(self.torch_cpu_threads)
            torch.set_num_interop_threads(self.torch_interop_threads)
            model = keypointrcnn_resnet50_fpn(weights=None, weights_backbone=None)
            self._configure_model_input_limit(model, self.inference_max_side)
            state_dict = torch.load(str(weights_file), map_location="cuda")
            model.load_state_dict(state_dict)
            model.to("cuda")
            model.eval()
        except Exception as error:
            raise PoseEstimatorError("The local Keypoint R-CNN model could not be loaded on CUDA.") from error
        self._torch = torch
        self._model = model
        return torch, model, pil_to_tensor

    @staticmethod
    def _configure_model_input_limit(model: Any, maximum_side: int) -> None:
        """Keep Torchvision from enlarging a frame after bounded preprocessing.

        Detection models apply an internal ``GeneralizedRCNNTransform`` even when the
        caller already resized the image. Setting both limits to the configured cap
        preserves aspect ratio while preventing the default 800/1333 resize policy
        from silently increasing CUDA work again.
        """

        if maximum_side <= 0:
            raise PoseEstimatorError("The pose inference maximum image side must be positive.")
        try:
            model.transform.min_size = (maximum_side,)
            model.transform.max_size = maximum_side
        except AttributeError as error:
            raise PoseEstimatorError("The Keypoint R-CNN input transform is unavailable.") from error

    @staticmethod
    def _map_prediction(
        prediction: Any,
        width: int,
        height: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> Tuple[PoseCandidate, ...]:
        """Map Torchvision tensors without passing tensors or COCO class indexes to callers."""

        try:
            boxes = prediction["boxes"].detach().cpu().tolist()
            scores = prediction["scores"].detach().cpu().tolist()
            labels = prediction["labels"].detach().cpu().tolist()
            keypoints = prediction["keypoints"].detach().cpu().tolist()
            raw_joint_scores = prediction.get("keypoints_scores")
            joint_scores = None if raw_joint_scores is None else raw_joint_scores.detach().cpu().tolist()
        except (AttributeError, KeyError, TypeError) as error:
            raise PoseEstimatorError("Keypoint R-CNN returned an invalid prediction structure.") from error
        candidates = []
        for index, score in enumerate(scores):
            if index >= len(boxes) or index >= len(labels) or index >= len(keypoints):
                continue
            if int(labels[index]) != 1:
                continue
            raw_candidate_joint_scores = None
            if joint_scores is not None and index < len(joint_scores):
                raw_candidate_joint_scores = joint_scores[index]
            candidate = TorchvisionKeypointRcnnEstimator._map_candidate(
                boxes[index],
                float(score),
                keypoints[index],
                raw_candidate_joint_scores,
                width,
                height,
                scale_x,
                scale_y,
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    @staticmethod
    def _map_candidate(
        raw_box: Sequence[float],
        confidence: float,
        raw_keypoints: Sequence[Sequence[float]],
        raw_joint_scores: Optional[Sequence[float]],
        width: int,
        height: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> Optional[PoseCandidate]:
        """Build one bounded candidate while rejecting malformed model tensor values."""

        if len(raw_box) != 4 or len(raw_keypoints) != len(COCO_KEYPOINT_NAMES):
            return None
        if raw_joint_scores is not None and len(raw_joint_scores) != len(COCO_KEYPOINT_NAMES):
            return None
        if scale_x <= 0.0 or scale_y <= 0.0:
            raise PoseEstimatorError("Pose prediction coordinate scales must be positive.")
        left, top, right, bottom = [float(value) for value in raw_box]
        left *= scale_x
        right *= scale_x
        top *= scale_y
        bottom *= scale_y
        box_width = max(0.0, right - left)
        box_height = max(0.0, bottom - top)
        if box_width <= 0.0 or box_height <= 0.0:
            return None
        bounding_box = BoundingBox2D(
            max(0.0, min(1.0, left / width)),
            max(0.0, min(1.0, top / height)),
            max(0.0, min(1.0, box_width / width)),
            max(0.0, min(1.0, box_height / height)),
        )
        joints = []
        for index, name in enumerate(COCO_KEYPOINT_NAMES):
            keypoint = raw_keypoints[index]
            if len(keypoint) < 2:
                return None
            x_value = float(keypoint[0]) * scale_x
            y_value = float(keypoint[1]) * scale_y
            # A missing score must never silently turn an uncertain joint into a valid lock.
            confidence_value = 0.0 if raw_joint_scores is None else float(raw_joint_scores[index])
            joints.append(
                PoseJoint2D(
                    name,
                    x_value,
                    y_value,
                    max(0.0, min(1.0, x_value / width)),
                    max(0.0, min(1.0, y_value / height)),
                    max(0.0, min(1.0, confidence_value)),
                )
            )
        return PoseCandidate(bounding_box, max(0.0, min(1.0, confidence)), tuple(joints))
