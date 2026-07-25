"""Build the isolated vision-preview graph from an explicit JSON configuration."""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from gripper_ai_controller.bootstrap.runtime_builder import (
    VISION_ADAPTERS,
    create_component,
    load_json_config,
    optional_mapping,
    required_mapping,
    required_string,
)
from gripper_ai_controller.bootstrap.pose_settings import (
    build_pose_tracking_settings as _build_hardware_neutral_pose_settings,
)
from gripper_ai_controller.configuration import (
    PoseTrackingSettings,
    VisionAnalysisSettings,
    WebPreviewSettings,
    validate_camera_parameter_overrides,
)
from gripper_ai_controller.domain.models import CameraParameterValue
from gripper_ai_controller.domain.ports import VisionAdapter


@dataclass
class VisionPreviewConfig:
    """The isolated dependencies required by the browser-facing camera service."""

    camera_id: str
    vision: VisionAdapter
    settings: WebPreviewSettings
    camera_parameter_overrides: Dict[str, CameraParameterValue] = field(default_factory=dict)
    config_file: Optional[str] = None
    vision_name: Optional[str] = None
    vision_adapter_settings: Dict[str, Any] = field(default_factory=dict)
    pose_settings: PoseTrackingSettings = field(default_factory=PoseTrackingSettings)
    vision_analysis_settings: VisionAnalysisSettings = field(default_factory=VisionAnalysisSettings)


@dataclass(frozen=True)
class VisionEvaluationConfig:
    """Offline model-evaluation settings that deliberately do not construct a camera adapter."""

    pose_settings: PoseTrackingSettings
    vision_analysis_settings: VisionAnalysisSettings


def load_vision_preview_config(config_file: str) -> VisionPreviewConfig:
    """Create only the configured vision adapter without constructing execution targets.

    This intentionally bypasses ``Runtime`` construction and target startup. A web
    preview can therefore acquire camera frames without connecting to JAKA, a gripper,
    or any future physical execution target declared in the same JSON file.
    """

    payload = load_json_config(config_file)
    camera = required_mapping(payload, "camera")
    components = required_mapping(payload, "components")
    camera_id = required_string(camera, "camera_id")
    vision_name = required_string(components, "vision")
    vision_adapter_settings = optional_mapping(components, "vision_adapter_settings")
    camera_parameter_overrides = validate_camera_parameter_overrides(
        payload.get("camera_parameters", {})
    )
    return VisionPreviewConfig(
        camera_id=camera_id,
        vision=create_component(
            VISION_ADAPTERS,
            vision_name,
            "vision adapter",
            vision_adapter_settings,
        ),
        settings=_build_web_settings(optional_mapping(payload, "web")),
        camera_parameter_overrides=camera_parameter_overrides,
        config_file=config_file,
        vision_name=vision_name,
        vision_adapter_settings=dict(vision_adapter_settings),
        pose_settings=build_pose_tracking_settings(optional_mapping(payload, "pose")),
        vision_analysis_settings=_build_vision_analysis_settings(
            optional_mapping(payload, "vision_analysis")
        ),
    )


def load_vision_evaluation_config(config_file: str) -> VisionEvaluationConfig:
    """Load only pose and diagnostic settings for static images without opening any device."""

    payload = load_json_config(config_file)
    return VisionEvaluationConfig(
        pose_settings=build_pose_tracking_settings(optional_mapping(payload, "pose")),
        vision_analysis_settings=_build_vision_analysis_settings(
            optional_mapping(payload, "vision_analysis")
        ),
    )


