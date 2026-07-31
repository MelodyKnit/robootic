"""JAKA controller adapter with a separately gated manual joint-motion entry point."""

import asyncio
import importlib
import math
import time
from typing import Any, Callable, Optional, Sequence, Tuple

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.adapters.jaka.motion import (
    JakaJointMotionCompiler,
    JakaJointMoveRequest,
    JakaMotionCompletionError,
    JakaMotionConstraint,
)
from gripper_ai_controller.domain.models import (
    ComponentManifest,
    JointMoveMode,
    JointPositionSnapshot,
    Pose3D,
    RobotAction,
    RobotCommand,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import RobotAdapter


class JakaAdapterError(RuntimeError):
    """Report an unsuccessful JAKA SDK operation without exposing vendor types."""


MANUAL_MOTION_ROBOT_MODEL = "zu3"
"""The only robot model whose motion envelope is implemented by this adapter."""


class JakaAdapter(BaseAdapter, RobotAdapter):
    """Normalize safe JAKA controller access through the project robot-adapter contract.

    Startup only opens an SDK session. ``execute`` deliberately rejects every real
    motion command so Runtime and plugins cannot move hardware. A separate narrow
    manual-control entry point accepts only an already-authorized blocking absolute
    joint move. Automatic controller power-on remains unsupported. Servo enable is an
    explicit operator action that requires a local opt-in and a powered, fault-free
    controller.
    """

    manifest = ComponentManifest(
        "jaka-robot",
        "0.2.0",
        "robot",
        (
            "robot",
            "jaka",
            "connect",
            "joint-position-read",
            "explicit-enable",
            "manual-joint-motion-gated",
        ),
        "JakaAdapter",
    )

    def __init__(
        self,
        controller_ip: str,
        allow_enable: bool = False,
        allow_remote_power_on: bool = False,
        allow_manual_motion: bool = False,
        client_factory: Optional[Callable[[str], Any]] = None,
        joint_lower_limits_rad: Optional[object] = None,
        joint_upper_limits_rad: Optional[object] = None,
        maximum_joint_speed_rad_per_second: Optional[object] = None,
        maximum_joint_step_rad: Optional[object] = None,
        robot_model: Optional[str] = None,
    ) -> None:
        """Bind one controller endpoint without establishing a connection yet.

        `controller_ip` must come from ignored local configuration or an explicit caller,
        never from a version-controlled project configuration. `allow_enable`,
        `allow_remote_power_on`, and `allow_manual_motion` remain false by default because
        all can change physical hardware state. Optional joint limits and speed/step caps
        construct the pure pre-dispatch constraint used by the separately gated manual-control facade.
        """

        super().__init__()
        if not isinstance(controller_ip, str) or not controller_ip.strip():
            raise ValueError("JAKA controller_ip must be a non-empty string.")
        if type(allow_enable) is not bool:
            raise ValueError("JAKA allow_enable must be a boolean value.")
        if type(allow_remote_power_on) is not bool:
            raise ValueError("JAKA allow_remote_power_on must be a boolean value.")
        if type(allow_manual_motion) is not bool:
            raise ValueError("JAKA allow_manual_motion must be a boolean value.")
        if robot_model is not None and robot_model != MANUAL_MOTION_ROBOT_MODEL:
            raise ValueError(
                "JAKA robot_model must be omitted for read-only access or set to '{0}' for manual motion.".format(
                    MANUAL_MOTION_ROBOT_MODEL
                )
            )
        self.controller_ip = controller_ip.strip()
        self.allow_enable = allow_enable
        self.allow_remote_power_on = allow_remote_power_on
        self.allow_manual_motion = allow_manual_motion
        self.robot_model = robot_model
        self.client_factory = client_factory or self.create_vendor_client
        self.motion_compiler = JakaJointMotionCompiler(
            joint_lower_limits_rad=joint_lower_limits_rad,
            joint_upper_limits_rad=joint_upper_limits_rad,
            maximum_joint_speed_rad_per_second=maximum_joint_speed_rad_per_second,
            maximum_joint_step_rad=maximum_joint_step_rad,
        )
        self.motion_constraint = JakaMotionConstraint(self.motion_compiler)
        self._client = None  # type: Optional[Any]
        self._connected = False
        self._powered = False
        self._enabled = False
        # The vendor SDK does not document concurrent calls on one RC instance.
        # A blocking joint move therefore serializes browser status reads until it ends.
        self._vendor_call_lock = None  # type: Optional[Any]

    @property
    def connected(self) -> bool:
        """Return whether the JAKA SDK login operation completed successfully."""

        return self._connected

    @property
    def powered(self) -> bool:
        """Return the latest controller power state reported by read-only telemetry."""

        return self._powered

    @property
    def enabled(self) -> bool:
        """Return the latest servo-enable state reported by read-only telemetry."""

        return self._enabled

    @property
    def control_mode(self) -> str:
        """Return the operator-facing mode without exposing SDK implementation details."""

        return "physical"

    @property
    def manual_motion_enabled(self) -> bool:
        """Return whether local configuration released manual moves for the supported model."""

        return self.allow_manual_motion and self.robot_model == MANUAL_MOTION_ROBOT_MODEL

    @staticmethod
    def create_vendor_client(controller_ip: str) -> Any:
        """Load the project-local JAKA extension only when a real connection is requested."""

        try:
            module = importlib.import_module("gripper_ai_controller.adapters.jaka.jkrc")
        except ImportError as error:
            raise JakaAdapterError(
                "Unable to load the project-local JAKA SDK. Verify the Python 3.7 x64 "
                "jkrc.pyd, jakaAPI.dll, and Microsoft Visual C++ runtime."
            ) from error
        return module.RC(controller_ip)

    async def on_startup(self) -> None:
        """Create one SDK client and log in without changing controller power or motion."""

        self._client = self.client_factory(self.controller_ip)
        try:
            self.require_success("login", await self._call_client_method("login"))
        except Exception:
            self._client = None
            raise
        self._connected = True

    async def on_shutdown(self) -> None:
        """Log out of the controller without disabling servos or changing controller power."""

        try:
            if self._client is not None and self._connected:
                self.require_success("logout", await self._call_client_method("logout"))
        finally:
            self._client = None
            self._connected = False
            self._powered = False
            self._enabled = False

    async def initialize(self) -> RobotStatus:
        """Read initial controller telemetry after the runtime has started the adapter."""

        self.ensure_started()
        return await self.get_status()

    async def get_status(self) -> RobotStatus:
        """Read JAKA robot telemetry without changing power, enable, or motion state."""

        self.ensure_started()
        vendor_status = self.require_payload(
            "get_robot_status", await self._call_client_method("get_robot_status")
        )
        if not isinstance(vendor_status, (list, tuple)) or len(vendor_status) < 24:
            raise JakaAdapterError("JAKA get_robot_status returned an incomplete telemetry payload.")

        joint_positions = self.require_vector("joint positions", vendor_status[19])
        tcp_values = self.require_vector("TCP pose", vendor_status[18])
        in_position = bool(vendor_status[1])
        controller_socket_connected = bool(vendor_status[22])
        controller_connected = self._connected and controller_socket_connected
        self._powered = bool(vendor_status[2]) if controller_connected else False
        self._enabled = bool(vendor_status[3]) if controller_connected else False
        protective_stop = bool(vendor_status[5])
        drag_mode = bool(vendor_status[6])
        soft_limit = bool(vendor_status[7])
        controller_error = bool(vendor_status[0])
        emergency_stopped = bool(vendor_status[23])
        return RobotStatus(
            initialized=controller_connected,
            joint_positions_rad=joint_positions,
            tcp_pose=Pose3D(
                tcp_values[0],
                tcp_values[1],
                tcp_values[2],
                tcp_values[3],
                tcp_values[4],
                tcp_values[5],
                "robot_base",
            ),
            faulted=controller_error or protective_stop or drag_mode or soft_limit,
            emergency_stopped=emergency_stopped,
            connected=controller_connected,
            powered=self._powered,
            enabled=self._enabled,
            moving=not in_position,
        )

    async def get_joint_positions(self) -> JointPositionSnapshot:
        """Read J1 through J6 directly from the documented read-only JAKA SDK call.

        The returned values are controller joint angles in radians. This method does
        not read or change power, servo-enable, motion, or controller configuration.
        """

        self.ensure_started()
        joint_positions = self.require_vector(
            "joint positions",
            self.require_payload(
                "get_joint_position", await self._call_client_method("get_joint_position")
            ),
        )
        return JointPositionSnapshot(time.time(), joint_positions)

    async def get_sdk_version(self) -> str:
        """Return the read-only SDK version reported by the connected controller."""

        self.ensure_started()
        return str(self.require_payload("get_sdk_version", await self._call_client_method("get_sdk_version")))

    async def power_on(self) -> RobotStatus:
        """Power on the controller after explicit local authorization.

        This method requires `allow_remote_power_on=True` in local configuration.
        Remote power-on should only be enabled in controlled environments where the
        operator can verify the work area is clear and safe before initiating.
        """

        self.ensure_started()
        if not self.allow_remote_power_on:
            raise PermissionError(
                "JAKA remote power-on is disabled. Enable it in local configuration with "
                "'allow_remote_power_on: true' only if you have verified the work area is "
                "clear and safe. Remote power-on should not be enabled in production environments."
            )
        status = await self.get_status()
        if status.faulted or status.emergency_stopped:
            raise JakaAdapterError(
                "JAKA power-on refused because the controller reports a fault or emergency stop. "
                "Clear the fault on the teach pendant first."
            )
        if self._powered:
            return status
        self.require_success("power_on", await self._call_client_method("power_on"))
        status = await self.get_status()
        if not self._powered:
            raise JakaAdapterError("JAKA power_on returned success but telemetry still reports not powered.")
        return status

    async def enable(self) -> RobotStatus:
        """Enable a powered, fault-free robot only after explicit local authorization.

        This method never calls `power_on()` and never sends a movement command. It is
        intentionally outside the runtime command path so planners cannot invoke it.
        """

        self.ensure_started()
        if not self.allow_enable:
            raise PermissionError("JAKA enable is disabled until local configuration explicitly allows it.")
        status = await self.get_status()
        if status.faulted or status.emergency_stopped:
            raise JakaAdapterError("JAKA enable refused because the controller reports a fault or emergency stop.")
        if status.moving:
            raise JakaAdapterError("JAKA enable refused because the controller is not in-position.")
        if not self._powered:
            raise JakaAdapterError("JAKA enable refused because the controller is not powered; automatic power-on is disabled.")
        if self._enabled:
            return status
        self.require_success("enable_robot", await self._call_client_method("enable_robot"))
        status = await self.get_status()
        if not self._enabled:
            raise JakaAdapterError("JAKA enable_robot returned success but telemetry is still not enabled.")
        return status

    async def disable(self) -> RobotStatus:
        """Explicitly disable servos as a recovery action without powering off the controller."""

        self.ensure_started()
        status = await self.get_status()
        if not self._enabled:
            return status
        self.require_success("disable_robot", await self._call_client_method("disable_robot"))
        return await self.get_status()

    async def reconnect(self) -> RobotStatus:
        """Replace the SDK session without enabling, powering, or moving the robot."""

        await self.shutdown()
        await self.startup()
        return await self.initialize()

    async def operator_power_on(self) -> RobotStatus:
        """Power on the controller only through the separately authorized manual-control facade."""

        return await self.power_on()

    async def operator_enable(self) -> RobotStatus:
        """Enable servos only through the separately authorized manual-control facade."""

        return await self.enable()

    async def operator_joint_move(
        self,
        command: RobotCommand,
        expected_source_joint_positions_rad: object,
        position_tolerance_rad: object,
    ) -> RobotStatus:
        """Run one explicitly authorized, blocking absolute JAKA joint move.

        The normal ``execute`` method remains motion-disabled for Runtime and plugin
        dispatch. This method is intentionally narrow: it accepts only an already
        validated absolute six-axis command after a local manual-control service has
        checked token, current telemetry, and a second confirmation.
        """

        self.ensure_started()
        if not self.manual_motion_enabled:
            raise PermissionError(
                "JAKA manual motion requires allow_manual_motion=true and robot_model='zu3' in local configuration."
            )
        if command.action != RobotAction.MOVE_JOINTS:
            raise JakaAdapterError("Manual JAKA control supports only MOVE_JOINTS.")
        if command.joint_move_mode != JointMoveMode.ABSOLUTE:
            raise JakaAdapterError("Manual JAKA control supports only absolute joint moves.")
        status = await self.get_status()
        if not status.connected or not status.initialized:
            raise JakaAdapterError("JAKA must be connected and initialized before manual motion.")
        if status.faulted or status.emergency_stopped:
            raise JakaAdapterError("JAKA fault or emergency-stop state prevents manual motion.")
        if status.moving:
            raise JakaAdapterError("JAKA must report in-position before manual motion.")
        if not status.powered or not status.enabled:
            raise JakaAdapterError("JAKA must be powered and servo-enabled before manual motion.")

        self.motion_compiler.require_source_position_match(
            expected_source_joint_positions_rad,
            status.joint_positions_rad,
            position_tolerance_rad,
        )

        preview = self.motion_compiler.compile(command, status)
        if not isinstance(preview.request, JakaJointMoveRequest):
            raise JakaAdapterError("Manual JAKA control did not compile a joint_move request.")
        request = preview.request
        self.require_success(
            "joint_move",
            await self._call_client_method(
                "joint_move",
                list(request.joint_positions_rad),
                request.sdk_move_mode,
                request.is_blocking,
                request.speed_rad_per_second,
            ),
        )
        completed_status = await self.get_status()
        if not completed_status.connected or not completed_status.initialized:
            raise JakaMotionCompletionError("JAKA connection was unavailable after joint_move.")
        if completed_status.faulted or completed_status.emergency_stopped:
            raise JakaMotionCompletionError("JAKA reported a fault or emergency stop after joint_move.")
        if completed_status.moving:
            raise JakaMotionCompletionError("JAKA joint_move returned before the controller reported in-position.")
        self.motion_compiler.require_completed_position_match(
            request.joint_positions_rad,
            completed_status.joint_positions_rad,
            position_tolerance_rad,
        )
        return completed_status

    async def execute(self, command: RobotCommand) -> RobotStatus:
        """Reject every robot motion command until a separately reviewed motion release exists."""

        self.ensure_started()
        raise JakaAdapterError(
            "JAKA motion execution is intentionally disabled in the initial adapter; "
            "the supplied command was not sent to the controller."
        )

    async def synchronize(self, status: RobotStatus) -> None:
        """Reject mirror synchronization because a physical JAKA target is authoritative only."""

        raise JakaAdapterError("A physical JAKA adapter cannot be used as a mirror target.")

    async def _call_client_method(self, method_name: str, *arguments: Any) -> Any:
        """Invoke one synchronous SDK method after the adapter has allocated its client."""

        if self._client is None:
            raise JakaAdapterError("JAKA SDK client is not connected.")
        try:
            method = getattr(self._client, method_name)
        except AttributeError as error:
            raise JakaAdapterError("JAKA SDK does not expose {0}.".format(method_name)) from error
        async with self._vendor_call_lock_for_current_loop():
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(None, method, *arguments)
            return await self._await_vendor_future(future)

    @staticmethod
    async def _await_vendor_future(future: Any) -> Any:
        """Keep the SDK lock held until a blocking vendor call actually returns.

        Cancelling an HTTP request cannot cancel the vendor thread. The cancellation is
        therefore delayed until the thread finishes so a second SDK call cannot overlap a
        still-running physical action.
        """

        cancellation = None  # type: Optional[BaseException]
        while True:
            try:
                result = await asyncio.shield(future)
                break
            except asyncio.CancelledError as error:
                cancellation = error
                continue
            except BaseException:
                if cancellation is not None:
                    raise cancellation
                raise
        if cancellation is not None:
            raise cancellation
        return result

    def _vendor_call_lock_for_current_loop(self) -> Any:
        """Create the single-client SDK lock lazily under the active asyncio loop."""

        if self._vendor_call_lock is None:
            self._vendor_call_lock = asyncio.Lock()
        return self._vendor_call_lock

    @staticmethod
    def require_success(operation: str, response: Any) -> None:
        """Raise a normalized error unless a JAKA SDK response has success code zero."""

        if not isinstance(response, (list, tuple)) or not response or response[0] != 0:
            raise JakaAdapterError("JAKA {0} failed: {1!r}".format(operation, response))

    @classmethod
    def require_payload(cls, operation: str, response: Any) -> Any:
        """Return a successful JAKA response payload and reject missing payloads."""

        cls.require_success(operation, response)
        if len(response) < 2:
            raise JakaAdapterError("JAKA {0} returned no payload.".format(operation))
        return response[1]

    @staticmethod
    def require_vector(name: str, values: Any) -> Tuple[float, float, float, float, float, float]:
        """Convert one vendor six-axis vector into the normalized immutable representation."""

        if not isinstance(values, Sequence) or len(values) != 6:
            raise JakaAdapterError("JAKA {0} must contain exactly six values.".format(name))
        normalized = tuple(float(value) for value in values)
        if not all(math.isfinite(value) for value in normalized):
            raise JakaAdapterError("JAKA {0} must contain only finite values.".format(name))
        return normalized  # type: ignore
