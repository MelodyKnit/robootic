"""FastAPI contracts for the safety-gated manual JAKA joint-control facade."""

from typing import Any, List, Optional

from fastapi import FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from gripper_ai_controller.web.jaka_service import (
    JakaArmSession,
    JakaCapabilityError,
    JakaControlConflictError,
    JakaControlError,
    JakaControlsDisabledError,
    JakaControlValidationError,
    JakaIdempotencyConflictError,
    JakaMotionPreview,
    JakaNotArmedError,
    JakaUnavailableError,
    ManualJakaControlService,
    ManualJakaOperationResult,
    ManualJakaStatus,
)


class JakaErrorResponse(BaseModel):
    """Stable JSON error payload returned by every manual JAKA route."""

    code: str
    message: str


class JakaStatusResponse(BaseModel):
    """Browser-safe status, live joint angles, and fixed local JAKA limits."""

    robot_id: str
    mode: str
    controls_enabled: bool
    manual_motion_enabled: bool
    enable_permitted: bool
    connected: bool
    powered: bool
    enabled: bool
    moving: bool
    faulted: bool
    emergency_stopped: bool
    captured_at: float
    joint_positions_rad: List[float]
    joint_lower_limits_rad: List[float]
    joint_upper_limits_rad: List[float]
    maximum_joint_speed_rad_per_second: float
    maximum_joint_step_rad: float
    armed_until: Optional[float]
    last_error: Optional[str]


class JakaListResponse(BaseModel):
    """Configured manual JAKA targets retained as a list for future expansion."""

    robots: List[JakaStatusResponse]


class JakaArmRequest(BaseModel):
    """Physical safety confirmations required before issuing a temporary control token."""

    work_area_clear: bool
    emergency_stop_ready: bool


class JakaArmResponse(BaseModel):
    """One short-lived browser token and the status that issued it."""

    token: str
    expires_at: float
    status: JakaStatusResponse


class JakaMotionPreviewRequest(BaseModel):
    """One browser joint draft validated explicitly by the manual-control facade."""

    joint_positions_rad: List[Any]
    speed_rad_per_second: Any


class JakaMotionPreviewResponse(BaseModel):
    """A non-executing motion preview retained server-side for later confirmation."""

    preview_id: str
    expires_at: float
    source_joint_positions_rad: List[float]
    target_joint_positions_rad: List[float]
    joint_deltas_rad: List[float]
    estimated_duration_seconds: float
    status: JakaStatusResponse


class JakaCommandRequest(BaseModel):
    """Second confirmation payload that may reference only a server-created preview."""

    preview_id: str


class JakaOperationResponse(BaseModel):
    """Idempotent servo-enable or blocking joint-move outcome."""

    idempotency_key: str
    replayed: bool
    status: JakaStatusResponse


