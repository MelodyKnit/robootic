"""Configuration owned by the application rather than hardware adapters."""

from dataclasses import dataclass, replace
import math
from typing import Any, Dict, Mapping, Optional

from gripper_ai_controller.domain.models import CameraParameterValue


class CameraParameterConfigurationError(ValueError):
    """Report an invalid JSON camera-parameter override mapping."""


def validate_camera_parameter_overrides(value: Any) -> Dict[str, CameraParameterValue]:
    """Normalize the scalar camera parameters allowed in an explicit JSON configuration.

    Device-specific availability, ranges, enum options, and parameter dependencies are
    intentionally validated later by the connected adapter. This parser only protects
    the JSON contract from nested values, non-finite numbers, and empty parameter keys.
    """

    if not isinstance(value, Mapping):
        raise CameraParameterConfigurationError("camera_parameters must be a JSON object.")
    overrides = {}  # type: Dict[str, CameraParameterValue]
    for key, parameter_value in value.items():
        if not isinstance(key, str) or not key:
            raise CameraParameterConfigurationError("Every camera_parameters key must be a non-empty string.")
        if type(parameter_value) is bool:
            overrides[key] = parameter_value
        elif type(parameter_value) in (int, float):
            normalized_value = float(parameter_value)
            if not math.isfinite(normalized_value):
                raise CameraParameterConfigurationError(
                    "camera_parameters values must be finite JSON scalars."
                )
            overrides[key] = normalized_value
        elif isinstance(parameter_value, str):
            overrides[key] = parameter_value
        else:
            raise CameraParameterConfigurationError(
                "camera_parameters values must be string, number, or boolean scalars."
            )
    return overrides


@dataclass(frozen=True)
class SafetyLimits:
    """Documented PGI command limits used before any physical adapter is added."""

    minimum_force_percent: int = 20
    maximum_force_percent: int = 100
    maximum_position: int = 1000
    minimum_speed_percent: int = 1
    maximum_speed_percent: int = 100
    minimum_perception_confidence: float = 0.70
    maximum_perception_age_seconds: float = 2.0


@dataclass(frozen=True)
class WebPreviewSettings:
    """Validated settings for the browser camera preview and parameter-control server.

    The values originate in a versioned JSON configuration file or explicit CLI
    overrides. Camera parameter writes remain disabled unless a local configuration
    explicitly enables the fixed adapter whitelist. These settings never enable robot
    or gripper controls, and they do not define persistence paths.
    """

    bind_host: str = "0.0.0.0"
    port: int = 8000
    frontend_dist_dir: str = "src/web/dist"
    stream_fps: int = 10
    jpeg_quality: int = 80
    capture_retry_seconds: float = 1.0
    camera_controls_enabled: bool = False


@dataclass(frozen=True)
class PoseTrackingSettings:
    """Validated configuration for browser-only CUDA human-pose tracking.

    The model file is deliberately optional while tracking is disabled. Enabling the
    tracker without an explicit local file results in a visible invalid pose state; the
    implementation never downloads model weights or falls back to CPU implicitly.
    """

    enabled: bool = False
    model: str = "torchvision-keypoint-rcnn-resnet50-fpn"
    weights_path: Optional[str] = None
    device: str = "cuda"
    inference_max_side: int = 768
    max_inference_fps: int = 2
    torch_cpu_threads: int = 2
    torch_interop_threads: int = 1
    overlay_max_frame_lag_seconds: float = 0.35
    person_confidence_threshold: float = 0.80
    joint_confidence_threshold: float = 0.60
    lost_after_frames: int = 3
    target_joint: str = "right_wrist"
    tracking_min_iou: float = 0.20
    tracking_max_center_distance: float = 0.25
    motion_speed_threshold: float = 0.04
    motion_max_interval_seconds: float = 1.50
    draw_skeleton: bool = True

    def with_target_joint(self, target_joint: str) -> "PoseTrackingSettings":
        """Return an immutable copy with only the browser-selected target joint changed."""

        return replace(self, target_joint=target_joint)


@dataclass(frozen=True)
class VisionAnalysisSettings:
    """Thresholds for read-only frame diagnostics shown beside the camera preview.

    These values only classify the currently acquired image. They never change a
    camera parameter, suppress capture, or authorize a hardware command.
    """

    minimum_width: int = 320
    minimum_height: int = 240
    minimum_brightness: float = 20.0
    maximum_brightness: float = 235.0
    minimum_contrast: float = 8.0
    minimum_sharpness: float = 5.0
    sample_max_side: int = 640
    max_analysis_fps: int = 1
