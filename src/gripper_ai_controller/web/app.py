"""FastAPI application factory for browser camera preview and controlled parameters."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, StrictStr, validator

from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.core.components import CameraBinding, CameraBindingRequirement
from gripper_ai_controller.core.plugin_host import (
    PluginFactoryDescriptor,
    PluginDisabledError,
    PluginHost,
    PluginHostError,
    PluginLifecycleInProgressError,
    PluginReloadFailedError,
    PluginReloadInProgressError,
    PluginReloadNotAllowedError,
    PluginStatus,
    UnknownPluginError,
)
from gripper_ai_controller.domain.models import (
    CameraDeviceDescriptor,
    CameraParameter,
    CameraParameterApplyMode,
    ComponentManifest,
    RuntimeMode,
)
from gripper_ai_controller.domain.ports import CameraParameterError
from gripper_ai_controller.pose.config_store import PoseTargetConfigStore
from gripper_ai_controller.pose.gpu import inspect_cuda_gpu
from gripper_ai_controller.pose.models import (
    HumanPose2D,
    PoseJoint2D,
    PoseMotion2D,
)
from gripper_ai_controller.pose.tracker import PoseTargetError
from gripper_ai_controller.object_detection.tracker import UnknownDetectionModelError
from gripper_ai_controller.services.safety import SafetyPolicy
from gripper_ai_controller.vision.models import VisionAnalysisSnapshot
from gripper_ai_controller.web.config_store import (
    CameraParameterConfigStore,
    CameraSelectionConfigStore,
    PluginLifecycleConfigStore,
)
from gripper_ai_controller.web.gripper_api import install_gripper_routes
from gripper_ai_controller.web.gripper_service import ManualGripperControlService
from gripper_ai_controller.web.jaka_api import install_jaka_routes
from gripper_ai_controller.web.jaka_service import ManualJakaControlService
from gripper_ai_controller.web.models import (
    CameraCatalogSnapshot,
    CameraPreviewStatus,
    ObjectDetectionPreviewSnapshot,
    ObjectPosePreviewSnapshot,
    PosePreviewSnapshot,
)
from gripper_ai_controller.web.calibration_api import (
    install_calibration_routes,
    set_calibration_service,
)
from gripper_ai_controller.web.calibration_service import CalibrationService
from gripper_ai_controller.plugins.auto_calibration import build_auto_calibration_plugin
from gripper_ai_controller.web.service import (
    CameraDeviceNotFoundError,
    CameraParameterCapabilityError,
    CameraParameterOperationError,
    CameraParameterPersistenceError,
    CameraParameterWriteDisabledError,
    CameraPreviewService,
    CameraSelectionCapabilityError,
    CameraSelectionConflictError,
    CameraSelectionDisabledError,
    CameraSelectionOperationError,
    CameraSelectionPersistenceError,
    DetectionModelUnavailableError,
    PluginLifecycleControlsDisabledError,
    PluginLifecyclePersistenceError,
    PoseTargetPersistenceError,
    PoseTrackingCapabilityError,
)


_PREVIEW_PLUGIN_FACTORIES = {
    "visual-pose-analysis": (
        "gripper_ai_controller.plugins.visual_pose_analysis",
        "build_visual_pose_analysis_plugin",
        ComponentManifest(
            "visual-pose-analysis",
            "0.1.0",
            "plugin",
            ("frame-observer", "pose-tracking", "vision-analysis"),
            "build_visual_pose_analysis_plugin",
        ),
        "visual-pose-analysis",
    ),
    "object-pose-analysis": (
        "gripper_ai_controller.plugins.object_pose_analysis",
        "build_object_pose_analysis_plugin",
        ComponentManifest(
            "object-pose-analysis",
            "0.1.0",
            "plugin",
            ("frame-observer", "object-pose", "plane-calibration"),
            "build_object_pose_analysis_plugin",
        ),
        "object-pose-analysis",
    ),
    "object-detection-analysis": (
        "gripper_ai_controller.plugins.object_detection_analysis",
        "build_object_detection_analysis_plugin",
        ComponentManifest(
            "object-detection-analysis",
            "0.1.0",
            "plugin",
            ("frame-observer", "object-detection", "bounding-boxes"),
            "build_object_detection_analysis_plugin",
        ),
        "object-detection-analysis",
    ),
}


class ApiErrorResponse(BaseModel):
    """Stable JSON error payload returned by the camera preview API."""

    code: str
    message: str


class CameraErrorResponse(BaseModel):
    """A camera preview fault safe for presentation to an unauthenticated browser."""

    code: str
    message: str


class CameraStatusResponse(BaseModel):
    """Public state of one configured camera preview pipeline."""

    camera_id: str
    state: str
    latest_frame_at: Optional[float]
    error: Optional[CameraErrorResponse]


class CameraDeviceResponse(BaseModel):
    """Browser-safe physical camera metadata without a vendor serial number."""

    device_id: str
    display_name: str
    model_name: str
    transport: str
    selected: bool
    calibrated: bool


class CameraListResponse(BaseModel):
    """One logical preview and its currently discoverable physical camera devices."""

    cameras: List[CameraStatusResponse]
    devices: List[CameraDeviceResponse]
    selected_device_id: Optional[str]
    selection_enabled: bool
    discovery_error: Optional[CameraErrorResponse]


class CameraSelectionRequest(BaseModel):
    """Select one freshly discovered opaque camera device identifier."""

    device_id: StrictStr = Field(..., min_length=1)

    @validator("device_id")
    def reject_blank_device_id(cls, value: str) -> str:
        """Reject whitespace-only identifiers through the standard 422 contract."""

        if not value.strip():
            raise ValueError("device_id must contain a non-whitespace character")
        return value


class CameraParameterResponse(BaseModel):
    """One normalized camera parameter safe to render in the browser."""

    key: str
    kind: str
    apply_mode: str
    value: Any
    minimum: Optional[float]
    maximum: Optional[float]
    step: Optional[float]
    unit: Optional[str]
    options: List[str]


class CameraParametersResponse(BaseModel):
    """The current fixed parameter whitelist for one configured camera resource."""

    camera_id: str
    write_enabled: bool
    parameters: List[CameraParameterResponse]


class CameraParameterUpdateResponse(CameraParametersResponse):
    """Parameter state returned after one validated browser update request."""

    restarted_acquisition: bool


class CameraParameterValueRequest(BaseModel):
    """One raw JSON scalar preserved for adapter-side type validation."""

    value: Any


class CameraParameterBatchRequest(BaseModel):
    """A batch of restart-required values staged by the browser before explicit save."""

    values: Dict[str, Any]


class PoseJointResponse(BaseModel):
    """One browser-renderable COCO keypoint with pixel and normalized coordinates."""

    name: str
    x_px: float
    y_px: float
    normalized_x: float
    normalized_y: float
    confidence: float


class PoseBoundingBoxResponse(BaseModel):
    """Normalized bounding box of the selected single person."""

    x: float
    y: float
    width: float
    height: float


class HumanPoseResponse(BaseModel):
    """One selected person pose returned by the preview-only tracking pipeline."""

    camera_id: str
    captured_at: float
    bounding_box: PoseBoundingBoxResponse
    confidence: float
    joints: List[PoseJointResponse]


class PoseMotionResponse(BaseModel):
    """Latest image-space target-joint movement derived from consecutive valid frames."""

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


class PoseTrackingResponse(BaseModel):
    """Latest pose tracking state returned independently from the MJPEG stream."""

    camera_id: str
    enabled: bool
    captured_at: Optional[float]
    valid: bool
    reason: str
    target_joint: str
    target: Optional[PoseJointResponse]
    person: Optional[HumanPoseResponse]
    inference_latency_ms: Optional[float]
    lost_frames: int
    motion: Optional[PoseMotionResponse]
    draw_skeleton: bool
    latest_frame_at: Optional[float]
    overlay_fresh: bool


class ObjectPoseImagePointResponse(BaseModel):
    """A normalized image point used by a known-workpiece overlay."""

    x: float
    y: float


class ObjectPosePixelPointResponse(BaseModel):
    """A source-image pixel coordinate retained for operator diagnostics only."""

    x: float
    y: float


class ObjectPoseBoundingBoxResponse(BaseModel):
    """A normalized known-workpiece bounding box."""

    x: float
    y: float
    width: float
    height: float


class ObjectTranslationResponse(BaseModel):
    """A calibrated JAKA-base position in millimetres with no command semantics."""

    x: float
    y: float
    z: float


class ObjectOrientationRpyResponse(BaseModel):
    """Fixed-plane derived RPY values in radians."""

    roll: float
    pitch: float
    yaw: float


class ObjectPoseResponse(BaseModel):
    """One safe known-workpiece output; no grasp target is part of this schema."""

    profile_id: str
    confidence: float
    bounding_box: ObjectPoseBoundingBoxResponse
    contour: List[ObjectPoseImagePointResponse]
    pixel_center: Optional[ObjectPosePixelPointResponse]
    normalized_center: ObjectPoseImagePointResponse
    coordinate_frame: str
    translation_mm: Optional[ObjectTranslationResponse]
    orientation_rpy_rad: Optional[ObjectOrientationRpyResponse]
    observed_dof: List[str]
    derived_dof: List[str]
    yaw_period_rad: Optional[float]
    orientation_defined: bool
    warning: Optional[str]


class ObjectPoseTrackingResponse(BaseModel):
    """Cached passive known-workpiece analysis for one configured camera."""

    camera_id: str
    enabled: bool
    captured_at: Optional[float]
    latest_frame_at: Optional[float]
    overlay_fresh: bool
    valid: bool
    reason: str
    inference_latency_ms: Optional[float]
    objects: List[ObjectPoseResponse]


class ObjectDetectionModelResponse(BaseModel):
    """One configured model without its local file path or provider internals."""

    model_id: str
    display_name: str
    provider: str
    available: bool
    selected: bool


class ObjectDetectionResponse(BaseModel):
    """One normalized semantic bounding box safe for Canvas rendering."""

    detection_id: str
    label: str
    class_id: Optional[int]
    confidence: float
    bounding_box: ObjectPoseBoundingBoxResponse


class ObjectDetectionTrackingResponse(BaseModel):
    """Cached passive semantic detections for one configured camera."""

    camera_id: str
    enabled: bool
    selected_model_id: Optional[str]
    models: List[ObjectDetectionModelResponse]
    captured_at: Optional[float]
    latest_frame_at: Optional[float]
    overlay_fresh: bool
    valid: bool
    reason: str
    inference_latency_ms: Optional[float]
    detections: List[ObjectDetectionResponse]


class ObjectDetectionModelSelectionRequest(BaseModel):
    """Select one model identifier already declared by local configuration."""

    model_id: StrictStr = Field(..., min_length=1)

    @validator("model_id")
    def reject_blank_model_id(cls, value: str) -> str:
        """Reject whitespace-only IDs through the normal 422 response contract."""

        if not value.strip():
            raise ValueError("model_id must contain a non-whitespace character")
        return value


class PoseTargetRequest(BaseModel):
    """One browser-selected COCO joint persisted only to the explicit local configuration."""

    target_joint: str


class FrameQualityResponse(BaseModel):
    """Passive quality measurements for the latest camera image payload."""

    captured_at: float
    valid: bool
    width: Optional[int]
    height: Optional[int]
    pixel_format: Optional[str]
    brightness_mean: Optional[float]
    contrast: Optional[float]
    sharpness: Optional[float]
    warnings: List[str]


class PersonDetectionResponse(BaseModel):
    """One model-recognized person box without exposing model tensors or camera pixels."""

    bounding_box: PoseBoundingBoxResponse
    confidence: float
    selected: bool


class JointVisibilityResponse(BaseModel):
    """One named COCO joint's image-space visibility classification."""

    name: str
    state: str
    confidence: float
    normalized_x: float
    normalized_y: float