def install_jaka_routes(
    application: FastAPI,
    service: Optional[ManualJakaControlService],
) -> None:
    """Install the optional JAKA resource without changing camera or Runtime ownership."""

    application.state.manual_jaka_control_service = service

    @application.exception_handler(RequestValidationError)
    async def invalid_jaka_request(request, error):
        """Keep standalone JAKA route validation on the shared error contract."""

        del request, error
        return _error_response(422, "invalid_request", "The request body is invalid.")

    @application.get("/api/robots", response_model=JakaListResponse)
    async def list_robots():
        """Return zero or one configured manual JAKA target without creating hardware."""

        if service is None:
            return {"robots": []}
        try:
            return {"robots": [_status_response(await service.status())]}
        except JakaControlError as error:
            return _error_for(error)

    @application.get("/api/robots/{robot_id}/status", response_model=JakaStatusResponse)
    async def robot_status(robot_id: str):
        """Read current JAKA telemetry without enabling or moving the controller."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        try:
            return _status_response(await service.status())  # type: ignore[union-attr]
        except JakaControlError as error:
            return _error_for(error)

    @application.post("/api/robots/{robot_id}/reconnect", response_model=JakaStatusResponse)
    async def reconnect_robot(robot_id: str):
        """Replace the SDK session without enabling, powering, or moving JAKA."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        try:
            return _status_response(await service.reconnect())  # type: ignore[union-attr]
        except JakaControlError as error:
            return _error_for(error)

    @application.post("/api/robots/{robot_id}/arm", response_model=JakaArmResponse)
    async def arm_robot(robot_id: str, request: JakaArmRequest):
        """Issue one short-lived token after the operator confirms both safety checks."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        try:
            session = await service.arm(  # type: ignore[union-attr]
                request.work_area_clear,
                request.emergency_stop_ready,
            )
        except JakaControlError as error:
            return _error_for(error)
        return _arm_response(session)

    @application.delete("/api/robots/{robot_id}/arm", status_code=204)
    async def disarm_robot(
        robot_id: str,
        control_token: Optional[str] = Header(None, alias="X-Robot-Control-Token"),
    ):
        """Revoke browser command authority without contacting the JAKA controller."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        try:
            await service.disarm(control_token)  # type: ignore[union-attr]
        except JakaControlError as error:
            return _error_for(error)
        return Response(status_code=204)

    @application.post("/api/robots/{robot_id}/power-on", response_model=JakaOperationResponse)
    async def power_on_robot(
        robot_id: str,
        control_token: Optional[str] = Header(None, alias="X-Robot-Control-Token"),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        """Power on the controller after local authorization and safety confirmation."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        if idempotency_key is None:
            return _error_response(
                422,
                "invalid_jaka_command",
                "Idempotency-Key is required for power-on.",
            )
        try:
            result = await service.power_on(control_token, idempotency_key)  # type: ignore[union-attr]
        except JakaControlError as error:
            return _error_for(error)
        return _operation_response(result)

    @application.post("/api/robots/{robot_id}/enable", response_model=JakaOperationResponse)
    async def enable_robot(
        robot_id: str,
        control_token: Optional[str] = Header(None, alias="X-Robot-Control-Token"),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        """Enable already-powered servos after local authorization; never power on."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        if idempotency_key is None:
            return _error_response(
                422,
                "invalid_jaka_command",
                "Idempotency-Key is required for servo enable.",
            )
        try:
            result = await service.enable(control_token, idempotency_key)  # type: ignore[union-attr]
        except JakaControlError as error:
            return _error_for(error)
        return _operation_response(result)

    @application.post(
        "/api/robots/{robot_id}/joint-moves/preview",
        response_model=JakaMotionPreviewResponse,
    )
    async def preview_joint_move(
        robot_id: str,
        request: JakaMotionPreviewRequest,
        control_token: Optional[str] = Header(None, alias="X-Robot-Control-Token"),
    ):
        """Compile a six-axis draft and retain it for an explicit second confirmation."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        try:
            preview = await service.preview_joint_move(  # type: ignore[union-attr]
                control_token,
                request.joint_positions_rad,
                request.speed_rad_per_second,
            )
        except JakaControlError as error:
            return _error_for(error)
        return _preview_response(preview)

    @application.post("/api/robots/{robot_id}/commands", response_model=JakaOperationResponse)
    async def execute_preview(
        robot_id: str,
        request: JakaCommandRequest,
        control_token: Optional[str] = Header(None, alias="X-Robot-Control-Token"),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        """Execute one stored preview after fresh server-side telemetry validation."""

        missing = _unknown_robot_response(robot_id, service)
        if missing is not None:
            return missing
        if idempotency_key is None:
            return _error_response(
                422,
                "invalid_jaka_command",
                "Idempotency-Key is required for a JAKA joint move.",
            )
        try:
            result = await service.execute_preview(  # type: ignore[union-attr]
                control_token,
                idempotency_key,
                request.preview_id,
            )
        except JakaControlError as error:
            return _error_for(error)
        return _operation_response(result)


def _unknown_robot_response(
    requested_robot_id: str,
    service: Optional[ManualJakaControlService],
) -> Optional[JSONResponse]:
    """Return one normalized 404 for an absent or differently named JAKA resource."""

    if service is not None and requested_robot_id == service.robot_id:
        return None
    return _error_response(
        404,
        "robot_not_found",
        "The requested JAKA robot is not configured for manual control.",
    )


def _error_for(error: JakaControlError) -> JSONResponse:
    """Map manual-control error categories onto the existing HTTP conventions."""

    if isinstance(error, (JakaControlsDisabledError, JakaNotArmedError)):
        status_code = 403
    elif isinstance(
        error,
        (
            JakaControlConflictError,
            JakaCapabilityError,
            JakaIdempotencyConflictError,
        ),
    ):
        status_code = 409
    elif isinstance(error, JakaControlValidationError):
        status_code = 422
    elif isinstance(error, JakaUnavailableError):
        status_code = 503
    else:
        status_code = 503
    return _error_response(status_code, error.code, error.message)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return the project's normalized ``code`` and ``message`` error shape."""

    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def _status_response(status: ManualJakaStatus) -> JakaStatusResponse:
    """Convert immutable service telemetry into its Pydantic browser contract."""

    return JakaStatusResponse(
        robot_id=status.robot_id,
        mode=status.mode,
        controls_enabled=status.controls_enabled,
        manual_motion_enabled=status.manual_motion_enabled,
        enable_permitted=status.enable_permitted,
        connected=status.connected,
        powered=status.powered,
        enabled=status.enabled,
        moving=status.moving,
        faulted=status.faulted,
        emergency_stopped=status.emergency_stopped,
        captured_at=status.captured_at,
        joint_positions_rad=list(status.joint_positions_rad),
        joint_lower_limits_rad=list(status.joint_lower_limits_rad),
        joint_upper_limits_rad=list(status.joint_upper_limits_rad),
        maximum_joint_speed_rad_per_second=status.maximum_joint_speed_rad_per_second,
        maximum_joint_step_rad=status.maximum_joint_step_rad,
        armed_until=status.armed_until,
        last_error=status.last_error,
    )


def _arm_response(session: JakaArmSession) -> JakaArmResponse:
    """Serialize the in-memory token without persisting it in a client-visible store."""

    return JakaArmResponse(
        token=session.token,
        expires_at=session.expires_at,
        status=_status_response(session.status),
    )


def _preview_response(preview: JakaMotionPreview) -> JakaMotionPreviewResponse:
    """Serialize compiler-derived motion data without exposing SDK arguments."""

    return JakaMotionPreviewResponse(
        preview_id=preview.preview_id,
        expires_at=preview.expires_at,
        source_joint_positions_rad=list(preview.source_joint_positions_rad),
        target_joint_positions_rad=list(preview.target_joint_positions_rad),
        joint_deltas_rad=list(preview.joint_deltas_rad),
        estimated_duration_seconds=preview.estimated_duration_seconds,
        status=_status_response(preview.status),
    )


def _operation_response(result: ManualJakaOperationResult) -> JakaOperationResponse:
    """Serialize an idempotent result and whether this request reused an outcome."""

    return JakaOperationResponse(
        idempotency_key=result.idempotency_key,
        replayed=result.replayed,
        status=_status_response(result.status),
    )
