"""In-memory contracts shared by the camera preview service and HTTP boundary."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EncodedFrame:
    """One browser-ready JPEG held only in the bounded in-memory frame hub."""

    sequence: int
    captured_at: float
    jpeg_payload: bytes


@dataclass(frozen=True)
class CameraPreviewError:
    """A safe, actionable camera preview error exposed to browser clients."""

    code: str
    message: str


@dataclass(frozen=True)
class CameraPreviewStatus:
    """The read-only state of one configured camera preview pipeline."""

    camera_id: str
    state: str
    latest_frame_at: Optional[float]
    frame_sequence: Optional[int]
    error: Optional[CameraPreviewError]
