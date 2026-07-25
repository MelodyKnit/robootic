"""Lifecycle-enabled ports that isolate core logic from SDKs and simulators."""

from abc import ABC, abstractmethod
import time
from typing import Awaitable, Callable, Mapping, Optional, Tuple, Union

from gripper_ai_controller.domain.models import (
    CommandEnvelope,
    CameraParameter,
    CameraParameterUpdateResult,
    CameraParameterValue,
    ComponentManifest,
    GripperCommand,
    GripperStatus,
    ImageFrame,
    JointPositionSnapshot,
    RobotCommand,
    RobotStatus,
    TelemetrySnapshot,
)


FrameHandler = Callable[[ImageFrame], Awaitable[None]]
"""An asynchronous observer that receives one successfully acquired image frame."""

FrameHandlerDecorator = Callable[[FrameHandler], FrameHandler]
"""A decorator returned by a vision adapter to register one frame observer."""


class LifecycleComponent(ABC):
    """A component that can be started and stopped by the core runtime."""

    manifest = None  # type: ComponentManifest

    async def startup(self) -> None:
        """Allocate non-hardware resources required by the component."""

    async def shutdown(self) -> None:
        """Release resources and leave the component in a safe stopped state."""


class RobotAdapter(LifecycleComponent):
    """Executes approved robot commands and exposes live robot state."""

    @abstractmethod
    async def initialize(self) -> RobotStatus:
        """Initialize the adapter's robot representation without physical I/O."""

    @abstractmethod
    async def get_status(self) -> RobotStatus:
        """Read the latest robot state."""

    async def get_joint_positions(self) -> JointPositionSnapshot:
        """Return a timestamped joint-angle observation derived from current status.

        Adapters with an official dedicated joint-position API may override this
        method. The default preserves compatibility for existing adapters while
        keeping all generic callers on the same normalized radians contract.
        """

        status = await self.get_status()
        positions = status.joint_positions_rad
        if len(positions) != 6:
            raise RuntimeError("Robot status must expose exactly six joint positions.")
        return JointPositionSnapshot(time.time(), tuple(float(value) for value in positions))

    @abstractmethod
    async def execute(self, command: RobotCommand) -> RobotStatus:
        """Execute an already-authorized normalized robot command."""

    @abstractmethod
    async def synchronize(self, status: RobotStatus) -> None:
        """Apply primary telemetry to a mirror adapter."""


class GripperAdapter(LifecycleComponent):
    """Executes approved gripper commands and exposes live gripper state."""

    @abstractmethod
    async def initialize(self) -> GripperStatus:
        """Initialize the adapter's gripper representation without physical I/O."""

    @abstractmethod
    async def get_status(self) -> GripperStatus:
        """Read the latest gripper state."""

    @abstractmethod
    async def execute(self, command: GripperCommand) -> GripperStatus:
        """Execute an already-authorized normalized gripper command."""

    @abstractmethod
    async def synchronize(self, status: GripperStatus) -> None:
        """Apply primary telemetry to a mirror adapter."""


class VisionAdapter(LifecycleComponent):
    """Acquires image frames and camera health without running perception algorithms."""

    @abstractmethod
    def on_frame(
        self, handler: Optional[FrameHandler] = None
    ) -> Union[FrameHandler, FrameHandlerDecorator]:
        """Register an asynchronous observer for this adapter's successful captures.

        Calling this method without a handler returns a decorator, allowing callers to
        write ``@camera.on_frame()`` above an ``async def`` function. The registration
        belongs to this adapter instance so multiple cameras cannot share observers by
        accident. Registering an observer does not start background acquisition.
        """

    @abstractmethod
    async def capture(self) -> ImageFrame:
        """Capture one frame reference and its metadata."""


class CameraParameterError(ValueError):
    """Report an invalid or unavailable normalized camera parameter operation."""


class CameraParameterAdapter(ABC):
    """Optional vision capability for controlled camera parameter inspection and updates.

    Implementations expose only a fixed, normalized parameter whitelist. They never
    hand the browser a vendor SDK client or accept arbitrary SDK node names.
    """

    @abstractmethod
    async def get_camera_parameters(self) -> Tuple[CameraParameter, ...]:
        """Return currently supported parameter descriptors and live device values."""

    @abstractmethod
    async def update_camera_parameters(
        self, updates: Mapping[str, CameraParameterValue]
    ) -> CameraParameterUpdateResult:
        """Validate and apply one normalized update batch, restarting acquisition when needed."""