def _build_web_settings(settings):
    """Validate bounded preview parameters before the HTTP server starts."""

    allowed = {
        "bind_host",
        "port",
        "frontend_dist_dir",
        "stream_fps",
        "jpeg_quality",
        "capture_retry_seconds",
        "camera_controls_enabled",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError("Unsupported web settings: {0}.".format(", ".join(sorted(unknown))))
    bind_host = _string_setting(settings, "bind_host", "0.0.0.0")
    frontend_dist_dir = _string_setting(settings, "frontend_dist_dir", "src/web/dist")
    port = _integer_setting(settings, "port", 8000, 1, 65535)
    stream_fps = _integer_setting(settings, "stream_fps", 10, 1, 30)
    jpeg_quality = _integer_setting(settings, "jpeg_quality", 80, 1, 95)
    retry_seconds = _number_setting(settings, "capture_retry_seconds", 1.0, 0.1, 30.0)
    camera_controls_enabled = _boolean_setting(settings, "camera_controls_enabled", False)
    return WebPreviewSettings(
        bind_host=bind_host,
        port=port,
        frontend_dist_dir=frontend_dist_dir,
        stream_fps=stream_fps,
        jpeg_quality=jpeg_quality,
        capture_retry_seconds=retry_seconds,
        camera_controls_enabled=camera_controls_enabled,
    )


def build_pose_tracking_settings(settings):
    """Validate reusable pose-tracking settings without loading model dependencies.

    The offline image-centering simulator reuses the same hardware-neutral validator,
    so model selection, target-joint validation, CUDA-only requirements, and local
    weight-path rules stay identical to the browser preview path.
    """

    return _build_hardware_neutral_pose_settings(settings)


def _build_vision_analysis_settings(settings):
    """Validate passive image-quality thresholds without importing image or model libraries."""

    allowed = {
        "minimum_width",
        "minimum_height",
        "minimum_brightness",
        "maximum_brightness",
        "minimum_contrast",
        "minimum_sharpness",
        "sample_max_side",
        "max_analysis_fps",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported vision_analysis settings: {0}.".format(", ".join(sorted(unknown)))
        )
    minimum_brightness = _number_setting(settings, "minimum_brightness", 20.0, 0.0, 255.0)
    maximum_brightness = _number_setting(settings, "maximum_brightness", 235.0, 0.0, 255.0)
    if minimum_brightness >= maximum_brightness:
        raise ValueError("vision_analysis.minimum_brightness must be less than maximum_brightness.")
    return VisionAnalysisSettings(
        minimum_width=_integer_setting(settings, "minimum_width", 320, 1, 10000),
        minimum_height=_integer_setting(settings, "minimum_height", 240, 1, 10000),
        minimum_brightness=minimum_brightness,
        maximum_brightness=maximum_brightness,
        minimum_contrast=_number_setting(settings, "minimum_contrast", 8.0, 0.0, 255.0),
        minimum_sharpness=_number_setting(settings, "minimum_sharpness", 5.0, 0.0, 1000000.0),
        sample_max_side=_integer_setting(settings, "sample_max_side", 640, 32, 4096),
        max_analysis_fps=_integer_setting(settings, "max_analysis_fps", 1, 1, 30),
    )


def validate_web_settings(settings: WebPreviewSettings) -> WebPreviewSettings:
    """Revalidate settings after explicit CLI overrides without accepting unsafe paths."""

    return _build_web_settings(
        {
            "bind_host": settings.bind_host,
            "port": settings.port,
            "frontend_dist_dir": settings.frontend_dist_dir,
            "stream_fps": settings.stream_fps,
            "jpeg_quality": settings.jpeg_quality,
            "capture_retry_seconds": settings.capture_retry_seconds,
            "camera_controls_enabled": settings.camera_controls_enabled,
        }
    )


def _string_setting(settings, key, default):
    """Return one non-empty optional string setting without coercing JSON values."""

    value = settings.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError("web.{0} must be a non-empty string.".format(key))
    if key == "frontend_dist_dir":
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("web.frontend_dist_dir must be a relative directory without parent traversal.")
    return value


def _integer_setting(settings, key, default, minimum, maximum):
    """Return one bounded integer setting while rejecting booleans and floats."""

    value = settings.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("web.{0} must be an integer from {1} to {2}.".format(key, minimum, maximum))
    return value


def _number_setting(settings, key, default, minimum, maximum):
    """Return one bounded numeric setting without accepting booleans."""

    value = settings.get(key, default)
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < minimum
        or value > maximum
    ):
        raise ValueError("web.{0} must be a number from {1} to {2}.".format(key, minimum, maximum))
    return float(value)


def _boolean_setting(settings, key, default):
    """Return one strict JSON boolean without coercing strings or integer flags."""

    value = settings.get(key, default)
    if type(value) is not bool:
        raise ValueError("web.{0} must be a boolean.".format(key))
    return value
