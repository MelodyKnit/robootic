"""Build the isolated vision-preview graph from an explicit JSON configuration."""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from gripper_ai_controller.bootstrap.runtime_builder import (
    GRIPPER_ADAPTERS,
    ROBOT_ADAPTERS,
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
from gripper_ai_controller.adapters.jaka import JakaAdapter, JakaDryRunRobotAdapter
from gripper_ai_controller.configuration import (
    CameraSelectionSettings,
    GripperControlSettings,
    JakaControlSettings,
    PoseTrackingSettings,
    VisionAnalysisSettings,
    WebPreviewSettings,
    validate_camera_parameter_overrides,
)
from gripper_ai_controller.domain.models import CameraParameterValue, RuntimeMode
from gripper_ai_controller.domain.ports import OperatorControllableGripper, VisionAdapter


@dataclass
class VisionPreviewConfig:
    """The isolated dependencies required by the browser-facing operator service."""

    camera_id: str
    vision: VisionAdapter
    settings: WebPreviewSettings
    camera_parameter_overrides: Dict[str, CameraParameterValue] = field(default_factory=dict)
    config_file: Optional[str] = None
    vision_name: Optional[str] = None
    vision_adapter_settings: Dict[str, Any] = field(default_factory=dict)
    runtime_mode: RuntimeMode = RuntimeMode.PRODUCTION
    preview_plugin_names: Tuple[str, ...] = ("visual-pose-analysis",)
    pose_settings: PoseTrackingSettings = field(default_factory=PoseTrackingSettings)
    vision_analysis_settings: VisionAnalysisSettings = field(default_factory=VisionAnalysisSettings)
    camera_selection_settings: CameraSelectionSettings = field(
        default_factory=CameraSelectionSettings
    )
    gripper: Optional[OperatorControllableGripper] = None
    gripper_control_settings: Optional[GripperControlSettings] = None
    jaka: Optional[Union[JakaAdapter, JakaDryRunRobotAdapter]] = None
    jaka_control_settings: Optional[JakaControlSettings] = None


@dataclass(frozen=True)
class VisionEvaluationConfig:
    """Offline model-evaluation settings that deliberately do not construct a camera adapter."""

    pose_settings: PoseTrackingSettings
    vision_analysis_settings: VisionAnalysisSettings


def load_vision_preview_config(config_file: str) -> VisionPreviewConfig:
    """Create only the configured vision adapter without constructing execution targets.

    This intentionally bypasses ``Runtime`` construction and target startup. A web
    preview never constructs planners or mirror targets. When a ``gripper_control`` or
    ``jaka_control`` section is present, it constructs only the selected primary device;
    each manual facade controls its own safe connection lifecycle separately.
    """

    payload = load_json_config(config_file)
    camera = required_mapping(payload, "camera")
    components = required_mapping(payload, "components")
    runtime_mode = _optional_runtime_mode(payload)
    camera_id = required_string(camera, "camera_id")
    vision_name = required_string(components, "vision")
    vision_adapter_settings = optional_mapping(components, "vision_adapter_settings")
    camera_selection_settings = _build_camera_selection_settings(
        optional_mapping(payload, "camera_selection")
    )
    effective_vision_adapter_settings = dict(vision_adapter_settings)
    if (
        vision_name == "hikvision-camera"
        and camera_selection_settings.selected_device_id is not None
    ):
        effective_vision_adapter_settings["selected_device_id"] = (
            camera_selection_settings.selected_device_id
        )
    if vision_name == "hikvision-camera" and camera_selection_settings.calibration_ids:
        effective_vision_adapter_settings["device_calibration_ids"] = dict(
            camera_selection_settings.calibration_ids
        )
    preview_plugin_names = _build_preview_plugin_names(optional_mapping(components, "plugins"))
    camera_parameter_overrides = validate_camera_parameter_overrides(
        payload.get("camera_parameters", {})
    )
    web_settings = _build_web_settings(optional_mapping(payload, "web"), runtime_mode)
    if camera_selection_settings.enabled and web_settings.bind_host != "127.0.0.1":
        raise ValueError(
            "camera_selection.enabled requires web.bind_host to be 127.0.0.1."
        )
    gripper = None
    gripper_control_settings = None
    if "gripper_control" in payload:
        gripper_control_settings = _build_gripper_control_settings(
            required_mapping(payload, "gripper_control")
        )
        gripper = _build_operator_gripper(payload, gripper_control_settings.target_name)
    elif web_settings.gripper_controls_enabled:
        raise ValueError("gripper_control is required when browser gripper controls are enabled.")
    jaka = None
    jaka_control_settings = None
    if "jaka_control" in payload:
        jaka_control_settings = _build_jaka_control_settings(
            required_mapping(payload, "jaka_control")
        )
        jaka = _build_operator_jaka(payload, jaka_control_settings.target_name)
    elif web_settings.jaka_controls_enabled:
        raise ValueError("jaka_control is required when browser JAKA controls are enabled.")
    return VisionPreviewConfig(
        camera_id=camera_id,
        vision=create_component(
            VISION_ADAPTERS,
            vision_name,
            "vision adapter",
            effective_vision_adapter_settings,
        ),
        settings=web_settings,
        camera_parameter_overrides=camera_parameter_overrides,
        config_file=config_file,
        vision_name=vision_name,
        vision_adapter_settings=dict(vision_adapter_settings),
        runtime_mode=runtime_mode,
        preview_plugin_names=preview_plugin_names,
        pose_settings=build_pose_tracking_settings(optional_mapping(payload, "pose")),
        vision_analysis_settings=_build_vision_analysis_settings(
            optional_mapping(payload, "vision_analysis")
        ),
        camera_selection_settings=camera_selection_settings,
        gripper=gripper,
        gripper_control_settings=gripper_control_settings,
        jaka=jaka,
        jaka_control_settings=jaka_control_settings,
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


def _build_web_settings(settings, runtime_mode=RuntimeMode.PRODUCTION):
    """Validate bounded preview parameters before the HTTP server starts."""

    allowed = {
        "bind_host",
        "port",
        "frontend_dist_dir",
        "stream_fps",
        "jpeg_quality",
        "capture_retry_seconds",
        "camera_controls_enabled",
        "gripper_controls_enabled",
        "jaka_controls_enabled",
        "plugin_reload_enabled",
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
    gripper_controls_enabled = _boolean_setting(settings, "gripper_controls_enabled", False)
    jaka_controls_enabled = _boolean_setting(settings, "jaka_controls_enabled", False)
    plugin_reload_enabled = _boolean_setting(settings, "plugin_reload_enabled", False)
    if (gripper_controls_enabled or jaka_controls_enabled) and bind_host != "127.0.0.1":
        raise ValueError(
            "web.bind_host must be 127.0.0.1 when browser gripper or JAKA controls are enabled."
        )
    if plugin_reload_enabled and runtime_mode != RuntimeMode.DEVELOPMENT:
        raise ValueError("web.plugin_reload_enabled requires runtime_mode to be development.")
    if plugin_reload_enabled and bind_host != "127.0.0.1":
        raise ValueError("web.bind_host must be 127.0.0.1 when plugin reload is enabled.")
    return WebPreviewSettings(
        bind_host=bind_host,
        port=port,
        frontend_dist_dir=frontend_dist_dir,
        stream_fps=stream_fps,
        jpeg_quality=jpeg_quality,
        capture_retry_seconds=retry_seconds,
        camera_controls_enabled=camera_controls_enabled,
        gripper_controls_enabled=gripper_controls_enabled,
        jaka_controls_enabled=jaka_controls_enabled,
        plugin_reload_enabled=plugin_reload_enabled,
    )


def _build_camera_selection_settings(settings: Dict[str, Any]) -> CameraSelectionSettings:
    """Validate local camera-selection policy without accepting vendor serial numbers."""

    allowed = {"enabled", "selected_device_id", "calibration_ids"}
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported camera_selection settings: {0}.".format(
                ", ".join(sorted(unknown))
            )
        )
    enabled = settings.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("camera_selection.enabled must be a boolean.")
    selected_device_id = settings.get("selected_device_id")
    if selected_device_id is not None and (
        not isinstance(selected_device_id, str) or not selected_device_id.strip()
    ):
        raise ValueError(
            "camera_selection.selected_device_id must be a non-empty string when present."
        )
    calibration_ids = settings.get("calibration_ids", {})
    if not isinstance(calibration_ids, dict):
        raise ValueError("camera_selection.calibration_ids must be a JSON object.")
    normalized_calibrations = {}
    for device_id, calibration_id in calibration_ids.items():
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError(
                "Every camera_selection.calibration_ids key must be a non-empty string."
            )
        if not isinstance(calibration_id, str) or not calibration_id.strip():
            raise ValueError(
                "Every camera_selection.calibration_ids value must be a non-empty string."
            )
        normalized_calibrations[device_id] = calibration_id
    return CameraSelectionSettings(
        enabled=enabled,
        selected_device_id=selected_device_id,
        calibration_ids=normalized_calibrations,
    )


def _build_operator_gripper(payload: Dict[str, Any], target_name: str) -> OperatorControllableGripper:
    """Construct only the selected target's manual gripper without robot side effects."""

    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets must be a JSON array when gripper controls are enabled.")
    selected = [target for target in targets if isinstance(target, dict) and target.get("name") == target_name]
    if len(selected) != 1:
        raise ValueError("gripper_control.target_name must select exactly one configured target.")
    target = selected[0]
    if target.get("role") != "primary":
        raise ValueError("gripper_control.target_name must select a primary target.")
    adapter = create_component(
        GRIPPER_ADAPTERS,
        required_string(target, "gripper_adapter"),
        "operator-controllable gripper adapter",
        optional_mapping(target, "gripper_adapter_settings"),
    )
    if not isinstance(adapter, OperatorControllableGripper):
        raise ValueError("The selected gripper adapter does not support manual Web control.")
    return adapter


def _build_operator_jaka(
    payload: Dict[str, Any], target_name: str
) -> Union[JakaAdapter, JakaDryRunRobotAdapter]:
    """Construct only one selected primary JAKA target without a runtime graph."""

    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets must be a JSON array when JAKA controls are enabled.")
    selected = [target for target in targets if isinstance(target, dict) and target.get("name") == target_name]
    if len(selected) != 1:
        raise ValueError("jaka_control.target_name must select exactly one configured target.")
    target = selected[0]
    if target.get("role") != "primary":
        raise ValueError("jaka_control.target_name must select a primary target.")
    adapter_name = required_string(target, "robot_adapter")
    if adapter_name not in ("jaka-robot", "jaka-dry-run-robot"):
        raise ValueError("jaka_control.target_name must select a JAKA robot adapter.")
    adapter = create_component(
        ROBOT_ADAPTERS,
        adapter_name,
        "operator-controllable JAKA robot adapter",
        optional_mapping(target, "robot_adapter_settings"),
    )
    if not isinstance(adapter, (JakaAdapter, JakaDryRunRobotAdapter)):
        raise ValueError("The selected JAKA adapter does not support manual Web control.")
    return adapter


def _build_gripper_control_settings(settings: Dict[str, Any]) -> GripperControlSettings:
    """Validate local operator-control limits without accepting device endpoints."""

    allowed = {
        "target_name",
        "open_position",
        "close_position",
        "arm_timeout_seconds",
        "initialization_timeout_seconds",
        "minimum_position",
        "maximum_position",
        "minimum_force_percent",
        "maximum_force_percent",
        "minimum_speed_percent",
        "maximum_speed_percent",
        "idempotency_cache_size",
        "idempotency_ttl_seconds",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported gripper_control settings: {0}.".format(", ".join(sorted(unknown)))
        )
    target_name = required_string(settings, "target_name")
    minimum_position = _bounded_integer(settings, "minimum_position", 0, 0, 1000, "gripper_control")
    maximum_position = _bounded_integer(settings, "maximum_position", 1000, 0, 1000, "gripper_control")
    if minimum_position >= maximum_position:
        raise ValueError("gripper_control.minimum_position must be less than maximum_position.")
    open_position = _required_bounded_integer(
        settings, "open_position", minimum_position, maximum_position, "gripper_control"
    )
    close_position = _required_bounded_integer(
        settings, "close_position", minimum_position, maximum_position, "gripper_control"
    )
    if open_position == close_position:
        raise ValueError("gripper_control open_position and close_position must differ.")
    minimum_force = _bounded_integer(
        settings, "minimum_force_percent", 20, 20, 100, "gripper_control"
    )
    maximum_force = _bounded_integer(
        settings, "maximum_force_percent", 30, 20, 100, "gripper_control"
    )
    minimum_speed = _bounded_integer(
        settings, "minimum_speed_percent", 1, 1, 100, "gripper_control"
    )
    maximum_speed = _bounded_integer(
        settings, "maximum_speed_percent", 20, 1, 100, "gripper_control"
    )
    if minimum_force > maximum_force:
        raise ValueError("gripper_control force limits are inverted.")
    if minimum_speed > maximum_speed:
        raise ValueError("gripper_control speed limits are inverted.")
    return GripperControlSettings(
        target_name=target_name,
        open_position=open_position,
        close_position=close_position,
        arm_timeout_seconds=_bounded_number(
            settings, "arm_timeout_seconds", 60.0, 5.0, 600.0, "gripper_control"
        ),
        initialization_timeout_seconds=_bounded_number(
            settings, "initialization_timeout_seconds", 5.0, 1.0, 30.0, "gripper_control"
        ),
        minimum_position=minimum_position,
        maximum_position=maximum_position,
        minimum_force_percent=minimum_force,
        maximum_force_percent=maximum_force,
        minimum_speed_percent=minimum_speed,
        maximum_speed_percent=maximum_speed,
        idempotency_cache_size=_bounded_integer(
            settings, "idempotency_cache_size", 128, 1, 1024, "gripper_control"
        ),
        idempotency_ttl_seconds=_bounded_number(
            settings, "idempotency_ttl_seconds", 600.0, 1.0, 3600.0, "gripper_control"
        ),
    )


def _build_jaka_control_settings(settings: Dict[str, Any]) -> JakaControlSettings:
    """Validate browser session and preview limits for one selected JAKA target."""

    allowed = {
        "target_name",
        "arm_timeout_seconds",
        "preview_timeout_seconds",
        "source_position_tolerance_rad",
        "idempotency_cache_size",
        "idempotency_ttl_seconds",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported jaka_control settings: {0}.".format(
                ", ".join(sorted(unknown))
            )
        )
    return JakaControlSettings(
        target_name=required_string(settings, "target_name"),
        arm_timeout_seconds=_bounded_number(
            settings, "arm_timeout_seconds", 60.0, 5.0, 600.0, "jaka_control"
        ),
        preview_timeout_seconds=_bounded_number(
            settings, "preview_timeout_seconds", 10.0, 3.0, 60.0, "jaka_control"
        ),
        source_position_tolerance_rad=_bounded_number(
            settings,
            "source_position_tolerance_rad",
            0.01,
            0.0001,
            0.1,
            "jaka_control",
        ),
        idempotency_cache_size=_bounded_integer(
            settings, "idempotency_cache_size", 128, 1, 1024, "jaka_control"
        ),
        idempotency_ttl_seconds=_bounded_number(
            settings, "idempotency_ttl_seconds", 600.0, 1.0, 3600.0, "jaka_control"
        ),
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


def validate_web_settings(
    settings: WebPreviewSettings, runtime_mode: RuntimeMode = RuntimeMode.PRODUCTION
) -> WebPreviewSettings:
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
            "gripper_controls_enabled": settings.gripper_controls_enabled,
            "jaka_controls_enabled": settings.jaka_controls_enabled,
            "plugin_reload_enabled": settings.plugin_reload_enabled,
        },
        runtime_mode,
    )


