"""Stable browser-safe contracts for passive camera and pose analysis."""

from dataclasses import dataclass
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import BoundingBox2D
from gripper_ai_controller.pose.models import HumanPose2D


@dataclass(frozen=True)
class FrameQualityDiagnostics:
    """Read-only measurements that describe whether one acquired image is usable."""

    captured_at: float
    valid: bool
    width: Optional[int]
    height: Optional[int]
    pixel_format: Optional[str]
    brightness_mean: Optional[float]
    contrast: Optional[float]
    sharpness: Optional[float]
    warnings: Tuple[str, ...]


@dataclass(frozen=True)
class PersonDetection2D:
    """One model-detected person expressed in normalized image coordinates."""

    bounding_box: BoundingBox2D
    confidence: float
    selected: bool


@dataclass(frozen=True)
class JointVisibility:
    """One COCO joint classification derived from model confidence and image bounds."""

    name: str
    state: str
    confidence: float
    normalized_x: float
    normalized_y: float


@dataclass(frozen=True)
class VisionAnalysisSnapshot:
    """The latest cached diagnostics, detections, and selected-person joint visibility."""

    camera_id: str
    frame_captured_at: Optional[float]
    inference_captured_at: Optional[float]
    pose_enabled: bool
    valid: bool
    reason: str
    frame: Optional[FrameQualityDiagnostics]
    persons: Tuple[PersonDetection2D, ...]
    selected_person: Optional[HumanPose2D]
    joint_visibility: Tuple[JointVisibility, ...]
    visible_joint_names: Tuple[str, ...]
