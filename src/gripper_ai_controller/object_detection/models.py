"""Hardware-independent two-dimensional object detection contracts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame


class DetectionProviderError(RuntimeError):
    """Wrap model dependency, file, input, and output failures with a stable error."""


@dataclass(frozen=True)
class DetectionCandidate:
    """One model detection with coordinates normalized to the source camera frame."""

    label: str
    confidence: float
    bounding_box: BoundingBox2D
    class_id: Optional[int] = None

    def __post_init__(self) -> None:
        """Reject incomplete model output before it reaches a plugin or HTTP layer."""

        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Detection label must be a non-empty string.")
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(float(self.confidence))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ValueError("Detection confidence must be a finite value from 0.0 to 1.0.")
        if self.class_id is not None and (
            type(self.class_id) is not int or self.class_id < 0
        ):
            raise ValueError("Detection class_id must be a non-negative integer when present.")
        if not isinstance(self.bounding_box, BoundingBox2D):
            raise ValueError("Detection bounding_box must be a BoundingBox2D.")
        box = self.bounding_box
        values = (box.x, box.y, box.width, box.height)
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("Detection bounding boxes must contain finite numbers.")
        if box.x < 0.0 or box.y < 0.0 or box.width <= 0.0 or box.height <= 0.0:
            raise ValueError("Detection bounding boxes require a non-negative origin and positive size.")
        tolerance = 1e-9
        if box.x + box.width > 1.0 + tolerance or box.y + box.height > 1.0 + tolerance:
            raise ValueError("Detection bounding boxes must stay inside the normalized image plane.")


class DetectionProvider(ABC):
    """Synchronous model strategy invoked by an upper latest-frame worker."""

    provider_id = "object-detection-provider"

    @abstractmethod
    def infer(self, frame: ImageFrame) -> Tuple[DetectionCandidate, ...]:
        """Analyze one captured in-memory frame without opening a camera or persisting it."""


@dataclass(frozen=True)
class DetectionModelProfile:
    """A locally configured model profile without automatic downloads or runtime import paths."""

    model_id: str
    display_name: str
    provider_id: str
    model_path: str
    class_names: Tuple[str, ...] = ()
    allowed_labels: Tuple[str, ...] = ()
    device: str = "cpu"
    backend: str = "cpu"
    confidence_threshold: float = 0.5
    nms_iou_threshold: float = 0.45
    input_size: Tuple[int, int] = (640, 640)
    output_format: str = "ultralytics"
    inference_max_side: int = 960
    max_detections: int = 100

    def __post_init__(self) -> None:
        """Reject empty identifiers, out-of-range thresholds, and unstable label ordering."""

        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("Detection model_id must be a non-empty string.")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("Detection display_name must be a non-empty string.")
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("Detection provider_id must be a non-empty string.")
        if not isinstance(self.model_path, str) or not self.model_path.strip():
            raise ValueError("Detection model_path must be a non-empty string.")
        for field_name, labels in (("class_names", self.class_names), ("allowed_labels", self.allowed_labels)):
            if not isinstance(labels, tuple) or not all(
                isinstance(label, str) and label.strip() for label in labels
            ):
                raise ValueError("Detection {0} must be a tuple of non-empty strings.".format(field_name))
        if len(set(self.class_names)) != len(self.class_names):
            raise ValueError("Detection class_names must not contain duplicates.")
        if not 0.0 <= float(self.confidence_threshold) <= 1.0:
            raise ValueError("Detection confidence_threshold must be from 0.0 to 1.0.")
        if not 0.0 <= float(self.nms_iou_threshold) <= 1.0:
            raise ValueError("Detection nms_iou_threshold must be from 0.0 to 1.0.")
        if (
            not isinstance(self.input_size, tuple)
            or len(self.input_size) != 2
            or type(self.input_size[0]) is not int
            or type(self.input_size[1]) is not int
            or self.input_size[0] < 32
            or self.input_size[1] < 32
        ):
            raise ValueError("Detection input_size must contain two integers >= 32.")
        if self.output_format not in ("ultralytics", "end2end", "official-nms"):
            raise ValueError(
                "Detection output_format must be ultralytics, end2end, or official-nms."
            )
        if type(self.inference_max_side) is not int or not 32 <= self.inference_max_side <= 4096:
            raise ValueError("Detection inference_max_side must be from 32 to 4096.")
        if type(self.max_detections) is not int or not 1 <= self.max_detections <= 1000:
            raise ValueError("Detection max_detections must be from 1 to 1000.")


@dataclass(frozen=True)
class ObjectDetectionSettings:
    """Rate, model catalog, and safe-overlay constraints for generic object detection."""

    enabled: bool = False
    selected_model_id: Optional[str] = None
    models: Tuple[DetectionModelProfile, ...] = ()
    max_analysis_fps: int = 2
    overlay_max_frame_lag_seconds: float = 0.5

    def __post_init__(self) -> None:
        """Ensure the model catalog is deterministic and enabled settings select a trusted profile."""

        if type(self.enabled) is not bool:
            raise ValueError("Object detection enabled must be a boolean.")
        if not isinstance(self.models, tuple) or not all(
            isinstance(profile, DetectionModelProfile) for profile in self.models
        ):
            raise ValueError("Object detection models must be a tuple of profiles.")
        model_ids = [profile.model_id for profile in self.models]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("Object detection model IDs must be unique.")
        if self.selected_model_id is not None and self.selected_model_id not in model_ids:
            raise ValueError("Object detection selected_model_id must name a configured model.")
        if self.enabled and not self.models:
            raise ValueError("Enabled object detection requires at least one model profile.")
        if self.enabled and self.selected_model_id is None:
            raise ValueError("Enabled object detection requires selected_model_id.")
        if type(self.max_analysis_fps) is not int or not 1 <= self.max_analysis_fps <= 30:
            raise ValueError("Object detection max_analysis_fps must be from 1 to 30.")
        if (
            not isinstance(self.overlay_max_frame_lag_seconds, (int, float))
            or isinstance(self.overlay_max_frame_lag_seconds, bool)
            or not 0.01 <= float(self.overlay_max_frame_lag_seconds) <= 10.0
        ):
            raise ValueError(
                "Object detection overlay_max_frame_lag_seconds must be from 0.01 to 10.0."
            )
