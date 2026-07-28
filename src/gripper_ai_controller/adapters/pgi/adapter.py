"""Lifecycle-safe PGI TCP gripper adapter for explicit operator control."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
import time
from typing import Callable, Optional

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.adapters.pgi.client import (
    PgiTcpClient,
    PgiTcpClientError,
    PgiTcpWriteOutcomeUnknownError,
)
from gripper_ai_controller.domain.models import (
    ComponentManifest,
    GripperAction,
    GripperCommand,
    GripperStatus,
)
from gripper_ai_controller.domain.ports import OperatorControllableGripper


class PgiTcpAdapterError(RuntimeError):
    """Report a normalized PGI adapter lifecycle, state, or command failure."""


class PgiTcpOperationOutcomeUnknownError(PgiTcpAdapterError):
    """Report a physical PGI operation whose acknowledgement was not confirmed."""


PgiClientFactory = Callable[[], PgiTcpClient]


class PgiTcpGripperAdapter(BaseAdapter, OperatorControllableGripper):
    """Expose verified PGI TCP operations without implicit physical actions.

    Startup attempts a connection and remains available in a disconnected state when
    the endpoint cannot be reached, allowing a local operator to retry explicitly.
    General ``initialize`` is read-only; only ``operator_initialize`` can request the
    ordinary vendor initialization movement.
    """

    manifest = ComponentManifest(
        "pgi-tcp-gripper",
        "0.1.0",
        "gripper",
        (
            "gripper",
            "pgi",
            "tcp",
            "explicit-initialization",
            "position-control",
            "force-control",
        ),
        "PgiTcpGripperAdapter",
    )

    def __init__(
        self,
        host: str,
        port: int = 8888,
        device_id: int = 1,
        timeout_seconds: float = 1.0,
        retry_count: int = 2,
        initialization_timeout_seconds: float = 5.0,
        initialization_poll_seconds: float = 0.2,
        client_factory: Optional[PgiClientFactory] = None,
    ) -> None:
        """Bind local configuration without opening a socket or moving hardware."""

        super().__init__()
        if type(initialization_timeout_seconds) not in (int, float) or initialization_timeout_seconds <= 0:
            raise ValueError("PGI initialization_timeout_seconds must be greater than zero.")
        if type(initialization_poll_seconds) not in (int, float) or initialization_poll_seconds <= 0:
            raise ValueError("PGI initialization_poll_seconds must be greater than zero.")

        def create_default_client() -> PgiTcpClient:
            """Create the standard-library client only when lifecycle startup begins."""

            return PgiTcpClient(
                host=host,
                port=port,
                device_id=device_id,
                timeout_seconds=timeout_seconds,
                retry_count=retry_count,
            )

        self._client_factory = client_factory or create_default_client
        self._client = None  # type: Optional[PgiTcpClient]
        self._executor = None  # type: Optional[ThreadPoolExecutor]
        # Configuration loading can occur before Python 3.7 creates an event loop.
        # Construct the asyncio lock lazily from the first lifecycle coroutine instead.
        self._operation_lock = None  # type: Optional[asyncio.Lock]
        self._initialization_timeout_seconds = float(initialization_timeout_seconds)
        self._initialization_poll_seconds = float(initialization_poll_seconds)
        self._last_status = GripperStatus(
            initialized=False,
            position=0,
            gripping=False,
            connected=False,
            grip_state="unknown",
            position_is_feedback=False,
        )

    @property
    def control_mode(self) -> str:
        """Identify this adapter as a physical operator-control target."""

        return "physical"

    @property
    def supports_speed(self) -> bool:
        """Return false because the TCP sample does not verify a speed register."""

        return False

    @property
    def supports_stop(self) -> bool:
        """Return false because the TCP sample does not define a software stop."""

        return False

    @property
    def last_status(self) -> GripperStatus:
        """Return the latest successful or degraded status without performing I/O."""

        return self._last_status

    async def on_startup(self) -> None:
        """Create one client, connect, and read telemetry without writing registers."""

        self._client = self._client_factory()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pgi-tcp")
        try:
            await self._call_client("connect")
            await self._read_status_unlocked()
        except PgiTcpClientError as error:
            self._record_error(error)
            return
        except PgiTcpAdapterError as error:
            self._record_error(error)
            return
        self._last_status = replace(self._last_status, connected=True, last_error=None)

    async def on_shutdown(self) -> None:
        """Close the instance connection and release its single native worker."""

        executor = self._executor
        async with self._get_operation_lock():
            try:
                if self._client is not None and executor is not None:
                    await self._call_client("close")
            finally:
                self._client = None
                self._executor = None
                self._last_status = replace(
                    self._last_status,
                    connected=False,
                    initializing=False,
                    moving=False,
                )
                if executor is not None:
                    executor.shutdown(wait=True)

    async def initialize(self) -> GripperStatus:
        """Read current device state without requesting initialization or movement."""

        return await self.get_status()

    async def get_status(self) -> GripperStatus:
        """Read initialization, configured target position, and grip-state registers."""

        self.ensure_started()
        async with self._get_operation_lock():
            try:
                return await self._read_status_unlocked()
            except (PgiTcpClientError, PgiTcpAdapterError) as error:
                self._record_error(error)
                raise PgiTcpAdapterError("Unable to read PGI gripper status: {0}".format(error)) from error

    async def reconnect(self) -> GripperStatus:
        """Replace the TCP connection and read status without initializing or moving."""

        self.ensure_started()
        async with self._get_operation_lock():
            try:
                await self._call_client("reconnect")
                return await self._read_status_unlocked()
            except (PgiTcpClientError, PgiTcpAdapterError) as error:
                self._record_error(error)
                raise PgiTcpAdapterError("Unable to reconnect the PGI gripper: {0}".format(error)) from error

    async def operator_initialize(self) -> GripperStatus:
        """Run ordinary operator-authorized initialization and await its bounded result."""

        self.ensure_started()
        async with self._get_operation_lock():
            try:
                status = await self._read_status_unlocked()
                if status.faulted:
                    raise PgiTcpAdapterError(
                        "PGI initialization is unavailable while device feedback is invalid."
                    )
                if status.initialized:
                    return status
                if not status.initializing:
                    await self._call_client("request_initialization")
                    self._last_status = replace(
                        status,
                        initializing=True,
                        moving=True,
                        grip_state="initializing",
                    )

                deadline = time.monotonic() + self._initialization_timeout_seconds
                while True:
                    initialization_state = await self._call_client("get_initialization_state")
                    if initialization_state == 1:
                        return await self._read_status_unlocked()
                    if initialization_state not in (0, 2):
                        raise PgiTcpAdapterError(
                            "PGI initialization returned unsupported state {0}.".format(
                                initialization_state
                            )
                        )
                    if time.monotonic() >= deadline:
                        raise PgiTcpAdapterError("PGI initialization did not finish before its timeout.")
                    await asyncio.sleep(self._initialization_poll_seconds)
            except PgiTcpWriteOutcomeUnknownError as error:
                self._record_error(error)
                raise PgiTcpOperationOutcomeUnknownError(
                    "PGI initialization outcome is unknown. Reconnect and inspect the gripper "
                    "before issuing another command."
                ) from error
            except (PgiTcpClientError, PgiTcpAdapterError) as error:
                self._record_error(error)
                if isinstance(error, PgiTcpAdapterError):
                    raise
                raise PgiTcpAdapterError("Unable to initialize the PGI gripper: {0}".format(error)) from error

    async def execute(self, command: GripperCommand) -> GripperStatus:
        """Apply one authorized force/position command using only verified registers."""

        self.ensure_started()
        if command.action not in (
            GripperAction.OPEN,
            GripperAction.CLOSE,
            GripperAction.MOVE_TO_POSITION,
        ):
            raise PgiTcpAdapterError(
                "PGI TCP supports only open, close, and move-to-position commands."
            )
        if command.speed_percent is not None:
            raise PgiTcpAdapterError("PGI TCP speed control is unavailable in the verified protocol.")
        if command.target_position is None:
            raise PgiTcpAdapterError("PGI TCP movement requires an explicit target position.")

        async with self._get_operation_lock():
            try:
                status = await self._read_status_unlocked()
                if not status.initialized or status.initializing:
                    raise PgiTcpAdapterError("PGI movement requires completed initialization.")
                if status.faulted or status.emergency_stopped:
                    raise PgiTcpAdapterError("PGI movement is unavailable while safety state is invalid.")
                if status.moving:
                    raise PgiTcpAdapterError("PGI movement is unavailable while another action is active.")
                if command.force_percent is not None:
                    await self._call_client("set_target_force", command.force_percent)
                await self._call_client("set_target_position", command.target_position)
                return await self._read_status_unlocked()
            except PgiTcpWriteOutcomeUnknownError as error:
                self._record_error(error)
                raise PgiTcpOperationOutcomeUnknownError(
                    "PGI command outcome is unknown. Reconnect and inspect the gripper "
                    "before issuing another command."
                ) from error
            except (PgiTcpClientError, PgiTcpAdapterError, ValueError) as error:
                self._record_error(error)
                if isinstance(error, PgiTcpAdapterError):
                    raise
                raise PgiTcpAdapterError("Unable to execute the PGI gripper command: {0}".format(error)) from error

    async def synchronize(self, status: GripperStatus) -> None:
        """Reject mirror synchronization because a physical adapter is authoritative."""

        raise PgiTcpAdapterError("A physical PGI adapter cannot be used as a mirror target.")

    async def _read_status_unlocked(self) -> GripperStatus:
        """Read one consistent normalized status while the adapter operation lock is held."""

        if self._client is None or not self._client.connected:
            raise PgiTcpAdapterError("PGI TCP client is not connected.")
        initialization_state = await self._call_client("get_initialization_state")
        target_position = await self._call_client("get_target_position")
        grip_state_value = await self._call_client("get_grip_state")
        grip_state_names = {
            0: "moving",
            1: "reached",
            2: "gripping",
            3: "dropped",
        }
        initializing = initialization_state == 2
        status_valid = (
            initialization_state in (0, 1, 2)
            and 0 <= target_position <= 1000
            and grip_state_value in grip_state_names
        )
        self._last_status = GripperStatus(
            initialized=initialization_state == 1,
            position=target_position,
            gripping=grip_state_value == 2,
            faulted=not status_valid,
            emergency_stopped=False,
            connected=True,
            initializing=initializing,
            moving=initializing or grip_state_value == 0,
            grip_state="initializing" if initializing else grip_state_names.get(grip_state_value, "unknown"),
            last_error=None,
            position_is_feedback=False,
        )
        return self._last_status

    async def _call_client(self, method_name: str, *arguments: object) -> object:
        """Run one blocking client operation on the adapter's dedicated worker."""

        client = self._client
        executor = self._executor
        if client is None or executor is None:
            raise PgiTcpAdapterError("PGI TCP client resources are unavailable.")
        try:
            method = getattr(client, method_name)
        except AttributeError as error:
            raise PgiTcpAdapterError(
                "PGI TCP client does not expose {0}.".format(method_name)
            ) from error
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, partial(method, *arguments))

    def _get_operation_lock(self) -> asyncio.Lock:
        """Create the adapter lock only while an async lifecycle operation is running."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        return self._operation_lock

    def _record_error(self, error: BaseException) -> None:
        """Retain a degraded status for operator-facing diagnostics and reconnect."""

        connected = self._client is not None and self._client.connected
        self._last_status = replace(
            self._last_status,
            connected=connected,
            initializing=False,
            moving=False,
            last_error=str(error),
        )
