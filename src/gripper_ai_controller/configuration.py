"""Configuration owned by the application rather than hardware adapters."""

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping

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
