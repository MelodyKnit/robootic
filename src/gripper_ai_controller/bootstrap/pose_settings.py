"""Hardware-neutral validation for reusable CUDA pose-tracking settings."""

import math
from pathlib import Path
from typing import Any, Dict

from gripper_ai_controller.configuration import PoseTrackingSettings
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES


def build_pose_tracking_settings(settings: Dict[str, Any]) -> PoseTrackingSettings:
    """Validate pose settings without importing preview, runtime, or adapter modules."""

    allowed = {
        "enabled",
        "model",
        "weights_path",
        "device",
        "inference_max_side",
        "max_inference_fps",
        "torch_cpu_threads",
        "torch_interop_threads",
        "overlay_max_frame_lag_seconds",
        "person_confidence_threshold",
        "joint_confidence_threshold",
        "lost_after_frames",
        "target_joint",
        "tracking_min_iou",
        "tracking_max_center_distance",
        "motion_speed_threshold",
        "motion_max_interval_seconds",
        "draw_skeleton",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError("Unsupported pose settings: {0}.".format(", ".join(sorted(unknown))))
    enabled = _boolean(settings, "enabled", False)
    model = _string(settings, "model", "torchvision-keypoint-rcnn-resnet50-fpn")
    if model != "torchvision-keypoint-rcnn-resnet50-fpn":
        raise ValueError("pose.model must be torchvision-keypoint-rcnn-resnet50-fpn.")
    device = _string(settings, "device", "cuda")
    if device != "cuda":
        raise ValueError("pose.device must be cuda; CPU fallback is intentionally disabled.")
    weights_path = settings.get("weights_path")
    if weights_path is not None:
        if not isinstance(weights_path, str) or not weights_path:
            raise ValueError("pose.weights_path must be a non-empty relative path when present.")
        path = Path(weights_path)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
            raise ValueError("pose.weights_path must be a localstore-relative path without parent traversal.")
    target_joint = _string(settings, "target_joint", "right_wrist")
    if target_joint not in COCO_KEYPOINT_NAMES:
        raise ValueError("pose.target_joint must be a supported COCO keypoint name.")
    return PoseTrackingSettings(
        enabled=enabled,
        model=model,
        weights_path=weights_path,
        device=device,
        inference_max_side=_integer(settings, "inference_max_side", 768, 64, 4096),
        max_inference_fps=_integer(settings, "max_inference_fps", 2, 1, 30),
        torch_cpu_threads=_integer(settings, "torch_cpu_threads", 2, 1, 32),
        torch_interop_threads=_integer(settings, "torch_interop_threads", 1, 1, 32),
        overlay_max_frame_lag_seconds=_number(
            settings, "overlay_max_frame_lag_seconds", 0.35, 0.01, 10.0
        ),
        person_confidence_threshold=_number(settings, "person_confidence_threshold", 0.80, 0.0, 1.0),
        joint_confidence_threshold=_number(settings, "joint_confidence_threshold", 0.60, 0.0, 1.0),
        lost_after_frames=_integer(settings, "lost_after_frames", 3, 1, 120),
        target_joint=target_joint,
        tracking_min_iou=_number(settings, "tracking_min_iou", 0.20, 0.0, 1.0),
        tracking_max_center_distance=_number(
            settings, "tracking_max_center_distance", 0.25, 0.0, 1.0
        ),
        motion_speed_threshold=_number(settings, "motion_speed_threshold", 0.04, 0.0, 10.0),
        motion_max_interval_seconds=_number(
            settings, "motion_max_interval_seconds", 1.50, 0.05, 30.0
        ),
        draw_skeleton=_boolean(settings, "draw_skeleton", True),
    )


def _string(settings: Dict[str, Any], key: str, default: str) -> str:
    """Return one non-empty JSON string without coercion."""

    value = settings.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError("pose.{0} must be a non-empty string.".format(key))
    return value


def _integer(settings: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    """Return one bounded integer while rejecting booleans and float coercion."""

    value = settings.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("pose.{0} must be an integer from {1} to {2}.".format(key, minimum, maximum))
    return value


def _number(settings: Dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    """Return one bounded finite number while rejecting boolean flags."""

    value = settings.get(key, default)
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < minimum
        or value > maximum
    ):
        raise ValueError("pose.{0} must be a number from {1} to {2}.".format(key, minimum, maximum))
    return float(value)


def _boolean(settings: Dict[str, Any], key: str, default: bool) -> bool:
    """Return one strict JSON boolean without converting strings or integers."""

    value = settings.get(key, default)
    if type(value) is not bool:
        raise ValueError("pose.{0} must be a boolean.".format(key))
    return value
