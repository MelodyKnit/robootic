"""FastAPI application factory for browser camera preview and controlled parameters."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictStr

from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.core.plugin_host import (
    PluginFactoryDescriptor,
    PluginHost,
    PluginHostError,
    PluginReloadFailedError,
    PluginReloadInProgressError,
    PluginReloadNotAllowedError,
    PluginStatus,
    UnknownPluginError,
)
from gripper_ai_controller.domain.models import CameraParameter, CameraParameterApplyMode, RuntimeMode
from gripper_ai_controller.domain.ports import CameraParameterError
from gripper_ai_controller.pose.config_store import PoseTargetConfigStore
from gripper_ai_controller.pose.gpu import inspect_cuda_gpu
from gripper_ai_controller.pose.models import (
    HumanPose2D,
    PoseJoint2D,
    PoseMotion2D,
)
from gripper_ai_controller.pose.tracker import PoseTargetError
from gripper_ai_controller.services.safety import SafetyPolicy
from gripper_ai_controller.vision.models import VisionAnalysisSnapshot
from gripper_ai_controller.web.config_store import CameraParameterConfigStore
from gripper_ai_controller.web.gripper_api import install_gripper_routes
from gripper_ai_controller.web.gripper_service import ManualGripperControlService
from gripper_ai_controller.web.jaka_api import install_jaka_routes
from gripper_ai_controller.web.jaka_service import ManualJakaControlService
from gripper_ai_controller.web.models import CameraPreviewStatus, PosePreviewSnapshot
from gripper_ai_controller.web.service import (
    CameraParameterCapabilityError,
    CameraParameterOperationError,
    CameraParameterPersistenceError,
    CameraParameterWriteDisabledError,
    CameraPreviewService,
    PoseTargetPersistenceError,
    PoseTrackingCapabilityError,
)


_PREVIEW_PLUGIN_FACTORIES = {
    "visual-pose-analysis": (
        "gripper_ai_controller.plugins.visual_pose_analysis",
        "build_visual_pose_analysis_plugin",
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


class CameraListResponse(BaseModel):
    """A resource-oriented list response retained for future multi-camera expansion."""

    cameras: List[CameraStatusResponse]


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


class PluginListResponse(BaseModel):
    """Stable resource collection used by the dynamic browser plugin workspace."""

    plugins: List[PluginStatusResponse]


class PluginReloadRequest(BaseModel):
    """Select trusted configured plugin IDs; an empty list requests all configured plugins."""

    plugin_ids: List[StrictStr] = Field(default_factory=list)


def create_web_app(
    preview_config: VisionPreviewConfig,
    frontend_dist_dir: Optional[str] = None,
    preview_service: Optional[CameraPreviewService] = None,
    gripper_control_service: Optional[ManualGripperControlService] = None,
    jaka_control_service: Optional[ManualJakaControlService] = None,
) -> FastAPI:
    """Create one FastAPI app for preview plus optionally gated manual device control."""

    if preview_config.settings.plugin_reload_enabled and preview_config.runtime_mode != RuntimeMode.DEVELOPMENT:
        raise ValueError("Browser plugin reload requires a development preview configuration.")
    if (
        preview_config.settings.gripper_controls_enabled
        or preview_config.settings.jaka_controls_enabled
        or preview_config.settings.plugin_reload_enabled
    ) and preview_config.settings.bind_host != "127.0.0.1":
        raise ValueError(
            "Browser gripper, JAKA, or plugin reload controls require a 127.0.0.1 preview configuration."
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
        try:
            yield
        finally:
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

    @application.exception_handler(RequestValidationError)
    async def invalid_request_error(request, error):
        """Preserve the API error shape when FastAPI rejects malformed JSON input."""

        del request, error
        return _error_response(422, "invalid_request", "The request body is invalid.")

    @application.get("/api/cameras", response_model=CameraListResponse)
    async def list_cameras() -> CameraListResponse:
        """Return the configured camera list without starting any additional capture task."""

        return {"cameras": [_status_response(await service.hub.status())]}

    @application.get("/api/plugins", response_model=PluginListResponse)
    async def list_plugins() -> PluginListResponse:
        """Return configured passive preview plugins without exposing import locations."""

        host = service.plugin_host
        if host is None:
            return {"plugins": []}
        return {"plugins": [_plugin_status_response(status) for status in await host.statuses()]}

    @application.get("/api/plugins/{plugin_id}/status", response_model=PluginStatusResponse)
    async def plugin_status(plugin_id: str):
        """Return one configured plugin lifecycle status or a stable not-found response."""

        host = service.plugin_host
        if host is None:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")
        try:
            return _plugin_status_response(await host.status(plugin_id))
        except UnknownPluginError:
            return _error_response(404, "plugin_not_found", "The requested plugin is not configured for preview.")

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
        return {"plugins": [_plugin_status_response(status) for status in statuses]}

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

    install_gripper_routes(application, gripper_control_service)
    install_jaka_routes(application, jaka_control_service)
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
        module_name, factory_name = factory
        descriptors.append(
            PluginFactoryDescriptor(
                plugin_id=plugin_id,
                module_name=module_name,
                factory_name=factory_name,
                factory_kwargs={
                    "camera_id": preview_config.camera_id,
                    "pose_settings": preview_config.pose_settings,
                    "vision_analysis_settings": preview_config.vision_analysis_settings,
                },
            )
        )
    return PluginHost(descriptors, reload_enabled=preview_config.settings.plugin_reload_enabled)


def _plugin_status_response(status: PluginStatus) -> Dict[str, Any]:
    """Map host state to the browser's compact dynamic-plugin resource schema."""

    return {
        "plugin_id": status.plugin_id,
        "name": status.name,
        "version": status.version,
        "capabilities": list(status.capabilities),
        "ui_kind": status.ui_kind,
        "state": status.lifecycle_state,
        "error": status.error,
        "reloadable": status.reloadable,
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


def _mount_frontend_if_present(application: FastAPI, frontend_dist_dir: Optional[str]) -> None:
    """Mount Vite build output only when the caller supplies an existing explicit path."""

    if frontend_dist_dir is None:
        return
    path = Path(frontend_dist_dir)
    if not path.is_dir():
        return
    application.mount("/", StaticFiles(directory=str(path), html=True), name="frontend")