class VisionAnalysisResponse(BaseModel):
    """Cached camera quality and pose-derived person analysis for one configured camera."""

    camera_id: str
    frame_captured_at: Optional[float]
    inference_captured_at: Optional[float]
    pose_enabled: bool
    valid: bool
    reason: str
    frame: Optional[FrameQualityResponse]
    person_count: int
    persons: List[PersonDetectionResponse]
    selected_person: Optional[HumanPoseResponse]
    joint_visibility: List[JointVisibilityResponse]
    visible_joint_names: List[str]


class PluginCameraBindingResponse(BaseModel):
    """Read-only logical camera-source binding for one passive preview plugin."""

    mode: str
    camera_ids: List[str]
    minimum_sources: int
    maximum_sources: Optional[int]
    state: str


class PluginStatusResponse(BaseModel):
    """Browser-safe lifecycle metadata for one configured preview plugin."""

    plugin_id: str
    name: str
    version: str
    capabilities: List[str]
    ui_kind: str
    state: str
    error: Optional[str]
    reloadable: bool
    enabled: bool
    lifecycle_controllable: bool
    camera_binding: Optional[PluginCameraBindingResponse]


class PluginListResponse(BaseModel):
    """Stable resource collection used by the dynamic browser plugin workspace."""

    plugins: List[PluginStatusResponse]


