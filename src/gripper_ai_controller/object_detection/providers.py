"""Read-only two-dimensional object detection providers for Torchvision and YOLO-World ONNX."""

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy
from PIL import Image

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.object_detection.models import (
    DetectionCandidate,
    DetectionProvider,
    DetectionProviderError,
)


COCO_INSTANCE_CATEGORY_NAMES = (
    "__background__",
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "N/A",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "N/A",
    "backpack",
    "umbrella",
    "N/A",
    "N/A",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "N/A",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "N/A",
    "dining table",
    "N/A",
    "N/A",
    "toilet",
    "N/A",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "N/A",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)
"""Torchvision COCO label indexes, including empty slots from the official dataset."""


class TorchvisionFasterRcnnProvider(DetectionProvider):
    """Run Torchvision Faster R-CNN ResNet50-FPN with explicit local weights."""

    provider_id = "torchvision-faster-rcnn-resnet50-fpn"

    def __init__(
        self,
        weights_path: str,
        device: str = "cuda",
        confidence_threshold: float = 0.50,
        allowed_labels: Sequence[str] = (),
        inference_max_side: int = 960,
        max_detections: int = 100,
    ) -> None:
        """Store model selection; load Torch and weights only for the first inference frame."""

        if not isinstance(weights_path, str) or not weights_path:
            raise ValueError("Torchvision Faster R-CNN requires a local weights path.")
        if device not in ("cpu", "cuda"):
            raise ValueError("Torchvision detection device must be cpu or cuda.")
        _require_probability(confidence_threshold, "confidence_threshold")
        _require_positive_integer(inference_max_side, "inference_max_side", 32, 4096)
        _require_positive_integer(max_detections, "max_detections", 1, 1000)
        allowed = _validated_labels(allowed_labels, allow_empty=True)
        self.weights_path = weights_path
        self.device = device
        self.confidence_threshold = float(confidence_threshold)
        self.allowed_labels = allowed
        self.inference_max_side = inference_max_side
        self.max_detections = max_detections
        self._torch = None  # type: Optional[Any]
        self._model = None  # type: Optional[Any]
        self._pil_to_tensor = None  # type: Optional[Callable[[Image.Image], Any]]

    def infer(self, frame: ImageFrame) -> Tuple[DetectionCandidate, ...]:
        """Run one local inference and map results back to normalized source-image coordinates."""

        torch, model, pil_to_tensor = self._load_model()
        source_image = _frame_to_rgb_image(frame)
        image = _resize_image(source_image, self.inference_max_side)
        tensor = pil_to_tensor(image).to(device=self.device, dtype=torch.float32).div_(255.0)
        try:
            with torch.inference_mode():
                prediction = model([tensor])[0]
        except Exception as error:
            raise DetectionProviderError("Torchvision Faster R-CNN inference failed.") from error
        return self.map_prediction(
            prediction,
            source_image.width,
            source_image.height,
            scale_x=float(source_image.width) / float(image.width),
            scale_y=float(source_image.height) / float(image.height),
            confidence_threshold=self.confidence_threshold,
            allowed_labels=self.allowed_labels,
            max_detections=self.max_detections,
        )

    def _load_model(self) -> Tuple[Any, Any, Callable[[Image.Image], Any]]:
        """Build the model lazily without triggering Torchvision automatic weight downloads."""

        if self._torch is not None and self._model is not None and self._pil_to_tensor is not None:
            return self._torch, self._model, self._pil_to_tensor
        weights_file = Path(self.weights_path)
        if not weights_file.is_file():
            raise DetectionProviderError("The configured local Faster R-CNN weights file is unavailable.")
        try:
            import torch
            from torchvision.models.detection import fasterrcnn_resnet50_fpn
            from torchvision.transforms.functional import pil_to_tensor
        except ImportError as error:
            raise DetectionProviderError("Torch and Torchvision detection dependencies are unavailable.") from error
        if self.device == "cuda" and not torch.cuda.is_available():
            raise DetectionProviderError("CUDA detection was requested, but Torch cannot access CUDA.")
        try:
            model = fasterrcnn_resnet50_fpn(weights=None, weights_backbone=None)
            self.configure_model_input_limit(model, self.inference_max_side)
            state_dict = torch.load(str(weights_file), map_location=self.device)
            model.load_state_dict(state_dict)
            model.to(self.device)
            model.eval()
        except Exception as error:
            raise DetectionProviderError("The local Faster R-CNN model could not be loaded.") from error
        self._torch = torch
        self._model = model
        self._pil_to_tensor = pil_to_tensor
        return torch, model, pil_to_tensor

    @staticmethod
    def configure_model_input_limit(model: Any, maximum_side: int) -> None:
        """Prevent Torchvision from enlarging input after the caller already downscaled it."""

        _require_positive_integer(maximum_side, "maximum_side", 32, 4096)
        try:
            model.transform.min_size = (maximum_side,)
            model.transform.max_size = maximum_side
        except AttributeError as error:
            raise DetectionProviderError("The Faster R-CNN input transform is unavailable.") from error

    @staticmethod
    def map_prediction(
        prediction: Any,
        source_width: int,
        source_height: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        confidence_threshold: float = 0.50,
        allowed_labels: Sequence[str] = (),
        max_detections: int = 100,
        class_names: Sequence[str] = COCO_INSTANCE_CATEGORY_NAMES,
    ) -> Tuple[DetectionCandidate, ...]:
        """Parse Torchvision tensors without exposing tensors or empty COCO slots upstream."""

        _require_image_dimensions(source_width, source_height)
        if scale_x <= 0.0 or scale_y <= 0.0:
            raise DetectionProviderError("Torchvision detection coordinate scales must be positive.")
        _require_probability(confidence_threshold, "confidence_threshold")
        _require_positive_integer(max_detections, "max_detections", 1, 1000)
        labels_by_id = tuple(class_names)
        if not labels_by_id:
            raise DetectionProviderError("Torchvision detection class names are unavailable.")
        allowed = set(_validated_labels(allowed_labels, allow_empty=True))
        try:
            boxes = _tensor_values(prediction["boxes"])
            scores = _tensor_values(prediction["scores"])
            labels = _tensor_values(prediction["labels"])
        except (AttributeError, KeyError, TypeError) as error:
            raise DetectionProviderError("Faster R-CNN returned an invalid prediction structure.") from error
        if len(boxes) != len(scores) or len(scores) != len(labels):
            raise DetectionProviderError("Faster R-CNN prediction arrays have inconsistent lengths.")
        detections = []  # type: List[DetectionCandidate]
        for raw_box, raw_score, raw_class_id in zip(boxes, scores, labels):
            try:
                score = float(raw_score)
                class_id = int(raw_class_id)
            except (TypeError, ValueError, OverflowError):
                raise DetectionProviderError("Faster R-CNN returned a non-numeric detection value.")
            if not math.isfinite(score) or score < confidence_threshold:
                continue
            if score > 1.0:
                raise DetectionProviderError("Faster R-CNN returned confidence outside the unit interval.")
            if class_id <= 0 or class_id >= len(labels_by_id):
                continue
            label = labels_by_id[class_id]
            if label in ("", "N/A", "__background__") or (allowed and label not in allowed):
                continue
            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
                raise DetectionProviderError("Faster R-CNN returned an invalid bounding box.")
            try:
                left, top, right, bottom = [float(value) for value in raw_box]
            except (TypeError, ValueError, OverflowError):
                raise DetectionProviderError("Faster R-CNN returned a non-numeric bounding box.")
            coordinates = (left, top, right, bottom)
            if not all(math.isfinite(value) for value in coordinates):
                raise DetectionProviderError("Faster R-CNN returned a non-finite bounding box.")
            candidate = _candidate_from_source_corners(
                label,
                score,
                class_id,
                left * scale_x,
                top * scale_y,
                right * scale_x,
                bottom * scale_y,
                source_width,
                source_height,
            )
            if candidate is not None:
                detections.append(candidate)
                if len(detections) >= max_detections:
                    break
        return tuple(detections)


