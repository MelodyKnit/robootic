"""In-memory contracts shared by the camera preview service and HTTP boundary."""

from dataclasses import dataclass
from typing import Optional

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
class PosePreviewSnapshot:
    """Pose metadata paired with the browser JPEG timestamp used for overlay freshness."""

    snapshot: PoseTrackingSnapshot
    latest_frame_at: Optional[float]
    overlay_fresh: bool