class PluginReloadRequest(BaseModel):
    """Select trusted configured plugin IDs; an empty list requests all configured plugins."""

    plugin_ids: List[StrictStr] = Field(default_factory=list)


class PluginActivationRequest(BaseModel):
    """Replace one configured passive plugin's desired enabled state."""

    enabled: StrictBool


def create_web_app(
    preview_config: VisionPreviewConfig,
    frontend_dist_dir: Optional[str] = None,
    preview_service: Optional[CameraPreviewService] = None,
    gripper_control_service: Optional[ManualGripperControlService] = None,
    jaka_control_service: Optional[ManualJakaControlService] = None,
    calibration_service: Optional[CalibrationService] = None,
) -> FastAPI:
    """Create one FastAPI app for preview plus optionally gated manual device control."""

    if preview_config.settings.plugin_reload_enabled and preview_config.runtime_mode != RuntimeMode.DEVELOPMENT:
        raise ValueError("Browser plugin reload requires a development preview configuration.")
    if (
        preview_config.settings.camera_controls_enabled
        or preview_config.settings.gripper_controls_enabled
        or preview_config.settings.jaka_controls_enabled
        or preview_config.settings.plugin_reload_enabled
        or preview_config.settings.plugin_lifecycle_controls_enabled
        or preview_config.camera_selection_settings.enabled
    ) and preview_config.settings.bind_host != "127.0.0.1":
        raise ValueError(
            "Browser camera parameters and controls, camera selection, plugin reload, and plugin lifecycle changes require a 127.0.0.1 preview configuration."
        )
    if preview_config.pose_settings.enabled:
        preflight = inspect_cuda_gpu()
        if not preflight.ready_for_pose_inference:
            raise RuntimeError(
                "CUDA pose inference is not ready: {0} Run 'gripper-ai-controller gpu-check --require-torch'.".format(
                    preflight.reason
                )
            )
    if preview_service is None:
        parameter_store = None
        if preview_config.config_file is not None and preview_config.vision_name is not None:
            parameter_store = CameraParameterConfigStore(
                preview_config.config_file,
                preview_config.camera_id,
                preview_config.vision_name,
                preview_config.vision_adapter_settings,
            )
        camera_selection_store = None
        if preview_config.camera_selection_settings.enabled:
            if preview_config.config_file is None or preview_config.vision_name is None:
                raise ValueError(
                    "Camera selection requires an explicit local JSON configuration file."
                )
            camera_selection_store = CameraSelectionConfigStore(
                preview_config.config_file,
                preview_config.camera_id,
                preview_config.vision_name,
                preview_config.vision_adapter_settings,
            )
        plugin_lifecycle_store = None
        if preview_config.settings.plugin_lifecycle_controls_enabled:
            if preview_config.config_file is None or preview_config.vision_name is None:
                raise ValueError(
                    "Plugin lifecycle controls require an explicit local JSON configuration file."
                )
            plugin_lifecycle_store = PluginLifecycleConfigStore(
                preview_config.config_file,
                preview_config.camera_id,
                preview_config.vision_name,
                preview_config.preview_plugin_names,
                preview_config.vision_adapter_settings,
            )
        pose_target_store = None
        if preview_config.pose_settings.enabled:
            if preview_config.config_file is not None and preview_config.vision_name is not None:
                pose_target_store = PoseTargetConfigStore(
                    preview_config.config_file,
                    preview_config.camera_id,
                    preview_config.vision_name,
                    preview_config.vision_adapter_settings,
                )
        plugin_host = _build_preview_plugin_host(preview_config)
        service = CameraPreviewService(
            camera_id=preview_config.camera_id,
            vision=preview_config.vision,
            settings=preview_config.settings,
            camera_parameter_overrides=preview_config.camera_parameter_overrides,
            parameter_store=parameter_store,
            pose_target_store=pose_target_store,
            plugin_host=plugin_host,
            plugin_lifecycle_store=plugin_lifecycle_store,
            camera_selection_settings=preview_config.camera_selection_settings,
            camera_selection_store=camera_selection_store,
        )
    else:
        service = preview_service

    if gripper_control_service is None:
        if preview_config.gripper is not None and preview_config.gripper_control_settings is not None:
            control_settings = preview_config.gripper_control_settings
            gripper_control_service = ManualGripperControlService(
                preview_config.gripper,
                SafetyPolicy(
                    SafetyLimits(
                        minimum_force_percent=control_settings.minimum_force_percent,
                        maximum_force_percent=control_settings.maximum_force_percent,
                        maximum_position=control_settings.maximum_position,
                        minimum_speed_percent=control_settings.minimum_speed_percent,
                        maximum_speed_percent=control_settings.maximum_speed_percent,
                    )
                ),
                control_settings,
                controls_enabled=preview_config.settings.gripper_controls_enabled,
            )

    if jaka_control_service is None:
        if preview_config.jaka is not None and preview_config.jaka_control_settings is not None:
            jaka_control_service = ManualJakaControlService(
                preview_config.jaka,
                SafetyPolicy(SafetyLimits()),
                preview_config.jaka_control_settings,
                controls_enabled=preview_config.settings.jaka_controls_enabled,
            )

    if calibration_service is None:
        if preview_config.jaka is not None:
            # 构建自动标定插件（仅在有机器人适配器时）
            calibration_plugin = build_auto_calibration_plugin(
                robot_adapter=preview_config.jaka,
                vision_adapter=preview_config.vision,
                config=None,  # 使用默认配置
            )
            calibration_service = CalibrationService(calibration_plugin)

    if (
        bool(getattr(gripper_control_service, "controls_enabled", False))
        or bool(getattr(jaka_control_service, "controls_enabled", False))
    ) and preview_config.settings.bind_host != "127.0.0.1":
        raise ValueError(
            "An injected browser gripper or JAKA control service requires a 127.0.0.1 preview configuration."
        )

    @asynccontextmanager
    async def lifespan(application):
        """Start preview and optional device facades without autonomous execution."""

        await service.startup()
        if gripper_control_service is not None:
            await gripper_control_service.startup()
        if jaka_control_service is not None:
            await jaka_control_service.startup()
        if calibration_service is not None:
            await calibration_service.plugin.startup()
        try:
            yield
        finally:
            if calibration_service is not None:
                await calibration_service.plugin.shutdown()
            if jaka_control_service is not None:
                await jaka_control_service.shutdown()
            if gripper_control_service is not None:
                await gripper_control_service.shutdown()
            await service.shutdown()

    application = FastAPI(
        title="Gripper AI Camera Preview",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.camera_preview_service = service
    application.state.preview_plugin_host = service.plugin_host
    application.state.manual_gripper_control_service = gripper_control_service
    application.state.manual_jaka_control_service = jaka_control_service
    application.state.calibration_service = calibration_service

    @application.exception_handler(RequestValidationError)
    async def invalid_request_error(request, error):
        """Preserve the API error shape when FastAPI rejects malformed JSON input."""

        del request, error
        return _error_response(422, "invalid_request", "The request body is invalid.")

    @application.get("/api/cameras", response_model=CameraListResponse)
    async def list_cameras() -> CameraListResponse:
        """Return the logical preview plus a fresh physical-device discovery snapshot."""

        return _camera_catalog_response(await service.get_camera_catalog())

    @application.put(
        "/api/cameras/{camera_id}/selection",
        response_model=CameraListResponse,
    )
    async def select_camera(camera_id: str, request: CameraSelectionRequest):
        """Select one discovered device without changing the logical preview resource."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        try:
            catalog = await service.select_camera_device(request.device_id)
        except CameraSelectionDisabledError:
            return _error_response(
                403,
                "camera_selection_disabled",
                "Camera selection is disabled by the local preview configuration.",
            )
        except CameraSelectionCapabilityError:
            return _error_response(
                409,
                "camera_selection_unavailable",
                "The configured camera does not support physical-device selection.",
            )
        except CameraDeviceNotFoundError:
            return _error_response(
                404,
                "camera_device_not_found",
                "The requested camera device is no longer available.",
            )
        except CameraSelectionConflictError:
            return _error_response(
                409,
                "camera_selection_in_progress",
                "Another camera selection is already in progress.",
            )
        except CameraSelectionPersistenceError:
            return _error_response(
                503,
                "camera_selection_persistence_failed",
                "The camera switch was rolled back because the local selection could not be saved.",
            )
        except CameraSelectionOperationError:
            return _error_response(
                503,
                "camera_selection_failed",
                "The requested camera could not be activated; the previous source was preserved when possible.",
            )
        return _camera_catalog_response(catalog)

    @application.get("/api/plugins", response_model=PluginListResponse)
    async def list_plugins() -> PluginListResponse:
        """Return configured passive preview plugins without exposing import locations."""

        host = service.plugin_host
        if host is None:
            return {"plugins": []}
        return {
            "plugins": [
                _plugin_status_response(status, service.plugin_lifecycle_controls_enabled)
                for status in await host.statuses()
            ]
        }

    @application.get("/api/plugins/{plugin_id}/status", response_model=PluginStatusResponse)
    async def plugin_status(plugin_id: str):
        """Return one configured plugin lifecycle status or a stable not-found response."""

        host = service.plugin_host
        if host is None:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")
        try:
            return _plugin_status_response(
                await host.status(plugin_id), service.plugin_lifecycle_controls_enabled
            )
        except UnknownPluginError:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")

    @application.put(
        "/api/plugins/{plugin_id}/activation",
        response_model=PluginStatusResponse,
    )
    async def update_plugin_activation(plugin_id: str, request: PluginActivationRequest):
        """Replace one passive plugin's enabled state through the local lifecycle policy."""

        try:
            status = await service.set_plugin_enabled(plugin_id, request.enabled)
        except PluginLifecycleControlsDisabledError:
            return _error_response(
                403,
                "plugin_lifecycle_controls_disabled",
                "Plugin enable and disable controls require an explicit local preview configuration.",
            )
        except UnknownPluginError:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")
        except (PluginLifecycleInProgressError, PluginReloadInProgressError):
            return _error_response(
                409,
                "plugin_lifecycle_in_progress",
                "The requested plugin is already changing lifecycle state.",
            )
        except ValueError as error:
            return _error_response(422, "invalid_plugin_activation_request", str(error))
        except PluginLifecyclePersistenceError:
            return _error_response(
                503,
                "plugin_lifecycle_persistence_failed",
                "The plugin state could not be saved locally; the previous state was restored when possible.",
            )
        except PluginHostError:
            return _error_response(
                503,
                "plugin_lifecycle_failed",
                "The plugin could not complete the requested lifecycle change.",
            )
        return _plugin_status_response(status, service.plugin_lifecycle_controls_enabled)

    @application.post("/api/plugins/reload", response_model=PluginListResponse)
    async def reload_plugins(request: PluginReloadRequest):
        """Reload configured passive plugins only when the local development policy permits it."""

        host = service.plugin_host
        if host is None:
            return _error_response(
                403,
                "plugin_reload_disabled",
                "Plugin reload is disabled by the current preview configuration.",
            )
        try:
            statuses = await service.reload_plugins(request.plugin_ids)
        except PluginReloadNotAllowedError:
            return _error_response(
                403,
                "plugin_reload_disabled",
                "Plugin reload is disabled by the current preview configuration.",
            )
        except UnknownPluginError:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")
        except PluginReloadInProgressError:
            return _error_response(
                409,
                "plugin_reload_in_progress",
                "The requested plugin reload is already in progress.",
            )
        except (PluginLifecycleInProgressError, PluginDisabledError):
            return _error_response(
                409,
                "plugin_lifecycle_conflict",
                "The requested plugin is disabled or changing lifecycle state.",
            )
        except ValueError as error:
            return _error_response(422, "invalid_plugin_reload_request", str(error))
        except PluginReloadFailedError:
            return _error_response(
                503,
                "plugin_reload_failed",
                "The plugin replacement failed and the previous active plugin was preserved.",
            )
        except PluginHostError:
            return _error_response(
                503,
                "plugin_reload_failed",
                "The plugin host is temporarily unavailable for reload.",
            )
        return {
            "plugins": [
                _plugin_status_response(status, service.plugin_lifecycle_controls_enabled)
                for status in statuses
            ]
        }

    @application.get("/api/cameras/{camera_id}/status", response_model=CameraStatusResponse)
    async def camera_status(camera_id: str):
        """Return the current capture and retry state for one known camera resource."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return _status_response(await service.hub.status())

    @application.get("/api/cameras/{camera_id}/parameters", response_model=CameraParametersResponse)
    async def camera_parameters(camera_id: str):
        """Return runtime-supported browser controls without exposing an MVS client."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        try:
            parameters = await service.get_camera_parameters()
        except CameraParameterCapabilityError:
            return _error_response(
                409,
                "camera_controls_unavailable",
                "The configured camera does not support browser parameter controls.",
            )
        except (CameraParameterError, CameraParameterOperationError):
            return _error_response(
                503,
                "camera_unavailable",
                "Camera parameters are temporarily unavailable.",
            )
        return _parameter_list_response(service, parameters)

    @application.get("/api/cameras/{camera_id}/pose", response_model=PoseTrackingResponse)
    async def camera_pose(camera_id: str):
        """Return the latest single-person pose without triggering a new model inference."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return _pose_response(
            await service.get_pose_preview_snapshot(),
            preview_config.pose_settings.enabled,
            preview_config.pose_settings.draw_skeleton,
        )

    @application.get("/api/cameras/{camera_id}/pose/frame")
    async def camera_pose_frame(camera_id: str, captured_at: float):
        """Return the JPEG source frame for one pose result without acquiring or inferring."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        frame = await service.get_pose_frame(captured_at)
        if frame is None:
            return _error_response(
                503,
                "pose_frame_unavailable",
                "No pose-synchronized camera frame is available for the requested capture time.",
            )
        return Response(
            content=frame.jpeg_payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Camera-Captured-At": str(frame.captured_at),
            },
        )

    @application.put("/api/cameras/{camera_id}/pose/target", response_model=PoseTrackingResponse)
    async def update_pose_target(camera_id: str, request: PoseTargetRequest):
        """Persist then select one allowed COCO joint without exposing motion controls."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        try:
            await service.update_pose_target(request.target_joint)
        except PoseTrackingCapabilityError:
            return _error_response(
                409,
                "pose_tracking_disabled",
                "Pose tracking is disabled by the current preview configuration.",
            )
        except PoseTargetError as error:
            return _error_response(422, "invalid_pose_target", str(error))
        except PoseTargetPersistenceError:
            return _error_response(
                503,
                "pose_target_persistence_failed",
                "The selected pose target could not be saved locally.",
            )
        return _pose_response(
            await service.get_pose_preview_snapshot(),
            preview_config.pose_settings.enabled,
            preview_config.pose_settings.draw_skeleton,
        )

    @application.get(
        "/api/cameras/{camera_id}/vision/analysis",
        response_model=VisionAnalysisResponse,
    )
    async def camera_vision_analysis(camera_id: str):
        """Return cached frame quality and human recognition analysis without new inference."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return _vision_analysis_response(await service.get_vision_analysis_snapshot())

    @application.get(
        "/api/cameras/{camera_id}/objects",
        response_model=ObjectPoseTrackingResponse,
    )
    async def camera_objects(camera_id: str):
        """Return cached known-workpiece metadata without capture, inference, or device work."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return _object_pose_response(await service.get_object_pose_preview_snapshot())

    @application.get(
        "/api/cameras/{camera_id}/detections",
        response_model=ObjectDetectionTrackingResponse,
    )
    async def camera_detections(camera_id: str):
        """Return cached semantic boxes without capture, inference, or hardware work."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        preview = await service.get_object_detection_preview_snapshot()
        profiles = await service.get_object_detection_model_profiles()
        return _object_detection_response(preview, profiles)

    @application.put(
        "/api/cameras/{camera_id}/detections/model-selection",
        response_model=ObjectDetectionTrackingResponse,
    )
    async def select_detection_model(
        camera_id: str, request: ObjectDetectionModelSelectionRequest
    ):
        """Switch only among configured passive models and clear prior boxes."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        try:
            await service.select_object_detection_model(request.model_id)
        except UnknownDetectionModelError:
            return _error_response(
                404,
                "detection_model_not_found",
                "The requested object detection model is not configured.",
            )
        except DetectionModelUnavailableError:
            return _error_response(
                409,
                "detection_model_unavailable",
                "The requested object detection model is not available in localstore.",
            )
        preview = await service.get_object_detection_preview_snapshot()
        profiles = await service.get_object_detection_model_profiles()
        return _object_detection_response(preview, profiles)

    @application.post(
        "/api/cameras/{camera_id}/parameters/apply",
        response_model=CameraParameterUpdateResponse,
    )
    async def apply_restart_parameters(camera_id: str, request: CameraParameterBatchRequest):
        """Apply staged acquisition-locked parameters then resume camera acquisition."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return await _apply_camera_parameters(
            service,
            request.values,
            CameraParameterApplyMode.RESTART,
        )

    @application.patch(
        "/api/cameras/{camera_id}/parameters/{parameter_key}",
        response_model=CameraParameterUpdateResponse,
    )
    async def update_live_parameter(
        camera_id: str,
        parameter_key: str,
        request: CameraParameterValueRequest,
    ):
        """Apply one stream-safe parameter immediately after adapter validation."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        return await _apply_camera_parameters(
            service,
            {parameter_key: request.value},
            CameraParameterApplyMode.LIVE,
        )

    @application.get("/api/cameras/{camera_id}/frame")
    async def camera_frame(camera_id: str):
        """Return the latest JPEG snapshot or a normalized unavailable-camera error."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown
        frame = await service.hub.latest_frame()
        if frame is None:
            return _error_response(
                503,
                "camera_unavailable",
                "No browser-ready camera frame is available yet.",
            )
        return Response(
            content=frame.jpeg_payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/cameras/{camera_id}/stream")
    async def camera_stream(camera_id: str):
        """Return one MJPEG stream that reuses the shared latest-frame acquisition loop."""

        unknown = _unknown_camera_response(camera_id, service.camera_id)
        if unknown is not None:
            return unknown

        async def generate_mjpeg():
            """Yield each newest JPEG once per client without allocating a second camera loop."""

            previous_frame = None
            while True:
                frame = await service.hub.wait_for_frame(previous_frame)
                if frame is None:
                    return
                previous_frame = frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + "Content-Length: {0}\r\n\r\n".format(len(frame.jpeg_payload)).encode("ascii")
                    + frame.jpeg_payload
                    + b"\r\n"
                )

        return StreamingResponse(
            generate_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    # 录制和截图服务
    recording_service = None
    if preview_config.settings.recording_enabled:
        from gripper_ai_controller.web.recording_service import RecordingService
        recording_service = RecordingService(
            output_dir=preview_config.settings.recording_output_dir,
            default_fps=preview_config.settings.recording_default_fps,
            enabled=True
        )
        _install_recording_routes(application, service, recording_service)

    install_gripper_routes(application, gripper_control_service)
    install_jaka_routes(application, jaka_control_service)

    # 安装标定路由
    if calibration_service is not None:
        set_calibration_service(calibration_service)
        install_calibration_routes(application)

    _mount_frontend_if_present(application, frontend_dist_dir)
    return application


def create_web_app_factory() -> FastAPI:
    """Zero-argument factory used by Uvicorn --reload for auto-reloading development mode."""

    import os
    from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config

    config_file = os.environ.get("GRIPPER_CONFIG_FILE")
    if not config_file:
        raise RuntimeError(
            "GRIPPER_CONFIG_FILE is required for the reload app factory. "
            "Start the service with an explicit 'web --config-file ... --reload' command."
        )

    preview_config = load_vision_preview_config(config_file)
    return create_web_app(preview_config, preview_config.settings.frontend_dist_dir)


def _build_preview_plugin_host(preview_config: VisionPreviewConfig) -> PluginHost:
    """Create configured passive preview plugins from fixed server-side factory entries.

    Browser requests can only name entries already present in this host. The mapping
    deliberately keeps Python import locations out of JSON and HTTP input while
    letting project-local plugins evolve independently.
    """

    descriptors = []
    for plugin_id in preview_config.preview_plugin_names:
        factory = _PREVIEW_PLUGIN_FACTORIES.get(plugin_id)
        if factory is None:
            raise ValueError("Unsupported preview plugin: {0}".format(plugin_id))
        module_name, factory_name, manifest, ui_kind = factory
        descriptors.append(
            PluginFactoryDescriptor(
                plugin_id=plugin_id,
                module_name=module_name,
                factory_name=factory_name,
                factory_kwargs=_preview_plugin_factory_kwargs(plugin_id, preview_config),
                manifest=manifest,
                ui_kind=ui_kind,
                camera_binding_requirement=_preview_plugin_camera_requirement(plugin_id),
                camera_binding=CameraBinding((preview_config.camera_id,)),
            )
        )
    return PluginHost(
        descriptors,
        reload_enabled=preview_config.settings.plugin_reload_enabled,
        enabled_plugin_ids=preview_config.preview_plugin_enabled,
    )


def _preview_plugin_factory_kwargs(
    plugin_id: str, preview_config: VisionPreviewConfig
) -> Dict[str, object]:
    """Supply only each trusted plugin's declared passive dependencies."""

    if plugin_id == "visual-pose-analysis":
        return {
            "camera_id": preview_config.camera_id,
            "pose_settings": preview_config.pose_settings,
            "vision_analysis_settings": preview_config.vision_analysis_settings,
        }
    if plugin_id == "object-pose-analysis":
        return {
            "camera_id": preview_config.camera_id,
            "workpiece_pose_settings": preview_config.workpiece_pose_settings,
        }
    if plugin_id == "object-detection-analysis":
        return {
            "camera_id": preview_config.camera_id,
            "object_detection_settings": preview_config.object_detection_settings,
        }
    raise ValueError("Unsupported preview plugin: {0}".format(plugin_id))


def _preview_plugin_camera_requirement(plugin_id: str) -> CameraBindingRequirement:
    """Declare the current shared preview input for every registered visual plugin."""

    if plugin_id not in _PREVIEW_PLUGIN_FACTORIES:
        raise ValueError("Unsupported preview plugin: {0}".format(plugin_id))
    return CameraBindingRequirement.shared_single_source()


def _plugin_status_response(
    status: PluginStatus, lifecycle_controllable: bool
) -> Dict[str, Any]:
    """Map host state to the browser's compact dynamic-plugin resource schema."""

    camera_binding = None
    requirement = status.camera_binding_requirement
    if requirement.requires_camera:
        camera_binding = {
            "mode": requirement.mode,
            "camera_ids": list(status.camera_binding.camera_ids),
            "minimum_sources": requirement.minimum_sources,
            "maximum_sources": requirement.maximum_sources,
            "state": "satisfied"
            if status.camera_binding.satisfies(requirement)
            else "unbound",
        }
    return {
        "plugin_id": status.plugin_id,
        "name": status.name,
        "version": status.version,
        "capabilities": list(status.capabilities),
        "ui_kind": status.ui_kind,
        "state": status.lifecycle_state,
        "error": status.error,
        "reloadable": status.reloadable,
        "enabled": status.enabled,
        "lifecycle_controllable": lifecycle_controllable,
        "camera_binding": camera_binding,
    }


def _status_response(status: CameraPreviewStatus):
    """Map an internal immutable status object to the documented API response shape."""

    error = None
    if status.error is not None:
        error = {"code": status.error.code, "message": status.error.message}
    return {
        "camera_id": status.camera_id,
        "state": status.state,
        "latest_frame_at": status.latest_frame_at,
        "error": error,
    }


def _camera_catalog_response(catalog: CameraCatalogSnapshot) -> Dict[str, Any]:
    """Serialize one active pipeline and its browser-safe device discovery result."""

    discovery_error = None
    if catalog.discovery_error is not None:
        discovery_error = {
            "code": catalog.discovery_error.code,
            "message": catalog.discovery_error.message,
        }
    return {
        "cameras": [_status_response(catalog.status)],
        "devices": [_camera_device_response(device) for device in catalog.devices],
        "selected_device_id": catalog.selected_device_id,
        "selection_enabled": catalog.selection_enabled,
        "discovery_error": discovery_error,
    }


def _camera_device_response(device: CameraDeviceDescriptor) -> Dict[str, Any]:
    """Map an adapter descriptor without exposing vendor-native device metadata."""

    return {
        "device_id": device.device_id,
        "display_name": device.display_name,
        "model_name": device.model_name,
        "transport": device.transport,
        "selected": device.selected,
        "calibrated": device.calibrated,
    }


def _unknown_camera_response(requested_camera_id: str, configured_camera_id: str):
    """Return a stable 404 payload when a request names an unconfigured camera resource."""

    if requested_camera_id == configured_camera_id:
        return None
    return _error_response(404, "camera_not_found", "The requested camera is not configured for preview.")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return a normalized JSON error rather than FastAPI's framework-specific detail shape."""

    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


async def _apply_camera_parameters(
    service: CameraPreviewService,
    updates: Dict[str, Any],
    expected_apply_mode: CameraParameterApplyMode,
):
    """Map the controlled service facade's update failures to stable HTTP errors."""

    try:
        result = await service.update_camera_parameters(updates, expected_apply_mode)
    except CameraParameterWriteDisabledError:
        return _error_response(
            403,
            "camera_controls_disabled",
            "Camera parameter writes are disabled by the local preview configuration.",
        )
    except CameraParameterCapabilityError:
        return _error_response(
            409,
            "camera_controls_unavailable",
            "The configured camera does not support browser parameter controls.",
        )
    except CameraParameterError as error:
        return _error_response(422, "invalid_camera_parameter", str(error))
    except CameraParameterOperationError:
        return _error_response(
            503,
            "camera_parameter_apply_failed",
            "The camera could not apply the requested parameter update.",
        )
    except CameraParameterPersistenceError:
        return _error_response(
            503,
            "camera_parameter_persistence_failed",
            "The camera applied the requested parameter, but the local configuration could not be saved.",
        )
    return _parameter_update_response(service, result.parameters, result.restarted_acquisition)


def _parameter_list_response(service: CameraPreviewService, parameters):
    """Map immutable domain parameter descriptors to a browser-safe API response."""

    return {
        "camera_id": service.camera_id,
        "write_enabled": service.camera_controls_enabled,
        "parameters": [_parameter_response(parameter) for parameter in parameters],
    }


def _parameter_update_response(service: CameraPreviewService, parameters, restarted_acquisition: bool):
    """Add the observed restart outcome to the standard parameter list response."""

    response = _parameter_list_response(service, parameters)
    response["restarted_acquisition"] = restarted_acquisition
    return response


def _parameter_response(parameter: CameraParameter):
    """Serialize one adapter-normalized value without vendor node names or SDK handles."""

    return {
        "key": parameter.key,
        "kind": parameter.kind.value,
        "apply_mode": parameter.apply_mode.value,
        "value": parameter.value,
        "minimum": parameter.minimum,
        "maximum": parameter.maximum,
        "step": parameter.step,
        "unit": parameter.unit,
        "options": list(parameter.options),
    }


def _pose_response(preview: PosePreviewSnapshot, enabled: bool, draw_skeleton: bool):
    """Map typed pose metadata to a JSON response without serializing images or model tensors."""

    snapshot = preview.snapshot

    return {
        "camera_id": snapshot.camera_id,
        "enabled": enabled,
        "captured_at": snapshot.captured_at,
        "valid": snapshot.valid,
        "reason": snapshot.reason,
        "target_joint": snapshot.target_joint,
        "target": None if snapshot.target is None else _pose_joint_response(snapshot.target),
        "person": None if snapshot.person is None else _human_pose_response(snapshot.person),
        "inference_latency_ms": snapshot.inference_latency_ms,
        "lost_frames": snapshot.lost_frames,
        "motion": None if snapshot.motion is None else _pose_motion_response(snapshot.motion),
        "draw_skeleton": draw_skeleton,
        "latest_frame_at": preview.latest_frame_at,
        "overlay_fresh": preview.overlay_fresh,
    }


def _vision_analysis_response(snapshot: VisionAnalysisSnapshot):
    """Serialize cached read-only diagnostics without exposing image payloads or model tensors."""

    frame = None
    if snapshot.frame is not None:
        frame = {
            "captured_at": snapshot.frame.captured_at,
            "valid": snapshot.frame.valid,
            "width": snapshot.frame.width,
            "height": snapshot.frame.height,
            "pixel_format": snapshot.frame.pixel_format,
            "brightness_mean": snapshot.frame.brightness_mean,
            "contrast": snapshot.frame.contrast,
            "sharpness": snapshot.frame.sharpness,
            "warnings": list(snapshot.frame.warnings),
        }
    return {
        "camera_id": snapshot.camera_id,
        "frame_captured_at": snapshot.frame_captured_at,
        "inference_captured_at": snapshot.inference_captured_at,
        "pose_enabled": snapshot.pose_enabled,
        "valid": snapshot.valid,
        "reason": snapshot.reason,
        "frame": frame,
        "person_count": len(snapshot.persons),
        "persons": [
            {
                "bounding_box": {
                    "x": person.bounding_box.x,
                    "y": person.bounding_box.y,
                    "width": person.bounding_box.width,
                    "height": person.bounding_box.height,
                },
                "confidence": person.confidence,
                "selected": person.selected,
            }
            for person in snapshot.persons
        ],
        "selected_person": (
            None if snapshot.selected_person is None else _human_pose_response(snapshot.selected_person)
        ),
        "joint_visibility": [
            {
                "name": joint.name,
                "state": joint.state,
                "confidence": joint.confidence,
                "normalized_x": joint.normalized_x,
                "normalized_y": joint.normalized_y,
            }
            for joint in snapshot.joint_visibility
        ],
        "visible_joint_names": list(snapshot.visible_joint_names),
    }


def _object_pose_response(preview: ObjectPosePreviewSnapshot):
    """Serialize cached plane-constrained workpiece metadata without a grasp target."""

    snapshot = preview.snapshot
    return {
        "camera_id": snapshot.camera_id,
        "enabled": snapshot.enabled,
        "captured_at": snapshot.captured_at,
        "latest_frame_at": preview.latest_frame_at,
        "overlay_fresh": preview.overlay_fresh,
        "valid": snapshot.valid,
        "reason": snapshot.reason,
        "inference_latency_ms": snapshot.inference_latency_ms,
        "objects": [_workpiece_pose_response(object_pose) for object_pose in snapshot.objects],
    }


def _object_detection_response(preview: ObjectDetectionPreviewSnapshot, profiles):
    """Serialize normalized semantic boxes and the configured model catalog."""

    snapshot = preview.snapshot
    models = [
        {
            "model_id": profile.model_id,
            "display_name": profile.display_name,
            "provider": profile.provider_id,
            "available": Path(profile.model_path).is_file(),
            "selected": profile.model_id == snapshot.selected_model_id,
        }
        for profile in profiles
    ]
    detections = []
    for index, detection in enumerate(snapshot.detections):
        captured_key = "none" if snapshot.captured_at is None else "{0:.6f}".format(snapshot.captured_at)
        detections.append(
            {
                "detection_id": "{0}-{1}".format(captured_key, index),
                "label": detection.label,
                "class_id": detection.class_id,
                "confidence": detection.confidence,
                "bounding_box": {
                    "x": detection.bounding_box.x,
                    "y": detection.bounding_box.y,
                    "width": detection.bounding_box.width,
                    "height": detection.bounding_box.height,
                },
            }
        )
    return {
        "camera_id": snapshot.camera_id,
        "enabled": snapshot.enabled,
        "selected_model_id": snapshot.selected_model_id,
        "models": models,
        "captured_at": snapshot.captured_at,
        "latest_frame_at": preview.latest_frame_at,
        "overlay_fresh": preview.overlay_fresh,
        "valid": snapshot.valid,
        "reason": snapshot.reason,
        "inference_latency_ms": snapshot.inference_latency_ms,
        "detections": detections,
    }


def _workpiece_pose_response(object_pose):
    """Map one internal result to the stable browser schema without exposing calibration internals."""

    return {
        "profile_id": object_pose.profile_id,
        "confidence": object_pose.confidence,
        "bounding_box": {
            "x": object_pose.bounding_box.x,
            "y": object_pose.bounding_box.y,
            "width": object_pose.bounding_box.width,
            "height": object_pose.bounding_box.height,
        },
        "contour": [{"x": point.x, "y": point.y} for point in object_pose.contour],
        "pixel_center": {
            "x": object_pose.pixel_center.x,
            "y": object_pose.pixel_center.y,
        },
        "normalized_center": {
            "x": object_pose.normalized_center.x,
            "y": object_pose.normalized_center.y,
        },
        "coordinate_frame": object_pose.coordinate_frame,
        "translation_mm": {
            "x": object_pose.translation_mm.x_mm,
            "y": object_pose.translation_mm.y_mm,
            "z": object_pose.translation_mm.z_mm,
        },
        "orientation_rpy_rad": {
            "roll": object_pose.orientation_rpy_rad.roll_rad,
            "pitch": object_pose.orientation_rpy_rad.pitch_rad,
            "yaw": object_pose.orientation_rpy_rad.yaw_rad,
        },
        "observed_dof": list(object_pose.observed_dof),
        "derived_dof": list(object_pose.derived_dof),
        "yaw_period_rad": object_pose.yaw_period_rad,
        "orientation_defined": object_pose.orientation_defined,
        "warning": object_pose.warning,
    }


def _pose_joint_response(joint: PoseJoint2D):
    """Serialize one normalized joint without exposing a model-specific keypoint index."""

    return {
        "name": joint.name,
        "x_px": joint.x_px,
        "y_px": joint.y_px,
        "normalized_x": joint.normalized_x,
        "normalized_y": joint.normalized_y,
        "confidence": joint.confidence,
    }


def _pose_motion_response(motion: PoseMotion2D):
    """Serialize passive normalized image-space movement without control semantics."""

    return {
        "previous_captured_at": motion.previous_captured_at,
        "captured_at": motion.captured_at,
        "target_joint": motion.target_joint,
        "delta_x": motion.delta_x,
        "delta_y": motion.delta_y,
        "displacement": motion.displacement,
        "velocity_x": motion.velocity_x,
        "velocity_y": motion.velocity_y,
        "speed": motion.speed,
        "moving": motion.moving,
    }


def _human_pose_response(person: HumanPose2D):
    """Serialize the selected person and ordered named joints for canvas rendering."""

    return {
        "camera_id": person.camera_id,
        "captured_at": person.captured_at,
        "bounding_box": {
            "x": person.bounding_box.x,
            "y": person.bounding_box.y,
            "width": person.bounding_box.width,
            "height": person.bounding_box.height,
        },
        "confidence": person.confidence,
        "joints": [_pose_joint_response(joint) for joint in person.joints],
    }


def _install_recording_routes(application: FastAPI, camera_service, recording_service) -> None:
    """Install camera recording and snapshot routes."""

    @application.post("/api/cameras/{camera_id}/snapshot")
    async def save_snapshot(camera_id: str, prefix: str = "snapshot"):
        """Save current frame as JPEG snapshot."""
        unknown = _unknown_camera_response(camera_id, camera_service.camera_id)
        if unknown is not None:
            return unknown

        frame = await camera_service.hub.latest_frame()
        if frame is None:
            return _error_response(503, "camera_unavailable", "No frame available")

        try:
            filepath = await recording_service.save_snapshot(
                frame.jpeg_payload,
                camera_id,
                prefix
            )
            return {"filepath": filepath, "size_bytes": len(frame.jpeg_payload)}
        except Exception as e:
            return _error_response(500, "snapshot_failed", str(e))

    @application.post("/api/cameras/{camera_id}/recording/start")
    async def start_recording(camera_id: str, fps: Optional[int] = None, prefix: str = "recording"):
        """Start video recording."""
        unknown = _unknown_camera_response(camera_id, camera_service.camera_id)
        if unknown is not None:
            return unknown

        if recording_service.is_recording(camera_id):
            return _error_response(409, "already_recording", "Camera is already recording")

        try:
            recording_id = await recording_service.start_recording(camera_id, fps, prefix=prefix)
            # 启动后台任务来捕获帧
            asyncio.create_task(_recording_loop(camera_service, recording_service, camera_id))
            return {"recording_id": recording_id, "status": "started"}
        except Exception as e:
            return _error_response(500, "recording_start_failed", str(e))

    @application.post("/api/cameras/{camera_id}/recording/stop")
    async def stop_recording(camera_id: str):
        """Stop video recording."""
        unknown = _unknown_camera_response(camera_id, camera_service.camera_id)
        if unknown is not None:
            return unknown

        result = await recording_service.stop_recording(camera_id)
        if result is None:
            return _error_response(404, "not_recording", "Camera is not recording")

        return result

    @application.get("/api/cameras/{camera_id}/recording/status")
    async def recording_status(camera_id: str):
        """Get current recording status."""
        unknown = _unknown_camera_response(camera_id, camera_service.camera_id)
        if unknown is not None:
            return unknown

        status = recording_service.get_recording_status(camera_id)
        if status is None:
            return {"is_recording": False}

        return {"is_recording": True, **status}

    @application.get("/api/recordings")
    async def list_recordings(camera_id: Optional[str] = None):
        """List all recordings."""
        recordings = await recording_service.list_recordings(camera_id)
        return {"recordings": recordings}

    @application.get("/api/snapshots")
    async def list_snapshots(camera_id: Optional[str] = None):
        """List all snapshots."""
        snapshots = await recording_service.list_snapshots(camera_id)
        return {"snapshots": snapshots}

    @application.delete("/api/recordings/{recording_id}")
    async def delete_recording(recording_id: str):
        """Delete a recording."""
        success = await recording_service.delete_recording(recording_id)
        if not success:
            return _error_response(404, "recording_not_found", "Recording not found")
        return {"status": "deleted"}

    @application.delete("/api/snapshots/{filename}")
    async def delete_snapshot(filename: str):
        """Delete a snapshot."""
        success = await recording_service.delete_snapshot(filename)
        if not success:
            return _error_response(404, "snapshot_not_found", "Snapshot not found")
        return {"status": "deleted"}


async def _recording_loop(camera_service, recording_service, camera_id: str):
    """Background task to capture frames during recording."""
    previous_frame = None
    while recording_service.is_recording(camera_id):
        try:
            frame = await camera_service.hub.wait_for_frame(previous_frame)
            if frame is None:
                break
            previous_frame = frame
            await recording_service.add_frame(camera_id, frame.jpeg_payload)
        except Exception:
            break
        await asyncio.sleep(0.001)  # 防止过度占用CPU


def _mount_frontend_if_present(application: FastAPI, frontend_dist_dir: Optional[str]) -> None:
    """Mount Vite build output only when the caller supplies an existing explicit path."""

    if frontend_dist_dir is None:
        return
    path = Path(frontend_dist_dir)
    if not path.is_dir():
        return
    application.mount("/", StaticFiles(directory=str(path), html=True), name="frontend")