def _optional_runtime_mode(payload: Dict[str, Any]) -> RuntimeMode:
    """Read an optional preview runtime mode without breaking existing local configs."""

    value = payload.get("runtime_mode", RuntimeMode.PRODUCTION.value)
    if not isinstance(value, str):
        raise ValueError("runtime_mode must be a string when present.")
    try:
        return RuntimeMode(value)
    except ValueError as error:
        raise ValueError("runtime_mode must be development or production.") from error


def _build_preview_plugin_names(plugins: Dict[str, Any]) -> Tuple[str, ...]:
    """Validate configured preview plugins while preserving the legacy visual pipeline."""

    value = plugins.get("preview")
    if value is None:
        return ("visual-pose-analysis",)
    if not isinstance(value, list) or not value:
        raise ValueError("components.plugins.preview must be a non-empty JSON array.")
    if any(not isinstance(name, str) or not name for name in value):
        raise ValueError("components.plugins.preview entries must be non-empty strings.")
    if len(set(value)) != len(value):
        raise ValueError("components.plugins.preview must not contain duplicate plugin names.")
    return tuple(value)


def _required_bounded_integer(settings, key, minimum, maximum, section):
    """Return one required integer inside an explicitly named JSON section."""

    if key not in settings:
        raise ValueError("{0}.{1} is required.".format(section, key))
    return _bounded_integer(settings, key, None, minimum, maximum, section)


def _bounded_integer(settings, key, default, minimum, maximum, section):
    """Validate a bounded integer without treating booleans as numeric values."""

    value = settings.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(
            "{0}.{1} must be an integer from {2} to {3}.".format(section, key, minimum, maximum)
        )
    return value


def _bounded_number(settings, key, default, minimum, maximum, section):
    """Validate one finite bounded number in a non-Web configuration section."""

    value = settings.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(
            "{0}.{1} must be a number from {2} to {3}.".format(section, key, minimum, maximum)
        )
    return float(value)


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
