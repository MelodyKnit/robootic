"""FastAPI contracts for the safety-gated manual gripper control facade."""

from typing import List, Optional

from fastapi import FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from gripper_ai_controller.web.gripper_service import (
    GripperArmSession,
    GripperControlConflictError,
    GripperControlError,
    GripperControlsDisabledError,
    GripperControlValidationError,
    GripperNotArmedError,
    GripperUnavailableError,
    ManualGripperControlService,
    ManualGripperOperationResult,
    ManualGripperStatus,
)


class GripperErrorResponse(BaseModel):
    """Stable JSON error payload returned by every gripper control route."""

    code: str
    message: str


class GripperStatusResponse(BaseModel):
    """Browser-safe live state and configured bounds for one gripper resource."""

    gripper_id: str
    mode: str
    controls_enabled: bool
    connected: bool
    initialized: bool
    initializing: bool
    moving: bool
    gripping: bool
    position: int
    position_is_feedback: bool
    grip_state: str
    supports_speed: bool
    supports_stop: bool
    minimum_position: int
    maximum_position: int
    minimum_force_percent: int
    maximum_force_percent: int
    minimum_speed_percent: int
    maximum_speed_percent: int
    open_position: int
    close_position: int
    armed_until: Optional[float]
    last_error: Optional[str]


class GripperListResponse(BaseModel):
    """Configured grippers retained as a list for future multi-device expansion."""

    grippers: List[GripperStatusResponse]


class GripperArmRequest(BaseModel):
    """Explicit operator confirmations required before a control token is issued."""

    work_area_clear: bool
    emergency_stop_ready: bool


class GripperArmResponse(BaseModel):
    """Temporary browser capability returned only after safety confirmation."""

    token: str
    expires_at: float
    status: GripperStatusResponse


class GripperCommandRequest(BaseModel):
    """One explicit command draft submitted by the browser execution button."""

    action: str
    target_position: Optional[int] = None
    force_percent: Optional[int] = None
    speed_percent: Optional[int] = None


class GripperOperationResponse(BaseModel):
    """Idempotent initialization or command outcome safe for browser rendering."""

    idempotency_key: str
    replayed: bool
    status: GripperStatusResponse


