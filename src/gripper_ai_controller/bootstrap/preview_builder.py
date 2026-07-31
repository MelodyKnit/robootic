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
    WorkpiecePoseSettings,
    validate_camera_parameter_overrides,
)
from gripper_ai_controller.domain.models import CameraParameterValue, RuntimeMode
from gripper_ai_controller.domain.ports import OperatorControllableGripper, VisionAdapter
from gripper_ai_controller.object_pose.models import (
    KnownWorkpieceProfile,
    NormalizedRect,
    ObjectPoseSettings,
)
from gripper_ai_controller.object_detection.models import (
    DetectionModelProfile,
    ObjectDetectionSettings,
)


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
    preview_plugin_enabled: Dict[str, bool] = field(default_factory=dict)
    pose_settings: PoseTrackingSettings = field(default_factory=PoseTrackingSettings)
    vision_analysis_settings: VisionAnalysisSettings = field(default_factory=VisionAnalysisSettings)
    workpiece_pose_settings: WorkpiecePoseSettings = field(default_factory=WorkpiecePoseSettings)
    object_detection_settings: ObjectDetectionSettings = field(
        default_factory=ObjectDetectionSettings
    )
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
    preview_plugin_enabled = _build_preview_plugin_enabled(
        optional_mapping(payload, "plugin_runtime"), preview_plugin_names
    )
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
        preview_plugin_enabled=preview_plugin_enabled,
        pose_settings=build_pose_tracking_settings(optional_mapping(payload, "pose")),
        vision_analysis_settings=_build_vision_analysis_settings(
            optional_mapping(payload, "vision_analysis")
        ),
        workpiece_pose_settings=_build_workpiece_pose_settings(
            optional_mapping(payload, "object_pose")
        ),
        object_detection_settings=_build_object_detection_settings(
            optional_mapping(payload, "object_detection")
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
        "plugin_lifecycle_controls_enabled",
        "recording_enabled",
        "recording_output_dir",
        "recording_default_fps",
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
    plugin_lifecycle_controls_enabled = _boolean_setting(
        settings, "plugin_lifecycle_controls_enabled", False
    )
    recording_enabled = _boolean_setting(settings, "recording_enabled", True)
    recording_output_dir = _string_setting(settings, "recording_output_dir", "localstore/recordings")
    recording_default_fps = _integer_setting(settings, "recording_default_fps", 30, 1, 60)
    if (gripper_controls_enabled or jaka_controls_enabled) and bind_host != "127.0.0.1":
        raise ValueError(
            "web.bind_host must be 127.0.0.1 when browser gripper or JAKA controls are enabled."
        )
    if plugin_reload_enabled and runtime_mode != RuntimeMode.DEVELOPMENT:
        raise ValueError("web.plugin_reload_enabled requires runtime_mode to be development.")
    if plugin_reload_enabled and bind_host != "127.0.0.1":
        raise ValueError("web.bind_host must be 127.0.0.1 when plugin reload is enabled.")
    if plugin_lifecycle_controls_enabled and bind_host != "127.0.0.1":
        raise ValueError(
            "web.bind_host must be 127.0.0.1 when plugin lifecycle controls are enabled."
        )
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
        plugin_lifecycle_controls_enabled=plugin_lifecycle_controls_enabled,
        recording_enabled=recording_enabled,
        recording_output_dir=recording_output_dir,
        recording_default_fps=recording_default_fps,
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


def _build_object_detection_settings(settings: Dict[str, Any]) -> ObjectDetectionSettings:
    """Validate semantic detection models without importing model runtimes or downloading weights."""

    allowed = {
        "enabled",
        "selected_model_id",
        "models",
        "max_analysis_fps",
        "overlay_max_frame_lag_seconds",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported object_detection settings: {0}.".format(", ".join(sorted(unknown)))
        )
    enabled = settings.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("object_detection.enabled must be a boolean.")
    selected_model_id = settings.get("selected_model_id")
    if selected_model_id is not None and (
        not isinstance(selected_model_id, str) or not selected_model_id.strip()
    ):
        raise ValueError(
            "object_detection.selected_model_id must be a non-empty string when present."
        )
    raw_models = settings.get("models", [])
    if not isinstance(raw_models, list) or len(raw_models) > 16:
        raise ValueError("object_detection.models must be an array with at most 16 entries.")
    models = tuple(_build_object_detection_model(item, index) for index, item in enumerate(raw_models))
    return ObjectDetectionSettings(
        enabled=enabled,
        selected_model_id=selected_model_id,
        models=models,
        max_analysis_fps=_integer_setting(settings, "max_analysis_fps", 2, 1, 30),
        overlay_max_frame_lag_seconds=_number_setting(
            settings, "overlay_max_frame_lag_seconds", 0.5, 0.01, 10.0
        ),
    )


def _build_object_detection_model(value: Any, index: int) -> DetectionModelProfile:
    """Build one fixed provider profile with localstore-only model assets."""

    field_prefix = "object_detection.models[{0}]".format(index)
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object.".format(field_prefix))
    required = {"model_id", "display_name", "provider", "model_path"}
    if set(value).intersection(required) != required:
        raise ValueError("{0} must contain model_id, display_name, provider, and model_path.".format(field_prefix))
    model_id = value.get("model_id")
    display_name = value.get("display_name")
    provider_id = value.get("provider")
    if not all(isinstance(item, str) and item.strip() for item in (model_id, display_name, provider_id)):
        raise ValueError("{0} identifiers must be non-empty strings.".format(field_prefix))
    model_path = _object_detection_localstore_path(value.get("model_path"), field_prefix)
    common_allowed = {
        "model_id",
        "display_name",
        "provider",
        "model_path",
        "confidence_threshold",
        "max_detections",
    }
    if provider_id == "torchvision-faster-rcnn-resnet50-fpn":
        allowed = common_allowed | {"device", "allowed_labels", "inference_max_side"}
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError("Unsupported {0} fields: {1}.".format(field_prefix, ", ".join(sorted(unknown))))
        allowed_labels = _object_detection_labels(value.get("allowed_labels", []), field_prefix, allow_empty=True)
        device = value.get("device", "cpu")
        if device not in ("cpu", "cuda"):
            raise ValueError("{0}.device must be cpu or cuda.".format(field_prefix))
        return DetectionModelProfile(
            model_id=model_id,
            display_name=display_name,
            provider_id=provider_id,
            model_path=model_path,
            allowed_labels=allowed_labels,
            device=device,
            confidence_threshold=_number_setting(value, "confidence_threshold", 0.5, 0.0, 1.0),
            max_detections=_integer_setting(value, "max_detections", 100, 1, 1000),
            inference_max_side=_integer_setting(value, "inference_max_side", 960, 32, 4096),
        )
    if provider_id == "yolo-world-onnx-opencv":
        allowed = common_allowed | {
            "class_names",
            "input_size",
            "nms_iou_threshold",
            "output_format",
            "backend",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError("Unsupported {0} fields: {1}.".format(field_prefix, ", ".join(sorted(unknown))))
        class_names = _object_detection_labels(value.get("class_names", []), field_prefix, allow_empty=False)
        input_size = _object_detection_input_size(value.get("input_size", [640, 640]), field_prefix)
        output_format = value.get("output_format", "ultralytics")
        if output_format not in ("ultralytics", "end2end", "official-nms"):
            raise ValueError(
                "{0}.output_format must be ultralytics, end2end, or official-nms.".format(
                    field_prefix
                )
            )
        backend = value.get("backend", "cpu")
        if backend not in ("cpu", "cuda", "cuda-fp16"):
            raise ValueError("{0}.backend must be cpu, cuda, or cuda-fp16.".format(field_prefix))
        return DetectionModelProfile(
            model_id=model_id,
            display_name=display_name,
            provider_id=provider_id,
            model_path=model_path,
            class_names=class_names,
            backend=backend,
            confidence_threshold=_number_setting(value, "confidence_threshold", 0.25, 0.0, 1.0),
            nms_iou_threshold=_number_setting(value, "nms_iou_threshold", 0.45, 0.0, 1.0),
            input_size=input_size,
            output_format=output_format,
            max_detections=_integer_setting(value, "max_detections", 100, 1, 1000),
        )
    raise ValueError("Unsupported object detection provider: {0}.".format(provider_id))


def _object_detection_localstore_path(value: Any, field_prefix: str) -> str:
    """Require a relative model asset path rooted in the ignored localstore directory."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("{0}.model_path must be a non-empty localstore-relative path.".format(field_prefix))
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
        raise ValueError("{0}.model_path must stay under localstore without parent traversal.".format(field_prefix))
    return value


def _object_detection_labels(value: Any, field_prefix: str, allow_empty: bool) -> Tuple[str, ...]:
    """Validate stable model class order and optional Faster R-CNN label filters."""

    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > 256:
        raise ValueError("{0} labels must be a non-empty array with at most 256 entries.".format(field_prefix))
    if any(not isinstance(label, str) or not label.strip() for label in value):
        raise ValueError("{0} labels must contain non-empty strings.".format(field_prefix))
    if len(set(value)) != len(value):
        raise ValueError("{0} labels must not contain duplicates.".format(field_prefix))
    return tuple(value)


def _object_detection_input_size(value: Any, field_prefix: str) -> Tuple[int, int]:
    """Validate fixed YOLO-World export dimensions."""

    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int or item < 32 or item > 4096 for item in value)
    ):
        raise ValueError("{0}.input_size must be [width, height] integers from 32 to 4096.".format(field_prefix))
    return int(value[0]), int(value[1])


def _build_workpiece_pose_settings(settings: Dict[str, Any]) -> WorkpiecePoseSettings:
    """Validate the local-only known-workpiece pose configuration.

    The detector receives only normalized geometry and explicitly named localstore
    files. It cannot infer a background image or calibration record from a camera,
    project path, or current device selection.
    """

    allowed = {
        "enabled",
        "background_reference_path",
        "workcell_calibration_path",
        "expected_calibration_id",
        "profile",
        "difference_threshold",
        "roi",
        "excluded_regions",
        "morphology_kernel_size",
        "opening_iterations",
        "closing_iterations",
        "maximum_foreground_ratio",
        "minimum_orientation_eccentricity",
        "maximum_contour_points",
        "max_analysis_fps",
        "overlay_max_frame_lag_seconds",
        "stable_required_frames",
        "maximum_center_jitter_px",
        "maximum_yaw_jitter_rad",
        "grasp_height_mm",
        "maximum_reprojection_error_px",
        "maximum_base_fit_rms_error_mm",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError("Unsupported object_pose settings: {0}.".format(", ".join(sorted(unknown))))
    enabled = _object_pose_boolean(settings, "enabled", False)
    background_reference_path = _object_pose_localstore_path(
        settings.get("background_reference_path"), "background_reference_path"
    )
    workcell_calibration_path = _object_pose_localstore_path(
        settings.get("workcell_calibration_path"), "workcell_calibration_path"
    )
    expected_calibration_id = _object_pose_optional_string(
        settings.get("expected_calibration_id"), "expected_calibration_id"
    )
    if enabled and (
        background_reference_path is None
        or workcell_calibration_path is None
        or expected_calibration_id is None
    ):
        raise ValueError(
            "object_pose.enabled requires background_reference_path, workcell_calibration_path, "
            "and expected_calibration_id."
        )
    profile = _build_known_workpiece_profile(optional_mapping(settings, "profile"))
    grasp_height_mm = _object_pose_number(settings, "grasp_height_mm", 0.0, 0.0, 500.0)
    if enabled and not profile.has_measured_operational_geometry:
        raise ValueError(
            "object_pose.enabled requires profile nominal_length_mm, nominal_width_mm, "
            "and object_thickness_mm measured at the workcell."
        )
    if (
        profile.object_thickness_mm is not None
        and grasp_height_mm > float(profile.object_thickness_mm)
    ):
        raise ValueError(
            "object_pose.grasp_height_mm must not exceed profile.object_thickness_mm."
        )
    detector_settings = ObjectPoseSettings(
        profile=profile,
        difference_threshold=_object_pose_integer(settings, "difference_threshold", 25, 1, 255),
        roi=_build_normalized_rect(settings.get("roi"), "object_pose.roi"),
        excluded_regions=_build_excluded_regions(settings.get("excluded_regions")),
        morphology_kernel_size=_object_pose_odd_integer(
            settings, "morphology_kernel_size", 3, 1, 31
        ),
        opening_iterations=_object_pose_integer(settings, "opening_iterations", 1, 0, 8),
        closing_iterations=_object_pose_integer(settings, "closing_iterations", 1, 0, 8),
        maximum_foreground_ratio=_object_pose_number(
            settings, "maximum_foreground_ratio", 0.45, 0.001, 1.0
        ),
        minimum_orientation_eccentricity=_object_pose_number(
            settings, "minimum_orientation_eccentricity", 0.15, 0.0, 1.0
        ),
        maximum_contour_points=_object_pose_integer(
            settings, "maximum_contour_points", 96, 3, 2048
        ),
    )
    return WorkpiecePoseSettings(
        enabled=enabled,
        background_reference_path=background_reference_path,
        workcell_calibration_path=workcell_calibration_path,
        expected_calibration_id=expected_calibration_id,
        detector_settings=detector_settings,
        max_analysis_fps=_object_pose_integer(settings, "max_analysis_fps", 2, 1, 30),
        overlay_max_frame_lag_seconds=_object_pose_number(
            settings, "overlay_max_frame_lag_seconds", 0.35, 0.01, 10.0
        ),
        stable_required_frames=_object_pose_integer(settings, "stable_required_frames", 3, 1, 12),
        maximum_center_jitter_px=_object_pose_number(
            settings, "maximum_center_jitter_px", 4.0, 0.1, 200.0
        ),
        maximum_yaw_jitter_rad=_object_pose_number(
            settings, "maximum_yaw_jitter_rad", 0.08, 0.001, math.pi
        ),
        grasp_height_mm=grasp_height_mm,
        maximum_reprojection_error_px=_object_pose_number(
            settings, "maximum_reprojection_error_px", 0.5, 0.01, 10.0
        ),
        maximum_base_fit_rms_error_mm=_object_pose_number(
            settings, "maximum_base_fit_rms_error_mm", 1.0, 0.01, 20.0
        ),
    )


def _build_known_workpiece_profile(settings: Dict[str, Any]) -> KnownWorkpieceProfile:
    """Validate only the static, inspectable shape contract for one known workpiece."""

    allowed = {
        "profile_id",
        "minimum_area_px",
        "maximum_area_px",
        "minimum_aspect_ratio",
        "maximum_aspect_ratio",
        "minimum_fill_ratio",
        "maximum_fill_ratio",
        "minimum_solidity",
        "require_directional_yaw",
        "directional_feature",
        "minimum_directional_asymmetry",
        "nominal_length_mm",
        "nominal_width_mm",
        "object_thickness_mm",
        "grasp_origin_offset_x_mm",
        "grasp_origin_offset_y_mm",
        "maximum_planar_dimension_error_ratio",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError("Unsupported object_pose.profile settings: {0}.".format(", ".join(sorted(unknown))))
    minimum_area_px = _object_pose_integer(settings, "minimum_area_px", 400, 1, 100000000)
    maximum_area_value = settings.get("maximum_area_px")
    if maximum_area_value is not None:
        if type(maximum_area_value) is not int or maximum_area_value < minimum_area_px:
            raise ValueError("object_pose.profile.maximum_area_px must be an integer no smaller than minimum_area_px.")
    minimum_aspect_ratio = _object_pose_number(
        settings, "minimum_aspect_ratio", 1.0, 1.0, 1000.0
    )
    maximum_aspect_ratio = settings.get("maximum_aspect_ratio")
    if maximum_aspect_ratio is not None:
        if (
            type(maximum_aspect_ratio) not in (int, float)
            or not math.isfinite(float(maximum_aspect_ratio))
            or float(maximum_aspect_ratio) < minimum_aspect_ratio
            or float(maximum_aspect_ratio) > 1000.0
        ):
            raise ValueError(
                "object_pose.profile.maximum_aspect_ratio must be a finite number no smaller than minimum_aspect_ratio."
            )
        maximum_aspect_ratio = float(maximum_aspect_ratio)
    minimum_fill_ratio = _object_pose_number(settings, "minimum_fill_ratio", 0.0, 0.0, 1.0)
    maximum_fill_ratio = _object_pose_number(settings, "maximum_fill_ratio", 1.0, 0.0, 1.0)
    if maximum_fill_ratio < minimum_fill_ratio:
        raise ValueError("object_pose.profile.maximum_fill_ratio must not be smaller than minimum_fill_ratio.")
    return KnownWorkpieceProfile(
        profile_id=_object_pose_required_string(settings, "profile_id", "known-workpiece-v1"),
        minimum_area_px=minimum_area_px,
        maximum_area_px=maximum_area_value,
        minimum_aspect_ratio=minimum_aspect_ratio,
        maximum_aspect_ratio=maximum_aspect_ratio,
        minimum_fill_ratio=minimum_fill_ratio,
        maximum_fill_ratio=maximum_fill_ratio,
        minimum_solidity=_object_pose_number(settings, "minimum_solidity", 0.0, 0.0, 1.0),
        require_directional_yaw=_object_pose_boolean(settings, "require_directional_yaw", False),
        directional_feature=_object_pose_directional_feature(settings),
        minimum_directional_asymmetry=_object_pose_number(
            settings, "minimum_directional_asymmetry", 0.15, 0.0, 1.0
        ),
        nominal_length_mm=_object_pose_optional_number(
            settings, "nominal_length_mm", 0.001, 10000.0
        ),
        nominal_width_mm=_object_pose_optional_number(
            settings, "nominal_width_mm", 0.001, 10000.0
        ),
        object_thickness_mm=_object_pose_optional_number(
            settings, "object_thickness_mm", 0.001, 1000.0
        ),
        grasp_origin_offset_x_mm=_object_pose_number(
            settings, "grasp_origin_offset_x_mm", 0.0, -1000.0, 1000.0
        ),
        grasp_origin_offset_y_mm=_object_pose_number(
            settings, "grasp_origin_offset_y_mm", 0.0, -1000.0, 1000.0
        ),
        maximum_planar_dimension_error_ratio=_object_pose_number(
            settings, "maximum_planar_dimension_error_ratio", 0.15, 0.001, 1.0
        ),
    )


def _object_pose_directional_feature(settings: Dict[str, Any]) -> str:
    """限制首期可审计的头尾特征规则，避免配置字符串静默降级。"""

    value = settings.get("directional_feature", "none")
    if value not in ("none", "larger_end"):
        raise ValueError(
            "object_pose.profile.directional_feature must be none or larger_end."
        )
    return value


def _object_pose_optional_number(
    settings: Dict[str, Any], field_name: str, minimum: float, maximum: float
) -> Optional[float]:
    """Accept an omitted/null physical measurement only while the feature remains disabled."""

    value = settings.get(field_name)
    if value is None:
        return None
    return _object_pose_number(settings, field_name, minimum, minimum, maximum)


def _build_normalized_rect(value: Any, field_name: str):
    """Build one optional normalized ROI/exclusion rectangle without coercing JSON values."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object when present.".format(field_name))
    if set(value).difference({"x", "y", "width", "height"}) or set(value) != {"x", "y", "width", "height"}:
        raise ValueError("{0} must contain exactly x, y, width, and height.".format(field_name))
    try:
        return NormalizedRect(value["x"], value["y"], value["width"], value["height"])
    except ValueError as error:
        raise ValueError("{0} is invalid: {1}".format(field_name, error))


def _build_excluded_regions(value: Any):
    """Parse a bounded tuple of static normalized fixture exclusions."""

    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("object_pose.excluded_regions must be an array with at most 32 entries.")
    return tuple(
        _build_normalized_rect(region, "object_pose.excluded_regions[{0}]".format(index))
        for index, region in enumerate(value)
    )


def _object_pose_localstore_path(value: Any, field_name: str) -> Optional[str]:
    """Restrict non-versioned calibration assets to caller-selected localstore paths."""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("object_pose.{0} must be a non-empty localstore-relative path.".format(field_name))
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
        raise ValueError(
            "object_pose.{0} must be a localstore-relative path without parent traversal.".format(field_name)
        )
    return value


def _object_pose_optional_string(value: Any, field_name: str) -> Optional[str]:
    """Normalize an optional non-empty configuration identifier."""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("object_pose.{0} must be a non-empty string when present.".format(field_name))
    return value


def _object_pose_required_string(settings: Dict[str, Any], key: str, default: str) -> str:
    """Read a bounded profile identifier without coercing scalar JSON values."""

    value = settings.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("object_pose.profile.{0} must be a non-empty string.".format(key))
    return value


def _object_pose_boolean(settings: Dict[str, Any], key: str, default: bool) -> bool:
    """Read one strict object-pose boolean."""

    value = settings.get(key, default)
    if type(value) is not bool:
        raise ValueError("object_pose.{0} must be a boolean.".format(key))
    return value


def _object_pose_integer(settings: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    """Read one bounded integer without accepting booleans or floats."""

    value = settings.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("object_pose.{0} must be an integer from {1} to {2}.".format(key, minimum, maximum))
    return value


def _object_pose_odd_integer(settings: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    """Read an odd kernel size accepted by the morphology implementation."""

    value = _object_pose_integer(settings, key, default, minimum, maximum)
    if value % 2 == 0:
        raise ValueError("object_pose.{0} must be odd.".format(key))
    return value


def _object_pose_number(settings: Dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    """Read one finite bounded float without coercing booleans."""

    value = settings.get(key, default)
    if (
        type(value) not in (int, float)
        or not math.isfinite(float(value))
        or float(value) < minimum
        or float(value) > maximum
    ):
        raise ValueError("object_pose.{0} must be a number from {1} to {2}.".format(key, minimum, maximum))
    return float(value)


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
            "plugin_lifecycle_controls_enabled": settings.plugin_lifecycle_controls_enabled,
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


def _build_preview_plugin_enabled(
    settings: Dict[str, Any], preview_plugin_names: Tuple[str, ...]
) -> Dict[str, bool]:
    """Read local plugin lifecycle preferences without changing the trusted registry list."""

    allowed = {"enabled"}
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported plugin_runtime settings: {0}.".format(
                ", ".join(sorted(unknown))
            )
        )
    configured = settings.get("enabled", {})
    if not isinstance(configured, dict):
        raise ValueError("plugin_runtime.enabled must be a JSON object.")
    unknown_plugin_ids = set(configured).difference(preview_plugin_names)
    if unknown_plugin_ids:
        raise ValueError(
            "plugin_runtime.enabled contains unconfigured plugins: {0}.".format(
                ", ".join(sorted(unknown_plugin_ids))
            )
        )
    enabled = {}  # type: Dict[str, bool]
    for plugin_id in preview_plugin_names:
        value = configured.get(plugin_id, True)
        if type(value) is not bool:
            raise ValueError(
                "plugin_runtime.enabled.{0} must be a boolean.".format(plugin_id)
            )
        enabled[plugin_id] = value
    return enabled


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
