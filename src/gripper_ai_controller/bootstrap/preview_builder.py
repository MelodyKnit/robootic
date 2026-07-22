"""Build the isolated vision-preview graph from an explicit JSON configuration."""

from dataclasses import dataclass
from pathlib import Path

from gripper_ai_controller.bootstrap.runtime_builder import (
    VISION_ADAPTERS,
    create_component,
    load_json_config,
    optional_mapping,
    required_mapping,
    required_string,
)
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.domain.ports import VisionAdapter


@dataclass
class VisionPreviewConfig:
    """The isolated dependencies required by the browser-facing camera service."""

    camera_id: str
    vision: VisionAdapter
    settings: WebPreviewSettings


def load_vision_preview_config(config_file: str) -> VisionPreviewConfig:
    """Create only the configured vision adapter without constructing execution targets.

    This intentionally bypasses ``Runtime`` construction and target startup. A web
    preview can therefore acquire camera frames without connecting to JAKA, a gripper,
    or any future physical execution target declared in the same JSON file.
    """

    payload = load_json_config(config_file)
    camera = required_mapping(payload, "camera")
    components = required_mapping(payload, "components")
    return VisionPreviewConfig(
        camera_id=required_string(camera, "camera_id"),
        vision=create_component(
            VISION_ADAPTERS,
            required_string(components, "vision"),
            "vision adapter",
            optional_mapping(components, "vision_adapter_settings"),
        ),
        settings=_build_web_settings(optional_mapping(payload, "web")),
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
    if type(value) not in (int, float) or value < minimum or value > maximum:
        raise ValueError("web.{0} must be a number from {1} to {2}.".format(key, minimum, maximum))
    return float(value)


def _boolean_setting(settings, key, default):
    """Return one strict JSON boolean without coercing strings or integer flags."""

    value = settings.get(key, default)
    if type(value) is not bool:
        raise ValueError("web.{0} must be a boolean.".format(key))
    return value