def install_gripper_routes(
    application: FastAPI,
    service: Optional[ManualGripperControlService],
) -> None:
    """Install one optional gripper resource without changing camera route ownership."""

    application.state.manual_gripper_control_service = service

    @application.exception_handler(RequestValidationError)
    async def invalid_gripper_request(request, error):
        """Keep malformed gripper and camera inputs on the shared error contract."""

        del request, error
        return _error_response(422, "invalid_request", "The request body is invalid.")

    @application.get("/api/grippers", response_model=GripperListResponse)
    async def list_grippers():
        """Return zero or one configured manual gripper without creating hardware."""

        if service is None:
            return {"grippers": []}
        try:
            status = await service.status()
        except GripperControlError as error:
            return _error_for(error)
        return {"grippers": [_status_response(status)]}

    @application.get(
        "/api/grippers/{gripper_id}/status",
        response_model=GripperStatusResponse,
    )
    async def gripper_status(gripper_id: str):
        """Read current device feedback without changing adapter or arm state."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        try:
            return _status_response(await service.status())  # type: ignore[union-attr]
        except GripperControlError as error:
            return _error_for(error)

    @application.post(
        "/api/grippers/{gripper_id}/reconnect",
        response_model=GripperStatusResponse,
    )
    async def reconnect_gripper(gripper_id: str):
        """Reconnect without initialization or motion and revoke any old token."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        try:
            return _status_response(await service.reconnect())  # type: ignore[union-attr]
        except GripperControlError as error:
            return _error_for(error)

    @application.post(
        "/api/grippers/{gripper_id}/arm",
        response_model=GripperArmResponse,
    )
    async def arm_gripper(gripper_id: str, request: GripperArmRequest):
        """Issue a short-lived token after both required safety confirmations."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        try:
            session = await service.arm(  # type: ignore[union-attr]
                request.work_area_clear,
                request.emergency_stop_ready,
            )
        except GripperControlError as error:
            return _error_for(error)
        return _arm_response(session)

    @application.delete("/api/grippers/{gripper_id}/arm", status_code=204)
    async def disarm_gripper(
        gripper_id: str,
        control_token: Optional[str] = Header(
            None,
            alias="X-Gripper-Control-Token",
        ),
    ):
        """Revoke browser command authority without sending a device operation."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        try:
            await service.disarm(control_token)  # type: ignore[union-attr]
        except GripperControlError as error:
            return _error_for(error)
        return Response(status_code=204)

    @application.post(
        "/api/grippers/{gripper_id}/initialization",
        response_model=GripperOperationResponse,
    )
    async def initialize_gripper(
        gripper_id: str,
        control_token: Optional[str] = Header(
            None,
            alias="X-Gripper-Control-Token",
        ),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        """Run ordinary initialization once for the caller-provided operation key."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        if idempotency_key is None:
            return _error_response(
                422,
                "invalid_gripper_command",
                "Idempotency-Key is required for initialization.",
            )
        try:
            result = await service.initialize(  # type: ignore[union-attr]
                control_token,
                idempotency_key,
            )
        except GripperControlError as error:
            return _error_for(error)
        return _operation_response(result)

    @application.post(
        "/api/grippers/{gripper_id}/commands",
        response_model=GripperOperationResponse,
    )
    async def submit_gripper_command(
        gripper_id: str,
        request: GripperCommandRequest,
        control_token: Optional[str] = Header(
            None,
            alias="X-Gripper-Control-Token",
        ),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        """Submit one normalized movement request through authorization and safety."""

        missing = _unknown_gripper_response(gripper_id, service)
        if missing is not None:
            return missing
        if idempotency_key is None:
            return _error_response(
                422,
                "invalid_gripper_command",
                "Idempotency-Key is required for gripper commands.",
            )
        try:
            result = await service.execute_command(  # type: ignore[union-attr]
                control_token,
                idempotency_key,
                request.action,
                request.target_position,
                request.force_percent,
                request.speed_percent,
            )
        except GripperControlError as error:
            return _error_for(error)
        return _operation_response(result)


def _unknown_gripper_response(
    requested_gripper_id: str,
    service: Optional[ManualGripperControlService],
) -> Optional[JSONResponse]:
    """Return a normalized 404 for absent or differently named gripper resources."""

    if service is not None and requested_gripper_id == service.gripper_id:
        return None
    return _error_response(
        404,
        "gripper_not_found",
        "The requested gripper is not configured for manual control.",
    )


def _error_for(error: GripperControlError) -> JSONResponse:
    """Map service categories to the stable HTTP status contract."""

    if isinstance(error, (GripperControlsDisabledError, GripperNotArmedError)):
        status_code = 403
    elif isinstance(error, GripperControlConflictError):
        status_code = 409
    elif isinstance(error, GripperControlValidationError):
        status_code = 422
    elif isinstance(error, GripperUnavailableError):
        status_code = 503
    else:
        status_code = 503
    return _error_response(status_code, error.code, error.message)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Return the project-wide normalized API error payload."""

    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


def _status_response(status: ManualGripperStatus) -> GripperStatusResponse:
    """Convert one immutable service snapshot to its Pydantic response contract."""

    return GripperStatusResponse(**status.__dict__)


def _arm_response(session: GripperArmSession) -> GripperArmResponse:
    """Serialize one token and its device status without retaining it elsewhere."""

    return GripperArmResponse(
        token=session.token,
        expires_at=session.expires_at,
        status=_status_response(session.status),
    )


def _operation_response(result: ManualGripperOperationResult) -> GripperOperationResponse:
    """Serialize an idempotent operation result and whether it was replayed."""

    return GripperOperationResponse(
        idempotency_key=result.idempotency_key,
        replayed=result.replayed,
        status=_status_response(result.status),
    )
