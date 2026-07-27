"""Safety-gated manual gripper control independent from the autonomous runtime."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from gripper_ai_controller.configuration import GripperControlSettings
from gripper_ai_controller.domain.models import GripperAction, GripperCommand, GripperStatus
from gripper_ai_controller.domain.ports import OperatorControllableGripper
from gripper_ai_controller.services.safety import SafetyPolicy


# Allow a completed adapter-side timeout to publish its final safe status first.
INITIALIZATION_TIMEOUT_GRACE_SECONDS = 0.25


class GripperControlError(RuntimeError):
    """Base class for failures that have a stable HTTP-facing code and message."""

    code = "gripper_control_failed"

    def __init__(self, message: str) -> None:
        """Store one presentation-safe message without exposing adapter internals."""

        super().__init__(message)
        self.message = message


class GripperControlsDisabledError(GripperControlError):
    """Reject operations when local configuration has not enabled physical control."""

    code = "gripper_controls_disabled"


class GripperNotArmedError(GripperControlError):
    """Reject initialization or movement without the current temporary token."""

    code = "gripper_not_armed"


class GripperControlConflictError(GripperControlError):
    """Report a request that conflicts with live gripper or service state."""

    code = "gripper_state_conflict"


class GripperBusyError(GripperControlConflictError):
    """Reject a second independent operation while a device operation is active."""

    code = "gripper_busy"


class GripperCapabilityError(GripperControlConflictError):
    """Report a command unsupported by the selected adapter transport."""

    code = "gripper_capability_unavailable"


class GripperIdempotencyConflictError(GripperControlConflictError):
    """Reject reuse of one idempotency key for a different physical operation."""

    code = "idempotency_key_conflict"


class GripperControlValidationError(GripperControlError):
    """Report invalid manual command parameters before adapter dispatch."""

    code = "invalid_gripper_command"


class GripperUnavailableError(GripperControlError):
    """Normalize adapter communication failures for browser clients."""

    code = "gripper_unavailable"


@dataclass(frozen=True)
class ManualGripperStatus:
    """Browser-safe state for the one explicitly configured manual gripper."""

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


@dataclass(frozen=True)
class GripperArmSession:
    """One temporary browser control capability and its current device status."""

    token: str
    expires_at: float
    status: ManualGripperStatus


@dataclass(frozen=True)
class ManualGripperOperationResult:
    """Result of one idempotent initialization or movement request."""

    idempotency_key: str
    replayed: bool
    status: ManualGripperStatus


@dataclass(frozen=True)
class _IdempotencyOutcome:
    """A bounded success or normalized error retained to prevent repeated motion."""

    expires_at: float
    signature: Tuple[Any, ...]
    status: Optional[ManualGripperStatus]
    error: Optional[GripperControlError]


@dataclass(frozen=True)
class _InFlightOperation:
    """An active operation shared only by duplicate requests with the same signature."""

    signature: Tuple[Any, ...]
    task: asyncio.Task


class ManualGripperControlService:
    """Facade for one operator-controlled gripper with fail-closed safety gates.

    This service deliberately does not construct ``Runtime``, a robot adapter, a
    vision adapter, or a planner. It serializes the selected gripper directly only
    after a short-lived browser token and the core ``SafetyPolicy`` both authorize
    the normalized command.
    """

    def __init__(
        self,
        gripper: OperatorControllableGripper,
        safety_policy: SafetyPolicy,
        settings: GripperControlSettings,
        controls_enabled: bool = False,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        """Bind one adapter, deterministic policy, and validated local settings."""

        self.gripper = gripper
        self.safety_policy = safety_policy
        self.settings = settings
        self.controls_enabled = bool(controls_enabled)
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._token_factory = token_factory
        self._arm_token = None  # type: Optional[str]
        self._arm_expires_at = None  # type: Optional[float]
        self._last_status = GripperStatus(
            initialized=False,
            position=0,
            gripping=False,
            connected=False,
            initializing=False,
            moving=False,
            grip_state="unavailable",
            last_error="The configured gripper has not connected yet.",
            position_is_feedback=gripper.control_mode != "physical",
        )
        self._last_error = None  # type: Optional[str]
        self._started = False
        # Locks are created lazily because Python 3.7 binds asyncio primitives to
        # the active loop, while FastAPI applications are assembled before startup.
        self._command_lock = None  # type: Optional[Any]
        self._idempotency_lock = None  # type: Optional[Any]
        self._idempotency_cache = OrderedDict()  # type: OrderedDict[str, _IdempotencyOutcome]
        self._in_flight = {}  # type: Dict[str, _InFlightOperation]

    @property
    def gripper_id(self) -> str:
        """Return the stable configured identifier used by HTTP resource paths."""

        return self.settings.target_name

    async def startup(self) -> None:
        """Start only the selected adapter without initialization or motion.

        Connection failure remains recoverable through the status and reconnect
        endpoints, so a missing physical gripper does not take down camera preview.
        """

        if self._started:
            return
        self._started = True
        self._clear_arm()
        try:
            await self.gripper.startup()
            await self._read_status(allow_unavailable=True)
        except Exception:
            self._last_error = "The configured gripper is unavailable."
            self._last_status = replace(
                self._last_status,
                connected=False,
                moving=False,
                initializing=False,
                last_error=self._last_error,
            )

    async def shutdown(self) -> None:
        """Revoke browser control before releasing the selected adapter."""

        self._clear_arm()
        self._idempotency_cache.clear()
        self._in_flight.clear()
        if not self._started:
            return
        self._started = False
        try:
            await self.gripper.shutdown()
        except Exception:
            # Shutdown remains best-effort because authorization has already been
            # revoked and the ASGI process must still be able to terminate cleanly.
            pass

    async def status(self) -> ManualGripperStatus:
        """Read live adapter state and expose current temporary authorization."""

        status = await self._read_status(allow_unavailable=True)
        return self._public_status(status)

    async def arm(
        self, work_area_clear: bool, emergency_stop_ready: bool
    ) -> GripperArmSession:
        """Create a temporary token only after explicit operator safety confirmation."""

        self._require_controls_enabled()
        if not work_area_clear or not emergency_stop_ready:
            raise GripperControlValidationError(
                "Work-area clearance and an available independent emergency stop must both be confirmed."
            )
        status = await self._read_status()
        if not status.connected:
            raise GripperControlConflictError("Gripper must be connected before control is unlocked.")
        if status.faulted or status.emergency_stopped:
            raise GripperControlConflictError("Gripper fault or emergency-stop state prevents control.")
        if status.initializing or status.moving:
            raise GripperControlConflictError("Gripper must be idle before control is unlocked.")
        token = self._token_factory()
        if not token:
            raise GripperUnavailableError("A temporary gripper control token could not be created.")
        self._arm_token = token
        self._arm_expires_at = self._monotonic_clock() + self.settings.arm_timeout_seconds
        public_status = self._public_status(status)
        return GripperArmSession(token, public_status.armed_until or self._wall_clock(), public_status)

    async def disarm(self, token: Optional[str]) -> None:
        """Revoke the current token without sending any device command."""

        active_token = self._active_arm_token()
        if active_token is None:
            self._clear_arm()
            return
        if token != active_token:
            raise GripperNotArmedError("The gripper control token is missing, invalid, or expired.")
        self._clear_arm()

    async def reconnect(self) -> ManualGripperStatus:
        """Reconnect the adapter after revoking control, without initializing or moving."""

        self._require_controls_enabled()
        self._clear_arm()
        async with self._exclusive_operation():
            try:
                await self.gripper.reconnect()
                status = await self._read_status()
            except GripperControlError:
                raise
            except Exception as error:
                self._last_error = "The configured gripper could not be reconnected."
                raise GripperUnavailableError(self._last_error) from error
        return self._public_status(status)

    async def initialize(
        self, token: Optional[str], idempotency_key: str
    ) -> ManualGripperOperationResult:
        """Run ordinary operator initialization once for one idempotency key."""

        self._require_controls_enabled()
        self._require_arm_token(token)

        async def operation() -> ManualGripperStatus:
            async with self._exclusive_operation():
                status = await self._read_status()
                if not status.connected:
                    raise GripperControlConflictError(
                        "Gripper must be connected before initialization."
                    )
                if status.faulted or status.emergency_stopped:
                    raise GripperControlConflictError(
                        "Gripper fault or emergency-stop state prevents initialization."
                    )
                if status.moving or status.initializing:
                    raise GripperBusyError("Gripper is already moving or initializing.")
                try:
                    status = await asyncio.wait_for(
                        self.gripper.operator_initialize(),
                        timeout=(
                            self.settings.initialization_timeout_seconds
                            + INITIALIZATION_TIMEOUT_GRACE_SECONDS
                        ),
                    )
                except asyncio.TimeoutError as error:
                    self._clear_arm()
                    self._last_error = "Gripper initialization timed out."
                    raise GripperUnavailableError(self._last_error) from error
                except Exception as error:
                    self._clear_arm()
                    self._last_error = "Gripper initialization failed."
                    raise GripperUnavailableError(self._last_error) from error
                self._last_status = status
                if not status.initialized or status.initializing:
                    self._clear_arm()
                    raise GripperControlConflictError(
                        "Gripper did not report successful initialization."
                    )
                self._last_error = None
                self._refresh_arm(token)
                return self._public_status(status)

        return await self._run_idempotent(
            idempotency_key,
            ("initialize",),
            operation,
        )

    async def execute_command(
        self,
        token: Optional[str],
        idempotency_key: str,
        action: str,
        target_position: Optional[int] = None,
        force_percent: Optional[int] = None,
        speed_percent: Optional[int] = None,
    ) -> ManualGripperOperationResult:
        """Validate, authorize, and execute one explicit browser command at most once."""

        self._require_controls_enabled()
        self._require_arm_token(token)
        normalized_action = action.strip().lower()
        signature = (
            "command",
            normalized_action,
            target_position,
            force_percent,
            speed_percent,
        )

        async def operation() -> ManualGripperStatus:
            command = self._build_command(
                normalized_action,
                target_position,
                force_percent,
                speed_percent,
            )
            async with self._exclusive_operation():
                status = await self._read_status()
                if not status.connected:
                    self._clear_arm()
                    raise GripperControlConflictError(
                        "Gripper must be connected before a command is executed."
                    )
                decision = self.safety_policy.evaluate_gripper(command, status)
                if not decision.allowed:
                    raise GripperControlConflictError(decision.reason)
                try:
                    status = await self.gripper.execute(command)
                except Exception as error:
                    self._clear_arm()
                    self._last_error = (
                        "The gripper command result is uncertain. Inspect the gripper before retrying."
                    )
                    raise GripperUnavailableError(self._last_error) from error
                self._last_status = status
                self._last_error = status.last_error
                if not status.connected:
                    self._clear_arm()
                    raise GripperUnavailableError("The gripper disconnected during command execution.")
                self._refresh_arm(token)
                return self._public_status(status)

        return await self._run_idempotent(idempotency_key, signature, operation)

    def _build_command(
        self,
        action: str,
        target_position: Optional[int],
        force_percent: Optional[int],
        speed_percent: Optional[int],
    ) -> GripperCommand:
        """Map one browser action to a normalized command without vendor values."""

        if action == "open":
            if target_position is not None:
                raise GripperControlValidationError(
                    "Open uses the configured open position and cannot override target_position."
                )
            target_position = self.settings.open_position
            gripper_action = GripperAction.OPEN
        elif action == "close":
            if target_position is not None:
                raise GripperControlValidationError(
                    "Close uses the configured close position and cannot override target_position."
                )
            target_position = self.settings.close_position
            gripper_action = GripperAction.CLOSE
        elif action in ("move_to_position", "position"):
            if target_position is None:
                raise GripperControlValidationError(
                    "A position command requires target_position."
                )
            gripper_action = GripperAction.MOVE_TO_POSITION
        elif action == "stop":
            if any(value is not None for value in (target_position, force_percent, speed_percent)):
                raise GripperControlValidationError(
                    "Stop does not accept position, force, or speed parameters."
                )
            if not self.gripper.supports_stop:
                raise GripperCapabilityError(
                    "The selected gripper transport does not provide a verified stop command."
                )
            return GripperCommand(GripperAction.STOP)
        else:
            raise GripperControlValidationError("The requested gripper action is unsupported.")

        if speed_percent is not None and not self.gripper.supports_speed:
            raise GripperCapabilityError(
                "The selected gripper transport does not provide verified speed control."
            )
        self._validate_range(
            "target_position",
            target_position,
            self.settings.minimum_position,
            self.settings.maximum_position,
        )
        if force_percent is not None:
            self._validate_range(
                "force_percent",
                force_percent,
                self.settings.minimum_force_percent,
                self.settings.maximum_force_percent,
            )
        if speed_percent is not None:
            self._validate_range(
                "speed_percent",
                speed_percent,
                self.settings.minimum_speed_percent,
                self.settings.maximum_speed_percent,
            )
        return GripperCommand(gripper_action, target_position, force_percent, speed_percent)

    @staticmethod
    def _validate_range(name: str, value: int, minimum: int, maximum: int) -> None:
        """Reject booleans and out-of-range integers before core authorization."""

        if isinstance(value, bool) or not isinstance(value, int):
            raise GripperControlValidationError("{0} must be an integer.".format(name))
        if value < minimum or value > maximum:
            raise GripperControlValidationError(
                "{0} must be between {1} and {2}.".format(name, minimum, maximum)
            )

    async def _read_status(self, allow_unavailable: bool = False) -> GripperStatus:
        """Read live adapter state and revoke authorization after disconnect."""

        try:
            status = await self.gripper.get_status()
        except Exception as error:
            self._clear_arm()
            self._last_error = "The configured gripper status is unavailable."
            self._last_status = replace(
                self._last_status,
                connected=False,
                moving=False,
                initializing=False,
                last_error=self._last_error,
            )
            if allow_unavailable:
                return self._last_status
            raise GripperUnavailableError(self._last_error) from error
        self._last_status = status
        if status.last_error is not None:
            self._last_error = status.last_error
        elif status.connected:
            self._last_error = None
        if not status.connected:
            self._clear_arm()
        return status

    def _public_status(self, status: GripperStatus) -> ManualGripperStatus:
        """Combine live feedback, capabilities, limits, and temporary authorization."""

        return ManualGripperStatus(
            gripper_id=self.gripper_id,
            mode=self.gripper.control_mode,
            controls_enabled=self.controls_enabled,
            connected=status.connected,
            initialized=status.initialized,
            initializing=status.initializing,
            moving=status.moving,
            gripping=status.gripping,
            position=status.position,
            position_is_feedback=status.position_is_feedback,
            grip_state=status.grip_state,
            supports_speed=self.gripper.supports_speed,
            supports_stop=self.gripper.supports_stop,
            minimum_position=self.settings.minimum_position,
            maximum_position=self.settings.maximum_position,
            minimum_force_percent=self.settings.minimum_force_percent,
            maximum_force_percent=self.settings.maximum_force_percent,
            minimum_speed_percent=self.settings.minimum_speed_percent,
            maximum_speed_percent=self.settings.maximum_speed_percent,
            open_position=self.settings.open_position,
            close_position=self.settings.close_position,
            armed_until=self._public_arm_expiry(),
            last_error=status.last_error or self._last_error,
        )

    async def _run_idempotent(
        self,
        key: str,
        signature: Tuple[Any, ...],
        operation: Callable[[], Awaitable[ManualGripperStatus]],
    ) -> ManualGripperOperationResult:
        """Share in-flight duplicates and retain bounded outcomes without re-dispatch."""

        normalized_key = self._validate_idempotency_key(key)
        replayed = False
        async with self._idempotency_lock_for_current_loop():
            self._expire_idempotency_cache()
            cached = self._idempotency_cache.get(normalized_key)
            if cached is not None:
                if cached.signature != signature:
                    raise GripperIdempotencyConflictError(
                        "The idempotency key was already used for a different operation."
                    )
                self._idempotency_cache.move_to_end(normalized_key)
                if cached.error is not None:
                    raise cached.error
                return ManualGripperOperationResult(
                    normalized_key, True, cached.status  # type: ignore[arg-type]
                )
            in_flight = self._in_flight.get(normalized_key)
            if in_flight is not None:
                if in_flight.signature != signature:
                    raise GripperIdempotencyConflictError(
                        "The idempotency key is active for a different operation."
                    )
                task = in_flight.task
                replayed = True
            else:
                task = asyncio.create_task(
                    self._perform_idempotent(normalized_key, signature, operation)
                )
                self._in_flight[normalized_key] = _InFlightOperation(signature, task)
        # A disconnected HTTP request must not cancel a physical operation that is
        # still holding the adapter's single-device lock.
        status = await asyncio.shield(task)
        return ManualGripperOperationResult(normalized_key, replayed, status)

    async def _perform_idempotent(
        self,
        key: str,
        signature: Tuple[Any, ...],
        operation: Callable[[], Awaitable[ManualGripperStatus]],
    ) -> ManualGripperStatus:
        """Execute one operation and cache both normalized success and failure outcomes."""

        status = None  # type: Optional[ManualGripperStatus]
        error = None  # type: Optional[GripperControlError]
        try:
            status = await operation()
        except GripperControlError as operation_error:
            error = operation_error
        except Exception as operation_error:
            error = GripperUnavailableError("The gripper operation failed unexpectedly.")
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

    def _expire_idempotency_cache(self) -> None:
        """Discard expired outcomes before lookup or insertion."""

        now = self._monotonic_clock()
        expired = [key for key, outcome in self._idempotency_cache.items() if outcome.expires_at <= now]
        for key in expired:
            self._idempotency_cache.pop(key, None)

    @staticmethod
    def _validate_idempotency_key(key: str) -> str:
        """Accept one compact caller-generated key suitable for a bounded memory cache."""

        normalized = key.strip() if isinstance(key, str) else ""
        if not normalized or len(normalized) > 128:
            raise GripperControlValidationError(
                "Idempotency-Key must contain between 1 and 128 characters."
            )
        return normalized

    def _require_controls_enabled(self) -> None:
        """Fail closed unless the explicit local configuration enables commands."""

        if not self.controls_enabled:
            raise GripperControlsDisabledError(
                "Gripper controls are disabled by the local configuration."
            )

    def _require_arm_token(self, token: Optional[str]) -> None:
        """Authorize only the current unexpired temporary browser token."""

        if token is None or token != self._active_arm_token():
            raise GripperNotArmedError(
                "The gripper control token is missing, invalid, or expired."
            )

    def _active_arm_token(self) -> Optional[str]:
        """Return the current token, revoking it immediately after inactivity."""

        if self._arm_token is None or self._arm_expires_at is None:
            return None
        if self._monotonic_clock() >= self._arm_expires_at:
            self._clear_arm()
            return None
        return self._arm_token

    def _refresh_arm(self, token: Optional[str]) -> None:
        """Extend inactivity expiry only after a successful authorized operation."""

        if token is not None and token == self._arm_token:
            self._arm_expires_at = self._monotonic_clock() + self.settings.arm_timeout_seconds

    def _public_arm_expiry(self) -> Optional[float]:
        """Convert the monotonic deadline to a browser-readable Unix timestamp."""

        if self._active_arm_token() is None or self._arm_expires_at is None:
            return None
        remaining = max(0.0, self._arm_expires_at - self._monotonic_clock())
        return self._wall_clock() + remaining

    def _clear_arm(self) -> None:
        """Revoke all current browser command authority without contacting hardware."""

        self._arm_token = None
        self._arm_expires_at = None

    class _ExclusiveOperation:
        """Async context wrapper that rejects rather than queues independent commands."""

        def __init__(self, lock: Any) -> None:
            self._lock = lock

        async def __aenter__(self) -> None:
            if self._lock.locked():
                raise GripperBusyError("Another gripper operation is already active.")
            await self._lock.acquire()

        async def __aexit__(self, error_type, error, traceback) -> None:
            del error_type, error, traceback
            self._lock.release()

    def _exclusive_operation(self) -> "ManualGripperControlService._ExclusiveOperation":
        """Return the single-operation guard bound to the current event loop."""

        return self._ExclusiveOperation(self._command_lock_for_current_loop())

    def _command_lock_for_current_loop(self) -> Any:
        """Create the device operation lock lazily under the active event loop."""

        if self._command_lock is None:
            self._command_lock = asyncio.Lock()
        return self._command_lock

    def _idempotency_lock_for_current_loop(self) -> Any:
        """Create the idempotency-state lock lazily under the active event loop."""

        if self._idempotency_lock is None:
            self._idempotency_lock = asyncio.Lock()
        return self._idempotency_lock
