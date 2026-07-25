"""Typed contracts for a hardware-free image-centering simulation."""

from dataclasses import dataclass
from typing import Optional, Tuple


SIMULATED_JOINT_NAMES = (
    "base_yaw",
    "shoulder_pitch",
    "elbow_pitch",
    "wrist_yaw",
    "wrist_pitch",
    "wrist_roll",
)
"""Stable labels for the six virtual axes used only by the image-plane model."""


@dataclass(frozen=True)
class ImageServoSimulationSettings:
    """Validated calibration-like constants for the deterministic virtual camera model.

    The Jacobian expresses normalized image-coordinate change per radian of each
    virtual joint. It is deliberately a versioned demonstration model, not a JAKA
    calibration, hand-eye transform, robot kinematic chain, or collision model.
    """

    fixture_id: str = "full-body-front"
    pixel_format: str = "mono8"
    desired_normalized_x: float = 0.5
    desired_normalized_y: float = 0.5
    center_deadband: float = 0.025
    maximum_pose_age_seconds: float = 1.0
    maximum_joint_step_rad: float = 0.08
    gain: float = 0.85
    damping: float = 0.10
    maximum_iterations: int = 20
    initial_joint_positions_rad: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    joint_lower_limits_rad: Tuple[float, ...] = (-2.8, -1.5, -2.0, -2.8, -2.0, -3.1)
    joint_upper_limits_rad: Tuple[float, ...] = (2.8, 1.5, 2.0, 2.8, 2.0, 3.1)
    image_jacobian: Tuple[Tuple[float, ...], ...] = (
        (-0.52, 0.0, 0.0, -0.10, 0.0, 0.0),
        (0.0, -0.42, -0.18, 0.0, -0.08, 0.0),
    )


@dataclass(frozen=True)
class ImageTargetObservation:
    """One valid target joint expressed in normalized image coordinates."""

    camera_id: str
    captured_at: float
    joint_name: str
    normalized_x: float
    normalized_y: float
    confidence: float


@dataclass(frozen=True)
class ImageCenteringPlan:
    """One pure, unapproved virtual-joint update derived from image-plane error."""

    target: ImageTargetObservation
    executable: bool
    centered: bool
    reason: str
    error_x: float
    error_y: float
    requested_joint_deltas_rad: Tuple[float, ...]
    applied_joint_deltas_rad: Tuple[float, ...]
    target_joint_positions_rad: Tuple[float, ...]
    limited_joint_names: Tuple[str, ...]


@dataclass(frozen=True)
class SimulationStep:
    """One in-memory image-plane update and its predicted target location."""

    step_index: int
    target_before: ImageTargetObservation
    target_after: ImageTargetObservation
    plan: ImageCenteringPlan
    joint_positions_rad: Tuple[float, ...]


@dataclass(frozen=True)
class ImageCenteringResult:
    """Terminal state of a finite offline simulation run."""

    fixture_id: str
    pixel_format: str
    target_joint: str
    initial_target: ImageTargetObservation
    final_target: ImageTargetObservation
    final_joint_positions_rad: Tuple[float, ...]
    centered: bool
    reason: str
    steps: Tuple[SimulationStep, ...]
    last_plan: Optional[ImageCenteringPlan]
