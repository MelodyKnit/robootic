"""Generic two-dimensional object detection model boundary."""

from gripper_ai_controller.object_detection.models import (
    DetectionCandidate,
    DetectionModelProfile,
    DetectionProvider,
    DetectionProviderError,
    ObjectDetectionSettings,
)
from gripper_ai_controller.object_detection.providers import (
    COCO_INSTANCE_CATEGORY_NAMES,
    TorchvisionFasterRcnnProvider,
    YoloWorldOnnxOpenCvProvider,
)

__all__ = [
    "COCO_INSTANCE_CATEGORY_NAMES",
    "DetectionCandidate",
    "DetectionModelProfile",
    "DetectionProvider",
    "DetectionProviderError",
    "ObjectDetectionSettings",
    "TorchvisionFasterRcnnProvider",
    "YoloWorldOnnxOpenCvProvider",
]
