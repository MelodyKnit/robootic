"""JAKA controller adapter that permits connection and explicit enable only."""

import asyncio
import importlib
import math
import time
from typing import Any, Callable, Optional, Sequence, Tuple

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.domain.models import (
    ComponentManifest,
    JointPositionSnapshot,
    Pose3D,
    RobotCommand,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import RobotAdapter


class JakaAdapterError(RuntimeError):
    """Report an unsuccessful JAKA SDK operation without exposing vendor types."""


class JakaAdapter(BaseAdapter, RobotAdapter):
    """Normalize safe JAKA controller access through the project robot-adapter contract.

    Startup only opens an SDK session. Motion commands and automatic controller power-on
    are deliberately unsupported in this initial adapter. Enabling is an explicit
    operator action that requires a local opt-in and a powered, fault-free controller.
    """

    manifest = ComponentManifest(
        "jaka-robot",
        "0.2.0",
        "robot",
        ("robot", "jaka", "connect", "joint-position-read", "explicit-enable", "motion-disabled"),
        "JakaAdapter",
    )

    def __init__(
        self,
        controller_ip: str,
        allow_enable: bool = False,
        client_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """Bind one controller endpoint without establishing a connection yet.

        `controller_ip` must come from ignored local configuration or an explicit caller,
        never from a version-controlled project configuration. `allow_enable` remains
        false by default because servo enable changes physical hardware state.
        """

        super().__init__()
        if not controller_ip:
            raise ValueError("JAKA controller_ip must be a non-empty string.")
        self.controller_ip = controller_ip
        self.allow_enable = allow_enable
        self.client_factory = client_factory or self.create_vendor_client
        self._client = None  # type: Optional[Any]
        self._connected = False
        self._powered = False
        self._enabled = False

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
        vendor_status = self.require_payload("get_robot_status", await self.call_vendor("get_robot_status"))
        if not isinstance(vendor_status, (list, tuple)) or len(vendor_status) < 24:
            raise JakaAdapterError("JAKA get_robot_status returned an incomplete telemetry payload.")

        joint_positions = self.require_vector("joint positions", vendor_status[19])
        tcp_values = self.require_vector("TCP pose", vendor_status[18])
        self._powered = bool(vendor_status[2])
        self._enabled = bool(vendor_status[3])
        protective_stop = bool(vendor_status[5])
        soft_limit = bool(vendor_status[7])
        controller_error = bool(vendor_status[0])
        emergency_stopped = bool(vendor_status[23])
        return RobotStatus(
            initialized=self._connected,
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
            faulted=controller_error or protective_stop or soft_limit,
            emergency_stopped=emergency_stopped,
            connected=self._connected,
            powered=self._powered,
            enabled=self._enabled,
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
                "get_joint_position", await self.call_vendor("get_joint_position")
            ),
        )
        return JointPositionSnapshot(time.time(), joint_positions)

    async def get_sdk_version(self) -> str:
        """Return the read-only SDK version reported by the connected controller."""

        self.ensure_started()
        return str(self.require_payload("get_sdk_version", await self.call_vendor("get_sdk_version")))

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
        if not self._powered:
            raise JakaAdapterError("JAKA enable refused because the controller is not powered; automatic power-on is disabled.")
        if self._enabled:
            return status
        self.require_success("enable_robot", await self.call_vendor("enable_robot"))
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
        self.require_success("disable_robot", await self.call_vendor("disable_robot"))
        return await self.get_status()

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

    async def call_vendor(self, method_name: str, *arguments: Any) -> Any:
        """Run one synchronous JAKA SDK method outside the asyncio event-loop thread."""

        self.ensure_started()
        return await self._call_client_method(method_name, *arguments)

    async def _call_client_method(self, method_name: str, *arguments: Any) -> Any:
        """Invoke one synchronous SDK method after the adapter has allocated its client."""

        if self._client is None:
            raise JakaAdapterError("JAKA SDK client is not connected.")
        try:
            method = getattr(self._client, method_name)
        except AttributeError as error:
            raise JakaAdapterError("JAKA SDK does not expose {0}.".format(method_name)) from error
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, method, *arguments)

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
