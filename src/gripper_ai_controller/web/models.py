"""In-memory contracts shared by the camera preview service and HTTP boundary."""

from dataclasses import dataclass
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import CameraDeviceDescriptor
from gripper_ai_controller.object_detection.tracker import ObjectDetectionTrackingSnapshot
from gripper_ai_controller.object_pose.tracker import ObjectPoseTrackingSnapshot
from gripper_ai_controller.pose.models import PoseTrackingSnapshot


@dataclass(frozen=True)
class EncodedFrame:
    """One browser-ready JPEG held only in the bounded in-memory frame hub."""

    captured_at: float
    jpeg_payload: bytes


@dataclass(frozen=True)
class CameraPreviewError:
    """A safe, actionable camera preview error exposed to browser clients."""

    code: str
    message: str


@dataclass(frozen=True)
class CameraPreviewStatus:
    """The current state of one configured camera preview pipeline."""

    camera_id: str
    state: str
    latest_frame_at: Optional[float]
    error: Optional[CameraPreviewError]


@dataclass(frozen=True)
class CameraCatalogSnapshot:
    """One logical preview status plus its currently discoverable physical devices."""

    status: CameraPreviewStatus
    devices: Tuple[CameraDeviceDescriptor, ...]
    selected_device_id: Optional[str]
    selection_enabled: bool
    discovery_error: Optional[CameraPreviewError]


@dataclass(frozen=True)
class PosePreviewSnapshot:
    """Pose metadata paired with the browser JPEG timestamp used for overlay freshness."""

    snapshot: PoseTrackingSnapshot
    latest_frame_at: Optional[float]
    overlay_fresh: bool


@dataclass(frozen=True)
class ObjectPosePreviewSnapshot:
    """Object-pose cache paired with the JPEG timestamp used for overlay freshness."""

    snapshot: ObjectPoseTrackingSnapshot
    latest_frame_at: Optional[float]
    overlay_fresh: bool


@dataclass(frozen=True)
class ObjectDetectionPreviewSnapshot:
    """Semantic detection cache paired with the JPEG timestamp used for overlay freshness."""

    snapshot: ObjectDetectionTrackingSnapshot
    latest_frame_at: Optional[float]
    overlay_fresh: bool
