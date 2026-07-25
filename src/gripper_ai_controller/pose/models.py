"""Stable 2D human-pose contracts independent of camera and inference SDKs."""

from dataclasses import dataclass
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import BoundingBox2D


COCO_KEYPOINT_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
"""COCO keypoint names emitted by the pinned Keypoint R-CNN model."""

COCO_SKELETON_EDGES = (
    ("left_ankle", "left_knee"),
    ("left_knee", "left_hip"),
    ("right_ankle", "right_knee"),
    ("right_knee", "right_hip"),
    ("left_hip", "right_hip"),
    ("left_shoulder", "left_hip"),
    ("right_shoulder", "right_hip"),
    ("left_shoulder", "right_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("left_eye", "right_eye"),
    ("nose", "left_eye"),
    ("nose", "right_eye"),
    ("left_eye", "left_ear"),
    ("right_eye", "right_ear"),
    ("left_ear", "left_shoulder"),
    ("right_ear", "right_shoulder"),
)
"""Named COCO skeleton edges for browser rendering without model-specific indices."""


@dataclass(frozen=True)
class PoseJoint2D:
    """One human joint in both source-image pixels and normalized image coordinates."""

    name: str
    x_px: float
    y_px: float
    normalized_x: float
    normalized_y: float
    confidence: float


@dataclass(frozen=True)
class PoseCandidate:
    """One untracked human candidate returned by a pose estimator for one frame."""

    bounding_box: BoundingBox2D
    confidence: float
    joints: Tuple[PoseJoint2D, ...]


@dataclass(frozen=True)
class HumanPose2D:
    """The single selected person pose attached to one camera frame."""

    camera_id: str
    captured_at: float
    bounding_box: BoundingBox2D
    confidence: float
    joints: Tuple[PoseJoint2D, ...]


@dataclass(frozen=True)
class PoseMotion2D:
    """One temporal movement observation for the currently locked image-space joint.

    Values use normalized image coordinates per second. They describe only the
    selected 2D pose history and must never be interpreted as real-world distance,
    velocity, or a robot motion target.
    """

    previous_captured_at: float
    captured_at: float
    target_joint: str
    delta_x: float
    delta_y: float
    displacement: float
    velocity_x: float
    velocity_y: float
    speed: float
    moving: bool


@dataclass(frozen=True)
class PoseTrackingSnapshot:
    """Latest browser-safe pose state, including the currently selected target joint."""

    camera_id: str
    captured_at: Optional[float]
    valid: bool
    reason: str
    target_joint: str
    target: Optional[PoseJoint2D]
    person: Optional[HumanPose2D]
    inference_latency_ms: Optional[float]
    lost_frames: int
    motion: Optional[PoseMotion2D] = None


@dataclass(frozen=True)
class PoseInferenceSnapshot:
    """Latest qualified person candidates retained independently from target-joint validity."""

    camera_id: str
    captured_at: Optional[float]
    candidates: Tuple[PoseCandidate, ...]
    reason: str