class YoloWorldOnnxOpenCvProvider(DetectionProvider):
    """Run YOLO-World ONNX with export-time-fixed class prompts through OpenCV DNN."""

    provider_id = "yolo-world-onnx-opencv"

    def __init__(
        self,
        model_path: str,
        class_names: Sequence[str],
        input_size: Tuple[int, int] = (640, 640),
        confidence_threshold: float = 0.25,
        nms_iou_threshold: float = 0.45,
        max_detections: int = 100,
        output_format: str = "ultralytics",
        backend: str = "cpu",
        network_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """Bind explicit local ONNX and export-time label order without loading a text encoder."""

        if not isinstance(model_path, str) or not model_path:
            raise ValueError("YOLO-World ONNX requires a local model path.")
        labels = _validated_labels(class_names, allow_empty=False)
        if len(set(labels)) != len(labels):
            raise ValueError("YOLO-World class_names must not contain duplicates.")
        _require_input_size(input_size)
        _require_probability(confidence_threshold, "confidence_threshold")
        _require_probability(nms_iou_threshold, "nms_iou_threshold")
        _require_positive_integer(max_detections, "max_detections", 1, 1000)
        if output_format not in ("ultralytics", "end2end", "official-nms"):
            raise ValueError(
                "YOLO-World output_format must be ultralytics, end2end, or official-nms."
            )
        if backend not in ("cpu", "cuda", "cuda-fp16"):
            raise ValueError("YOLO-World OpenCV backend must be cpu, cuda, or cuda-fp16.")
        self.model_path = model_path
        self.class_names = labels
        self.input_size = input_size
        self.confidence_threshold = float(confidence_threshold)
        self.nms_iou_threshold = float(nms_iou_threshold)
        self.max_detections = max_detections
        self.output_format = output_format
        self.backend = backend
        self._network_factory = network_factory or cv2.dnn.readNetFromONNX
        self._network = None  # type: Optional[Any]

    def infer(self, frame: ImageFrame) -> Tuple[DetectionCandidate, ...]:
        """Run OpenCV DNN on one RGB8 or Mono8 in-memory frame without camera or network access."""

        source_image = numpy.asarray(_frame_to_rgb_image(frame), dtype=numpy.uint8)
        source_height, source_width = source_image.shape[:2]
        letterboxed = _letterbox_rgb(source_image, self.input_size)
        blob = cv2.dnn.blobFromImage(
            letterboxed,
            scalefactor=1.0 / 255.0,
            size=self.input_size,
            mean=(0.0, 0.0, 0.0),
            swapRB=False,
            crop=False,
        )
        network = self._load_network()
        try:
            network.setInput(blob)
            output_names = tuple(network.getUnconnectedOutLayersNames())
            outputs = network.forward(output_names) if output_names else network.forward()
            if self.output_format == "official-nms" and output_names:
                outputs = dict(zip(output_names, outputs))
        except Exception as error:
            raise DetectionProviderError("YOLO-World OpenCV inference failed.") from error
        return self.parse_output(
            outputs,
            self.class_names,
            source_width,
            source_height,
            self.input_size,
            self.confidence_threshold,
            self.nms_iou_threshold,
            self.max_detections,
            self.output_format,
        )

    def _load_network(self) -> Any:
        """Load local ONNX lazily and configure OpenCV DNN with the explicit backend."""

        if self._network is not None:
            return self._network
        model_file = Path(self.model_path)
        if not model_file.is_file():
            raise DetectionProviderError("The configured local YOLO-World ONNX file is unavailable.")
        try:
            network = self._network_factory(str(model_file))
            if self.backend == "cpu":
                network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            else:
                network.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
                target = (
                    cv2.dnn.DNN_TARGET_CUDA_FP16
                    if self.backend == "cuda-fp16"
                    else cv2.dnn.DNN_TARGET_CUDA
                )
                network.setPreferableTarget(target)
        except Exception as error:
            raise DetectionProviderError("The local YOLO-World ONNX model could not be loaded.") from error
        self._network = network
        return network

    @staticmethod
    def parse_output(
        outputs: Any,
        class_names: Sequence[str],
        source_width: int,
        source_height: int,
        input_size: Tuple[int, int] = (640, 640),
        confidence_threshold: float = 0.25,
        nms_iou_threshold: float = 0.45,
        max_detections: int = 100,
        output_format: str = "ultralytics",
    ) -> Tuple[DetectionCandidate, ...]:
        """Parse conventional Ultralytics or end-to-end ONNX output and reverse letterboxing."""

        labels = _validated_labels(class_names, allow_empty=False)
        _require_image_dimensions(source_width, source_height)
        _require_input_size(input_size)
        _require_probability(confidence_threshold, "confidence_threshold")
        _require_probability(nms_iou_threshold, "nms_iou_threshold")
        _require_positive_integer(max_detections, "max_detections", 1, 1000)
        if output_format not in ("ultralytics", "end2end", "official-nms"):
            raise DetectionProviderError("Unsupported YOLO-World ONNX output format.")
        if output_format == "official-nms":
            raw = _parse_official_nms_outputs(outputs, labels, confidence_threshold)
            selected = raw[:max_detections]
        else:
            matrix = _single_output_matrix(outputs)
            raw = (
                _parse_ultralytics_rows(matrix, labels, confidence_threshold)
                if output_format == "ultralytics"
                else _parse_end2end_rows(matrix, labels, confidence_threshold)
            )
            selected = _class_aware_nms(raw, nms_iou_threshold, max_detections)
        input_width, input_height = input_size
        scale = min(
            float(input_width) / float(source_width),
            float(input_height) / float(source_height),
        )
        resized_width = int(round(source_width * scale))
        resized_height = int(round(source_height * scale))
        pad_x = float((input_width - resized_width) // 2)
        pad_y = float((input_height - resized_height) // 2)
        detections = []  # type: List[DetectionCandidate]
        for item in selected:
            candidate = _candidate_from_source_corners(
                labels[item[5]],
                item[4],
                item[5],
                (item[0] - pad_x) / scale,
                (item[1] - pad_y) / scale,
                (item[2] - pad_x) / scale,
                (item[3] - pad_y) / scale,
                source_width,
                source_height,
            )
            if candidate is not None:
                detections.append(candidate)
        return tuple(detections)


def _frame_to_rgb_image(frame: ImageFrame) -> Image.Image:
    """Strictly validate normalized camera frames and duplicate mono channels through Pillow."""

    if not isinstance(frame, ImageFrame) or not frame.healthy or frame.pixel_payload is None:
        raise DetectionProviderError("A healthy camera frame with pixels is required for detection.")
    if frame.width is None or frame.height is None or frame.width <= 0 or frame.height <= 0:
        raise DetectionProviderError("Camera frame dimensions are required for detection.")
    pixel_count = frame.width * frame.height
    if frame.pixel_format == "rgb8":
        if len(frame.pixel_payload) != pixel_count * 3:
            raise DetectionProviderError("The RGB camera payload does not match its dimensions.")
        return Image.frombytes("RGB", (frame.width, frame.height), frame.pixel_payload)
    if frame.pixel_format == "mono8":
        if len(frame.pixel_payload) != pixel_count:
            raise DetectionProviderError("The monochrome camera payload does not match its dimensions.")
        return Image.frombytes("L", (frame.width, frame.height), frame.pixel_payload).convert("RGB")
    raise DetectionProviderError("The camera pixel format is unsupported for object detection.")


def _resize_image(image: Image.Image, maximum_side: int) -> Image.Image:
    """Downscale Torchvision input proportionally without enlarging a small image."""

    longest_side = max(image.size)
    if longest_side <= maximum_side:
        return image
    scale = float(maximum_side) / float(longest_side)
    target = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    resampling = getattr(Image, "Resampling", Image)
    return image.resize(target, resampling.BILINEAR)


def _letterbox_rgb(image: numpy.ndarray, input_size: Tuple[int, int]) -> numpy.ndarray:
    """Scale RGB input in the Ultralytics style and pad evenly with gray value 114."""

    input_width, input_height = input_size
    source_height, source_width = image.shape[:2]
    scale = min(
        float(input_width) / float(source_width),
        float(input_height) / float(source_height),
    )
    resized_width = max(1, int(round(source_width * scale)))
    resized_height = max(1, int(round(source_height * scale)))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    pad_width = input_width - resized_width
    pad_height = input_height - resized_height
    left = pad_width // 2
    right = pad_width - left
    top = pad_height // 2
    bottom = pad_height - top
    return cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )


def _single_output_matrix(outputs: Any) -> numpy.ndarray:
    """Accept one OpenCV output array or list item and reject ambiguous multi-head output."""

    if isinstance(outputs, (list, tuple)):
        if len(outputs) != 1:
            raise DetectionProviderError("YOLO-World ONNX must expose one detection output tensor.")
        outputs = outputs[0]
    matrix = numpy.asarray(outputs)
    if matrix.ndim == 3:
        if matrix.shape[0] != 1:
            raise DetectionProviderError("YOLO-World ONNX batch size must be one.")
        matrix = matrix[0]
    if matrix.ndim != 2:
        raise DetectionProviderError("YOLO-World ONNX returned an unsupported output rank.")
    return matrix.astype(numpy.float32, copy=False)


def _parse_ultralytics_rows(
    matrix: numpy.ndarray,
    class_names: Tuple[str, ...],
    confidence_threshold: float,
) -> List[Tuple[float, float, float, float, float, int]]:
    """Parse objectness-free `[1, 4+classes, anchors]` output or its transpose."""

    feature_count = 4 + len(class_names)
    if matrix.shape[0] == feature_count:
        rows = matrix.transpose()
    elif matrix.shape[1] == feature_count:
        rows = matrix
    else:
        raise DetectionProviderError(
            "YOLO-World output features do not match the configured class_names order."
        )
    detections = []  # type: List[Tuple[float, float, float, float, float, int]]
    for row in rows:
        if not numpy.all(numpy.isfinite(row)):
            raise DetectionProviderError("YOLO-World returned non-finite output values.")
        class_scores = row[4:]
        class_id = int(numpy.argmax(class_scores))
        confidence = float(class_scores[class_id])
        if confidence < confidence_threshold:
            continue
        if confidence > 1.0:
            raise DetectionProviderError("YOLO-World returned confidence outside the unit interval.")
        center_x, center_y, width, height = [float(value) for value in row[:4]]
        if width <= 0.0 or height <= 0.0:
            continue
        detections.append(
            (
                center_x - width / 2.0,
                center_y - height / 2.0,
                center_x + width / 2.0,
                center_y + height / 2.0,
                confidence,
                class_id,
            )
        )
    return detections


def _parse_end2end_rows(
    matrix: numpy.ndarray,
    class_names: Tuple[str, ...],
    confidence_threshold: float,
) -> List[Tuple[float, float, float, float, float, int]]:
    """Parse export-side postprocessed `[x1, y1, x2, y2, confidence, class_id]` output."""

    if matrix.shape[1] != 6:
        raise DetectionProviderError("YOLO-World end2end output must contain six values per detection.")
    detections = []  # type: List[Tuple[float, float, float, float, float, int]]
    for row in matrix:
        if not numpy.all(numpy.isfinite(row)):
            raise DetectionProviderError("YOLO-World returned non-finite output values.")
        confidence = float(row[4])
        if confidence < confidence_threshold:
            continue
        if confidence > 1.0:
            raise DetectionProviderError("YOLO-World returned confidence outside the unit interval.")
        class_value = float(row[5])
        class_id = int(round(class_value))
        if abs(class_value - class_id) > 1e-4 or not 0 <= class_id < len(class_names):
            raise DetectionProviderError("YOLO-World returned an invalid class index.")
        left, top, right, bottom = [float(value) for value in row[:4]]
        if right <= left or bottom <= top:
            continue
        detections.append((left, top, right, bottom, confidence, class_id))
    return detections


def _parse_official_nms_outputs(
    outputs: Any,
    class_names: Tuple[str, ...],
    confidence_threshold: float,
) -> List[Tuple[float, float, float, float, float, int]]:
    """Parse the official YOLO-World export's named NMS output tensors.

    The official deployment graph exposes ``num_dets``, ``boxes``, ``scores``,
    and ``labels``. Text prompts are already embedded when that graph is exported.
    """

    if not isinstance(outputs, Mapping):
        raise DetectionProviderError(
            "Official YOLO-World NMS output requires named OpenCV output tensors."
        )
    required = ("num_dets", "boxes", "scores", "labels")
    if any(name not in outputs for name in required):
        raise DetectionProviderError(
            "Official YOLO-World NMS output tensors are incomplete."
        )
    try:
        count_values = numpy.asarray(outputs["num_dets"]).reshape(-1)
        boxes = numpy.asarray(outputs["boxes"]).reshape(-1, 4)
        scores = numpy.asarray(outputs["scores"]).reshape(-1)
        labels = numpy.asarray(outputs["labels"]).reshape(-1)
    except (TypeError, ValueError) as error:
        raise DetectionProviderError(
            "Official YOLO-World NMS output tensors have invalid shapes."
        ) from error
    if count_values.size != 1:
        raise DetectionProviderError(
            "Official YOLO-World num_dets must contain one batch count."
        )
    try:
        count_value = float(count_values[0])
        count = int(count_value)
    except (TypeError, ValueError, OverflowError) as error:
        raise DetectionProviderError(
            "Official YOLO-World num_dets is invalid."
        ) from error
    if not math.isfinite(count_value) or abs(count_value - count) > 1e-4:
        raise DetectionProviderError(
            "Official YOLO-World num_dets must be an integer batch count."
        )
    if count < 0 or count > len(boxes) or count > len(scores) or count > len(labels):
        raise DetectionProviderError(
            "Official YOLO-World output lengths do not match num_dets."
        )
    detections = []  # type: List[Tuple[float, float, float, float, float, int]]
    for index in range(count):
        try:
            left, top, right, bottom = [float(item) for item in boxes[index]]
            score = float(scores[index])
            class_value = float(labels[index])
            class_id = int(class_value)
        except (TypeError, ValueError, OverflowError) as error:
            raise DetectionProviderError(
                "Official YOLO-World returned a non-numeric detection."
            ) from error
        values = (left, top, right, bottom, score)
        if not all(math.isfinite(item) for item in values):
            raise DetectionProviderError(
                "Official YOLO-World returned a non-finite detection."
            )
        if not math.isfinite(class_value) or abs(class_value - class_id) > 1e-4:
            raise DetectionProviderError(
                "Official YOLO-World returned a non-integer class index."
            )
        if score < confidence_threshold:
            continue
        if score > 1.0:
            raise DetectionProviderError(
                "Official YOLO-World returned confidence outside the unit interval."
            )
        if class_id < 0 or class_id >= len(class_names):
            raise DetectionProviderError(
                "Official YOLO-World returned an unknown class index."
            )
        if right <= left or bottom <= top:
            continue
        detections.append((left, top, right, bottom, score, class_id))
    return detections


def _class_aware_nms(
    detections: Sequence[Tuple[float, float, float, float, float, int]],
    iou_threshold: float,
    max_detections: int,
) -> List[Tuple[float, float, float, float, float, int]]:
    """Perform deterministic class-wise NMS so different prompts do not suppress each other."""

    grouped = {}  # type: Dict[int, List[Tuple[float, float, float, float, float, int]]]
    for detection in detections:
        grouped.setdefault(detection[5], []).append(detection)
    kept = []  # type: List[Tuple[float, float, float, float, float, int]]
    for class_id in sorted(grouped):
        remaining = sorted(grouped[class_id], key=lambda item: item[4], reverse=True)
        while remaining:
            selected = remaining.pop(0)
            kept.append(selected)
            remaining = [
                candidate
                for candidate in remaining
                if _intersection_over_union(selected, candidate) <= iou_threshold
            ]
    kept.sort(key=lambda item: item[4], reverse=True)
    return kept[:max_detections]


def _intersection_over_union(
    first: Tuple[float, float, float, float, float, int],
    second: Tuple[float, float, float, float, float, int],
) -> float:
    """Calculate intersection over union for two boxes in model-input coordinates."""

    intersection_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    intersection_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = intersection_width * intersection_height
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def _candidate_from_source_corners(
    label: str,
    confidence: float,
    class_id: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
    source_width: int,
    source_height: int,
) -> Optional[DetectionCandidate]:
    """Clamp a source-pixel box and create a strictly normalized candidate."""

    left = max(0.0, min(float(source_width), left))
    top = max(0.0, min(float(source_height), top))
    right = max(0.0, min(float(source_width), right))
    bottom = max(0.0, min(float(source_height), bottom))
    if right <= left or bottom <= top:
        return None
    return DetectionCandidate(
        label,
        confidence,
        BoundingBox2D(
            left / float(source_width),
            top / float(source_height),
            (right - left) / float(source_width),
            (bottom - top) / float(source_height),
        ),
        class_id,
    )


def _tensor_values(value: Any) -> Any:
    """Convert Torch tensors or test doubles into Python data."""

    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return value


def _validated_labels(labels: Sequence[str], allow_empty: bool) -> Tuple[str, ...]:
    """Reject blank labels while preserving stable ordering declared by config or export."""

    if isinstance(labels, str) or not isinstance(labels, Sequence):
        raise ValueError("Detection labels must be a sequence of strings.")
    normalized = tuple(labels)
    if not allow_empty and not normalized:
        raise ValueError("At least one detection label is required.")
    if not all(isinstance(label, str) and label.strip() for label in normalized):
        raise ValueError("Detection labels must be non-empty strings.")
    return normalized


def _require_probability(value: float, name: str) -> None:
    """Validate a strict finite probability threshold."""

    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError("{0} must be a finite value from 0.0 to 1.0.".format(name))


def _require_positive_integer(value: int, name: str, minimum: int, maximum: int) -> None:
    """Validate one bounded integer setting."""

    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("{0} must be an integer from {1} to {2}.".format(name, minimum, maximum))


def _require_image_dimensions(width: int, height: int) -> None:
    """Validate source-frame dimensions represented by model output."""

    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise DetectionProviderError("Detection source dimensions must be positive integers.")


def _require_input_size(input_size: Tuple[int, int]) -> None:
    """Validate the fixed width and height of an OpenCV blob."""

    if (
        not isinstance(input_size, tuple)
        or len(input_size) != 2
        or type(input_size[0]) is not int
        or type(input_size[1]) is not int
        or not 32 <= input_size[0] <= 4096
        or not 32 <= input_size[1] <= 4096
    ):
        raise ValueError("YOLO-World input_size must contain two integers from 32 to 4096.")
