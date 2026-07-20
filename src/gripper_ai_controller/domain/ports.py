"""Lifecycle-enabled ports that isolate core logic from SDKs and simulators."""

from abc import ABC, abstractmethod

from gripper_ai_controller.domain.models import (
    CommandEnvelope,
    ComponentManifest,
    GripperCommand,
    GripperStatus,
    ImageFrame,
    RobotCommand,
    RobotStatus,
    TelemetrySnapshot,
)


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
    async def capture(self) -> ImageFrame:
        """Capture one frame reference and its metadata."""
