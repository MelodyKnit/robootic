"""Safety-gated manual JAKA joint control isolated from the autonomous runtime."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
import math
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union

from gripper_ai_controller.adapters.jaka import (
    JakaAdapter,
    JakaDryRunRobotAdapter,
    JakaJointMovePreview,
    JakaMotionValidationError,
    JakaSourcePositionChangedError,
)
from gripper_ai_controller.configuration import JakaControlSettings
from gripper_ai_controller.domain.models import (
    JointMoveMode,
    Pose3D,
    RobotAction,
    RobotCommand,
    RobotStatus,
)
from gripper_ai_controller.services.safety import SafetyPolicy


JakaOperatorAdapter = Union[JakaAdapter, JakaDryRunRobotAdapter]


class JakaControlError(RuntimeError):
    """Base class for manual JAKA failures with stable browser-facing error codes."""

    code = "jaka_control_failed"

    def __init__(self, message: str) -> None:
        """Store one presentation-safe failure without leaking vendor exception details."""

        super().__init__(message)
        self.message = message


class JakaControlsDisabledError(JakaControlError):
    """Reject manual operations unless local configuration explicitly enables them."""

    code = "jaka_controls_disabled"


class JakaNotArmedError(JakaControlError):
    """Reject enable, preview, and motion when no current browser token exists."""

    code = "jaka_not_armed"


class JakaControlConflictError(JakaControlError):
    """Report an operation that conflicts with telemetry or manual-control state."""

    code = "jaka_state_conflict"


class JakaBusyError(JakaControlConflictError):
    """Reject a second independent operation instead of queuing physical work."""

    code = "jaka_busy"


class JakaCapabilityError(JakaControlConflictError):
    """Report a locally disabled JAKA capability before any SDK invocation."""

    code = "jaka_capability_unavailable"


class JakaIdempotencyConflictError(JakaControlConflictError):
    """Reject reuse of one idempotency key for a different manual operation."""

    code = "idempotency_key_conflict"


class JakaControlValidationError(JakaControlError):
    """Report malformed joint drafts or expired previews before hardware dispatch."""

    code = "invalid_jaka_command"


class JakaUnavailableError(JakaControlError):
    """Normalize connection and SDK failures for browser clients."""

    code = "jaka_unavailable"


@dataclass(frozen=True)
class ManualJakaStatus:
    """Browser-safe state and static motion bounds for one selected JAKA target."""

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
    joint_positions_rad: Tuple[float, float, float, float, float, float]
    joint_lower_limits_rad: Tuple[float, float, float, float, float, float]
    joint_upper_limits_rad: Tuple[float, float, float, float, float, float]
    maximum_joint_speed_rad_per_second: float
    maximum_joint_step_rad: float
    armed_until: Optional[float]
    last_error: Optional[str]


@dataclass(frozen=True)
class JakaArmSession:
    """One short-lived browser authorization and the status that issued it."""

    token: str
    expires_at: float
    status: ManualJakaStatus


@dataclass(frozen=True)
class JakaMotionPreview:
    """One server-side stored, non-executing absolute joint-motion preview."""

    preview_id: str
    expires_at: float
    source_joint_positions_rad: Tuple[float, float, float, float, float, float]
    target_joint_positions_rad: Tuple[float, float, float, float, float, float]
    joint_deltas_rad: Tuple[float, float, float, float, float, float]
    estimated_duration_seconds: float
    status: ManualJakaStatus


@dataclass(frozen=True)
class ManualJakaOperationResult:
    """One idempotent enable or joint-move outcome."""

    idempotency_key: str
    replayed: bool
    status: ManualJakaStatus


@dataclass(frozen=True)
class _StoredPreview:
    """Server-only preview state bound to the token that created it."""

    preview: JakaMotionPreview
    token: str
    command: RobotCommand


@dataclass(frozen=True)
class _IdempotencyOutcome:
    """A bounded success or normalized failure retained to prevent duplicate commands."""

    expires_at: float
    signature: Tuple[Any, ...]
    status: Optional[ManualJakaStatus]
    error: Optional[JakaControlError]


@dataclass(frozen=True)
class _InFlightOperation:
    """An active operation shared only by an identical idempotent request."""

    signature: Tuple[Any, ...]
    task: asyncio.Task


class ManualJakaControlService:
    """Facade for explicit human JAKA operations outside Runtime and planner dispatch.

    The facade starts only the selected adapter and reads status. It never calls
    ``power_on``. A physical move can occur only after local enablement, a temporary
    in-memory browser token, a fresh server-side preview, and a second command request.
    Normal ``RobotAdapter.execute`` remains unavailable to this service and to Runtime.
    """

    def __init__(
        self,
        robot: JakaOperatorAdapter,
        safety_policy: SafetyPolicy,
        settings: JakaControlSettings,
        controls_enabled: bool = False,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        preview_id_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        """Bind one JAKA adapter and local browser policy without starting hardware."""

        self.robot = robot
        self.safety_policy = safety_policy
        self.settings = settings
        self.controls_enabled = bool(controls_enabled)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._token_factory = token_factory
        self._preview_id_factory = preview_id_factory
        self._arm_token = None  # type: Optional[str]
        self._arm_expires_at = None  # type: Optional[float]
        self._active_preview = None  # type: Optional[_StoredPreview]
        self._last_status = RobotStatus(
            initialized=False,
            joint_positions_rad=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            tcp_pose=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "robot_base"),
            connected=False,
            powered=False,
            enabled=False,
        )
        self._last_status_at = self._wall_clock()
        self._last_error = None  # type: Optional[str]
        self._started = False
        # Python 3.7 binds asyncio primitives to the active loop. FastAPI creates
        # the application earlier, so locks are initialized lazily under that loop.
        self._command_lock = None  # type: Optional[Any]
        self._idempotency_lock = None  # type: Optional[Any]
        self._idempotency_cache = OrderedDict()  # type: OrderedDict[str, _IdempotencyOutcome]
        self._in_flight = {}  # type: Dict[str, _InFlightOperation]

    @property
    def robot_id(self) -> str:
        """Return the configured primary target identifier used by HTTP paths."""

        return self.settings.target_name

    async def startup(self) -> None:
        """Start and read the selected JAKA adapter without enabling or moving it."""

        if self._started:
            return
        self._started = True
        self._clear_arm()
        try:
            await self.robot.startup()
            self._record_status(await self.robot.initialize())
        except Exception:
            self._last_error = "The configured JAKA robot is unavailable."
            self._last_status = replace(
                self._last_status,
                initialized=False,
                connected=False,
                powered=False,
                enabled=False,
            )
            self._last_status_at = self._wall_clock()

    async def shutdown(self) -> None:
        """Revoke authorization and release the selected adapter without changing power."""

        self._clear_arm()
        self._active_preview = None
        self._idempotency_cache.clear()
        self._in_flight.clear()
        if not self._started:
            return
        self._started = False
        try:
            await self.robot.shutdown()
        except Exception:
            # Authorization is already revoked; shutdown must not prevent ASGI exit.
            pass

    async def status(self) -> ManualJakaStatus:
        """Read current telemetry without enabling or sending a motion command."""

        return self._public_status(await self._read_status(allow_unavailable=True))

    async def arm(self, work_area_clear: bool, emergency_stop_ready: bool) -> JakaArmSession:
        """Issue a temporary token only after both physical safety checks are confirmed."""

        self._require_controls_enabled()
        self._require_manual_motion_enabled()
        if not work_area_clear or not emergency_stop_ready:
            raise JakaControlValidationError(
                "Work-area clearance and an available independent emergency stop must both be confirmed."
            )
        status = await self._read_status()
        if not status.connected or not status.initialized:
            raise JakaControlConflictError("JAKA must be connected before control is unlocked.")
        if status.faulted or status.emergency_stopped:
            raise JakaControlConflictError("JAKA fault or emergency-stop state prevents control.")
        if status.moving:
            raise JakaControlConflictError("JAKA must report in-position before control is unlocked.")
        token = self._token_factory()
        if not token:
            raise JakaUnavailableError("A temporary JAKA control token could not be created.")
        self._arm_token = token
        self._arm_expires_at = self._monotonic_clock() + self.settings.arm_timeout_seconds
        self._active_preview = None
        public_status = self._public_status(status)
        return JakaArmSession(token, public_status.armed_until or self._wall_clock(), public_status)

    async def disarm(self, token: Optional[str]) -> None:
        """Revoke the current browser authorization without sending an SDK operation."""

        active_token = self._active_arm_token()
        if active_token is None:
            self._clear_arm()
            return
        if token != active_token:
            raise JakaNotArmedError("The JAKA control token is missing, invalid, or expired.")
        self._clear_arm()

    async def reconnect(self) -> ManualJakaStatus:
        """Reconnect and read status without enabling, powering, or moving the robot."""

        self._require_controls_enabled()
        self._clear_arm()
        async with self._exclusive_operation():
            try:
                self._record_status(await self.robot.reconnect())
            except Exception as error:
                self._last_error = "The configured JAKA robot could not be reconnected."
                raise JakaUnavailableError(self._last_error) from error
        return self._public_status(self._last_status)

    async def power_on(
        self, token: Optional[str], idempotency_key: str
    ) -> ManualJakaOperationResult:
        """Power on the controller once after explicit local authorization.

        Remote power-on requires allow_remote_power_on=True in local configuration.
        This should only be enabled in controlled environments where the operator
        can verify the work area is clear and safe.
        """

        self._require_controls_enabled()
        self._require_manual_motion_enabled()
        self._require_arm_token(token)

        async def operation() -> ManualJakaStatus:
            async with self._exclusive_operation():
                status = await self._read_status()
                if not status.connected or not status.initialized:
                    raise JakaControlConflictError("JAKA must be connected before power-on.")
                if status.faulted or status.emergency_stopped:
                    raise JakaControlConflictError("JAKA fault or emergency-stop state prevents power-on.")
                try:
                    self._record_status(await self.robot.operator_power_on())
                except PermissionError as error:
                    raise JakaCapabilityError("JAKA remote power-on is disabled by local configuration.") from error
                except Exception as error:
                    self._clear_arm()
                    self._last_error = "JAKA power-on failed."
                    raise JakaUnavailableError(self._last_error) from error
                if not self._last_status.powered:
                    self._clear_arm()
                    raise JakaControlConflictError("JAKA did not report powered state after power-on.")
                self._refresh_arm(token)
                return self._public_status(self._last_status)

        return await self._run_idempotent(idempotency_key, ("power_on",), operation)

    async def enable(
        self, token: Optional[str], idempotency_key: str
    ) -> ManualJakaOperationResult:
        """Enable already-powered servos once after explicit local authorization."""

        self._require_controls_enabled()
        self._require_manual_motion_enabled()
        self._require_arm_token(token)

        async def operation() -> ManualJakaStatus:
            async with self._exclusive_operation():
                status = await self._read_status()
                if not status.connected or not status.initialized:
                    raise JakaControlConflictError("JAKA must be connected before servo enable.")
                if status.faulted or status.emergency_stopped:
                    raise JakaControlConflictError("JAKA fault or emergency-stop state prevents servo enable.")
                if status.moving:
                    raise JakaControlConflictError("JAKA must report in-position before servo enable.")
                if not status.powered:
                    raise JakaControlConflictError(
                        "JAKA is not powered. Automatic power-on is intentionally unavailable."
                    )
                try:
                    self._record_status(await self.robot.operator_enable())
                except PermissionError as error:
                    raise JakaCapabilityError("JAKA servo enable is disabled by local configuration.") from error
                except Exception as error:
                    self._clear_arm()
                    self._last_error = "JAKA servo enable failed."
                    raise JakaUnavailableError(self._last_error) from error
                if not self._last_status.enabled:
                    self._clear_arm()
                    raise JakaControlConflictError("JAKA did not report enabled servos after enable.")
                self._refresh_arm(token)
                return self._public_status(self._last_status)

        return await self._run_idempotent(idempotency_key, ("enable",), operation)

    async def preview_joint_move(
        self,
        token: Optional[str],
        joint_positions_rad: object,
        speed_rad_per_second: object,
    ) -> JakaMotionPreview:
        """Compile one fresh absolute six-axis draft without communicating a motion."""

        self._require_controls_enabled()
        self._require_manual_motion_enabled()
        self._require_arm_token(token)
        command = self._build_absolute_command(joint_positions_rad, speed_rad_per_second)
        async with self._exclusive_operation():
            status = await self._read_status()
            decision = self.safety_policy.evaluate_manual_robot(command, status)
            if not decision.allowed:
                raise JakaControlConflictError(decision.reason)
            try:
                compiled = self.robot.motion_compiler.compile(command, status)
            except JakaMotionValidationError as error:
                raise JakaControlValidationError(str(error)) from error
            preview_id = self._preview_id_factory()
            if not preview_id:
                raise JakaUnavailableError("A JAKA motion preview identifier could not be created.")
            expires_at = self._wall_clock() + self.settings.preview_timeout_seconds
            preview = self._public_preview(preview_id, expires_at, compiled, status)
            self._active_preview = _StoredPreview(preview, token or "", command)
            return preview

    async def execute_preview(
        self,
        token: Optional[str],
        idempotency_key: str,
        preview_id: str,
    ) -> ManualJakaOperationResult:
        """Execute one stored preview once after fresh telemetry revalidation."""

        self._require_controls_enabled()
        self._require_manual_motion_enabled()
        self._require_arm_token(token)
        normalized_preview_id = preview_id.strip() if isinstance(preview_id, str) else ""
        if not normalized_preview_id:
            raise JakaControlValidationError("preview_id is required for a JAKA joint move.")

        async def operation() -> ManualJakaStatus:
            async with self._exclusive_operation():
                stored = self._require_active_preview(normalized_preview_id, token)
                status = await self._read_status()
                if self._source_position_changed(stored.preview, status):
                    self._active_preview = None
                    raise JakaControlConflictError(
                        "JAKA joint telemetry changed after the preview; create a new preview before moving."
                    )
                decision = self.safety_policy.evaluate_manual_robot(stored.command, status)
                if not decision.allowed:
                    raise JakaControlConflictError(decision.reason)
                try:
                    self.robot.motion_compiler.compile(stored.command, status)
                except JakaMotionValidationError as error:
                    raise JakaControlValidationError(str(error)) from error
                try:
                    self._record_status(
                        await self.robot.operator_joint_move(
                            stored.command,
                            stored.preview.source_joint_positions_rad,
                            self.settings.source_position_tolerance_rad,
                        )
                    )
                except JakaSourcePositionChangedError as error:
                    self._active_preview = None
                    raise JakaControlConflictError(str(error)) from error
                except PermissionError as error:
                    self._clear_arm()
                    raise JakaCapabilityError("JAKA manual motion is disabled by local configuration.") from error
                except Exception as error:
                    self._clear_arm()
                    self._last_error = "The JAKA joint move did not complete successfully."
                    raise JakaUnavailableError(self._last_error) from error
                self._active_preview = None
                self._refresh_arm(token)
                return self._public_status(self._last_status)

        return await self._run_idempotent(
            idempotency_key,
            ("joint_move", normalized_preview_id),
            operation,
        )

    def _build_absolute_command(
        self, joint_positions_rad: object, speed_rad_per_second: object
    ) -> RobotCommand:
        """Normalize the only browser-supported motion form into the domain command."""

        if not isinstance(joint_positions_rad, (list, tuple)) or len(joint_positions_rad) != 6:
            raise JakaControlValidationError("joint_positions_rad must contain exactly six values.")
        positions = []
        for index, value in enumerate(joint_positions_rad, start=1):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JakaControlValidationError("J{0} must be a finite numeric radian value.".format(index))
            normalized = float(value)
            if not math.isfinite(normalized):
                raise JakaControlValidationError("J{0} must be a finite numeric radian value.".format(index))
            positions.append(normalized)
        if isinstance(speed_rad_per_second, bool) or not isinstance(
            speed_rad_per_second, (int, float)
        ):
            raise JakaControlValidationError("speed_rad_per_second must be a finite numeric value.")
        speed = float(speed_rad_per_second)
        if not math.isfinite(speed) or speed <= 0.0:
            raise JakaControlValidationError("speed_rad_per_second must be greater than zero.")
        return RobotCommand(
            RobotAction.MOVE_JOINTS,
            tuple(positions),
            speed=speed,
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )

    async def _read_status(self, allow_unavailable: bool = False) -> RobotStatus:
        """Read live telemetry and revoke command authority after unsafe state changes."""

        try:
            status = await self.robot.get_status()
        except Exception as error:
            self._clear_arm()
            self._last_error = "The configured JAKA robot status is unavailable."
            self._last_status = replace(
                self._last_status,
                initialized=False,
                connected=False,
                powered=False,
                enabled=False,
            )
            self._last_status_at = self._wall_clock()
            if allow_unavailable:
                return self._last_status
            raise JakaUnavailableError(self._last_error) from error
        self._record_status(status)
        return status

    def _record_status(self, status: RobotStatus) -> None:
        """Retain one validated adapter status and revoke stale browser authority."""

        if len(status.joint_positions_rad) != 6:
            self._clear_arm()
            raise JakaUnavailableError("JAKA status did not provide six joint positions.")
        previous_status = self._last_status
        self._last_status = status
        self._last_status_at = self._wall_clock()
        if status.connected:
            self._last_error = None
        if self._status_requires_reauthorization(previous_status, status):
            self._clear_arm()

    @staticmethod
    def _status_requires_reauthorization(
        previous_status: RobotStatus, status: RobotStatus
    ) -> bool:
        """Return whether a telemetry transition invalidates an operator's prior confirmation."""

        if (
            not status.connected
            or status.faulted
            or status.emergency_stopped
            or status.moving
        ):
            return True
        return (
            (previous_status.initialized and not status.initialized)
            or (previous_status.powered and not status.powered)
            or (previous_status.enabled and not status.enabled)
        )

    def _public_status(self, status: RobotStatus) -> ManualJakaStatus:
        """Combine current telemetry, local gates, and compiler limits for the UI."""

        positions = tuple(float(value) for value in status.joint_positions_rad)
        if len(positions) != 6:
            raise JakaUnavailableError("JAKA status did not provide six joint positions.")
        compiler = self.robot.motion_compiler
        return ManualJakaStatus(
            robot_id=self.robot_id,
            mode=self.robot.control_mode,
            controls_enabled=self.controls_enabled,
            manual_motion_enabled=self.robot.manual_motion_enabled,
            enable_permitted=(
                True if isinstance(self.robot, JakaDryRunRobotAdapter) else self.robot.allow_enable
            ),
            connected=status.connected,
            powered=status.powered,
            enabled=status.enabled,
            moving=status.moving,
            faulted=status.faulted,
            emergency_stopped=status.emergency_stopped,
            captured_at=self._last_status_at,
            joint_positions_rad=positions,  # type: ignore[arg-type]
            joint_lower_limits_rad=compiler.joint_lower_limits_rad,
            joint_upper_limits_rad=compiler.joint_upper_limits_rad,
            maximum_joint_speed_rad_per_second=compiler.maximum_joint_speed_rad_per_second,
            maximum_joint_step_rad=compiler.maximum_joint_step_rad,
            armed_until=self._public_arm_expiry(),
            last_error=self._last_error,
        )

    def _public_preview(
        self,
        preview_id: str,
        expires_at: float,
        compiled: JakaJointMovePreview,
        status: RobotStatus,
    ) -> JakaMotionPreview:
        """Serialize compiler output without exposing vendor method names or arguments."""

        return JakaMotionPreview(
            preview_id=preview_id,
            expires_at=expires_at,
            source_joint_positions_rad=compiled.source_joint_positions_rad,
            target_joint_positions_rad=compiled.target_joint_positions_rad,
            joint_deltas_rad=compiled.joint_deltas_rad,
            estimated_duration_seconds=compiled.estimated_duration_seconds,
            status=self._public_status(status),
        )

    def _require_active_preview(self, preview_id: str, token: Optional[str]) -> _StoredPreview:
        """Return the current matching preview or require another explicit confirmation."""

        stored = self._active_preview
        if stored is None or stored.preview.preview_id != preview_id or stored.token != token:
            raise JakaControlConflictError("The JAKA motion preview is missing or belongs to another session.")
        if self._wall_clock() >= stored.preview.expires_at:
            self._active_preview = None
            raise JakaControlConflictError("The JAKA motion preview expired; create a new preview.")
        return stored

    def _source_position_changed(self, preview: JakaMotionPreview, status: RobotStatus) -> bool:
        """Reject a stale preview when any current joint changed beyond the configured tolerance."""

        return any(
            abs(current - source) > self.settings.source_position_tolerance_rad
            for current, source in zip(status.joint_positions_rad, preview.source_joint_positions_rad)
        )

    async def _run_idempotent(
        self,
        key: str,
        signature: Tuple[Any, ...],
        operation: Callable[[], Awaitable[ManualJakaStatus]],
    ) -> ManualJakaOperationResult:
        """Share duplicate work and retain bounded outcomes without reissuing hardware actions."""

        normalized_key = self._validate_idempotency_key(key)
        replayed = False
        async with self._idempotency_lock_for_current_loop():
            self._expire_idempotency_cache()
            cached = self._idempotency_cache.get(normalized_key)
            if cached is not None:
                if cached.signature != signature:
                    raise JakaIdempotencyConflictError(
                        "The idempotency key was already used for a different operation."
                    )
                self._idempotency_cache.move_to_end(normalized_key)
                if cached.error is not None:
                    raise cached.error
                return ManualJakaOperationResult(
                    normalized_key, True, cached.status  # type: ignore[arg-type]
                )
            in_flight = self._in_flight.get(normalized_key)
            if in_flight is not None:
                if in_flight.signature != signature:
                    raise JakaIdempotencyConflictError(
                        "The idempotency key is active for a different operation."
                    )
                task = in_flight.task
                replayed = True
            else:
                task = asyncio.create_task(
                    self._perform_idempotent(normalized_key, signature, operation)
                )
                self._in_flight[normalized_key] = _InFlightOperation(signature, task)
        # A request cancellation must not cancel a blocking physical move or release
        # the service-level operation lock before the adapter finishes its SDK call.
        status = await asyncio.shield(task)
        return ManualJakaOperationResult(normalized_key, replayed, status)

    async def _perform_idempotent(
        self,
        key: str,
        signature: Tuple[Any, ...],
        operation: Callable[[], Awaitable[ManualJakaStatus]],
    ) -> ManualJakaStatus:
        """Execute once and cache either the normalized status or its public error."""

        status = None  # type: Optional[ManualJakaStatus]
        error = None  # type: Optional[JakaControlError]
        try:
            status = await operation()
        except JakaControlError as operation_error:
            error = operation_error
        except Exception as operation_error:
            error = JakaUnavailableError("The JAKA operation failed unexpectedly.")
            error.__cause__ = operation_error
        outcome = _IdempotencyOutcome(
            self._monotonic_clock() + self.settings.idempotency_ttl_seconds,
            signature,
            status,
            error,
        )
        async with self._idempotency_lock_for_current_loop():
            self._idempotency_cache[key] = outcome
            self._idempotency_cache.move_to_end(key)
            while len(self._idempotency_cache) > self.settings.idempotency_cache_size:
                self._idempotency_cache.popitem(last=False)
            active = self._in_flight.get(key)
            if active is not None and active.task is asyncio.current_task():
                self._in_flight.pop(key, None)
        if error is not None:
            raise error
        return status  # type: ignore[return-value]

    @staticmethod
    def _validate_idempotency_key(key: str) -> str:
        """Accept one compact caller-generated key for a bounded memory cache."""

        normalized = key.strip() if isinstance(key, str) else ""
        if not normalized or len(normalized) > 128:
            raise JakaControlValidationError(
                "Idempotency-Key must contain between 1 and 128 characters."
            )
        return normalized

    def _expire_idempotency_cache(self) -> None:
        """Discard expired outcomes before lookup or insertion."""

        now = self._monotonic_clock()
        expired = [key for key, outcome in self._idempotency_cache.items() if outcome.expires_at <= now]
        for key in expired:
            self._idempotency_cache.pop(key, None)

    def _require_controls_enabled(self) -> None:
        """Fail closed unless local configuration enables browser JAKA control."""

        if not self.controls_enabled:
            raise JakaControlsDisabledError(
                "JAKA controls are disabled by the local configuration."
            )

    def _require_manual_motion_enabled(self) -> None:
        """Reject browser arming when the selected adapter remains read-only."""

        if not self.robot.manual_motion_enabled:
            raise JakaCapabilityError(
                "JAKA manual motion is disabled by the selected local adapter configuration."
            )

    def _require_arm_token(self, token: Optional[str]) -> None:
        """Authorize only the currently active, unexpired browser token."""

        if token is None or token != self._active_arm_token():
            raise JakaNotArmedError("The JAKA control token is missing, invalid, or expired.")

    def _active_arm_token(self) -> Optional[str]:
        """Return the current token and revoke it immediately after inactivity expiry."""

        if self._arm_token is None or self._arm_expires_at is None:
            return None
        if self._monotonic_clock() >= self._arm_expires_at:
            self._clear_arm()
            return None
        return self._arm_token

    def _refresh_arm(self, token: Optional[str]) -> None:
        """Extend inactivity only after a successful enable or completed joint move."""

        if token is not None and token == self._arm_token:
            self._arm_expires_at = self._monotonic_clock() + self.settings.arm_timeout_seconds

    def _public_arm_expiry(self) -> Optional[float]:
        """Convert the monotonic deadline into a browser-readable Unix timestamp."""

        if self._active_arm_token() is None or self._arm_expires_at is None:
            return None
        remaining = max(0.0, self._arm_expires_at - self._monotonic_clock())
        return self._wall_clock() + remaining

    def _clear_arm(self) -> None:
        """Revoke all motion authority and stale previews without contacting hardware."""

        self._arm_token = None
        self._arm_expires_at = None
        self._active_preview = None

    class _ExclusiveOperation:
        """Async guard that rejects independent operations rather than queuing them."""

        def __init__(self, lock: Any) -> None:
            self._lock = lock

        async def __aenter__(self) -> None:
            if self._lock.locked():
                raise JakaBusyError("Another JAKA operation is already active.")
            await self._lock.acquire()

        async def __aexit__(self, error_type, error, traceback) -> None:
            del error_type, error, traceback
            self._lock.release()

    def _exclusive_operation(self) -> "ManualJakaControlService._ExclusiveOperation":
        """Return the one-command guard bound to the current event loop."""

        return self._ExclusiveOperation(self._command_lock_for_current_loop())

    def _command_lock_for_current_loop(self) -> Any:
        """Create the operation lock lazily under FastAPI's active event loop."""

        if self._command_lock is None:
            self._command_lock = asyncio.Lock()
        return self._command_lock

    def _idempotency_lock_for_current_loop(self) -> Any:
        """Create the idempotency lock lazily under FastAPI's active event loop."""

        if self._idempotency_lock is None:
            self._idempotency_lock = asyncio.Lock()
        return self._idempotency_lock
