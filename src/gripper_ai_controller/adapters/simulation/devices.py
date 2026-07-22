"""Stateful simulation adapters that never open a device, socket, or camera."""

import time
from dataclasses import replace

from gripper_ai_controller.adapters.base import BaseAdapter, FrameDispatchingVisionAdapter
from gripper_ai_controller.domain.models import (
    ComponentManifest,
    GripperAction,
    GripperCommand,
    GripperStatus,
    ImageFrame,
    Pose3D,
    RobotAction,
    RobotCommand,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import GripperAdapter, RobotAdapter


class SimulatedRobotAdapter(BaseAdapter, RobotAdapter):
    """An in-memory six-axis robot representation for primary and mirror targets."""

    manifest = ComponentManifest(
        "simulated-robot",
        "0.1.0",
        "robot",
        ("robot", "mirror-sync"),
        "SimulatedRobotAdapter",
    )

    def __init__(self, fail_on_execute: bool = False) -> None:
        """Create a stopped robot state; failure injection is reserved for fault tests."""

        super().__init__()
        self.fail_on_execute = fail_on_execute
        self.status = RobotStatus(
            initialized=False,
            joint_positions_rad=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            tcp_pose=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "robot_base"),
            connected=False,
            powered=False,
            enabled=False,
        )

    async def initialize(self) -> RobotStatus:
        """Mark the in-memory robot initialized without producing a physical motion."""

        self.status = replace(self.status, initialized=True, connected=True, powered=True, enabled=True)
        return self.status

    async def get_status(self) -> RobotStatus:
        """Return the latest simulated state without copying or contacting hardware."""

        return self.status

    async def execute(self, command: RobotCommand) -> RobotStatus:
        """Apply a normalized robot command to memory after runtime authorization.

        This adapter deliberately models only target joint or TCP state. It does not
        model kinematics, collision, acceleration, or real controller timing.
        """

        if self.fail_on_execute:
            raise RuntimeError("Simulated robot execution failed.")
        if command.action == RobotAction.MOVE_JOINTS:
            self.status = replace(self.status, initialized=True, joint_positions_rad=command.joint_positions_rad)
        elif command.action == RobotAction.MOVE_LINEAR and command.target_pose is not None:
            self.status = replace(self.status, initialized=True, tcp_pose=command.target_pose)
        return self.status

    async def synchronize(self, status: RobotStatus) -> None:
        """Replace predicted mirror state with telemetry from the authoritative target."""

        self.status = status


class SimulatedGripperAdapter(BaseAdapter, GripperAdapter):
    """An in-memory PGI-like gripper representation for primary and mirror targets."""

    manifest = ComponentManifest(
        "simulated-gripper",
        "0.1.0",
        "gripper",
        ("gripper", "mirror-sync"),
        "SimulatedGripperAdapter",
    )

    def __init__(self, fail_on_execute: bool = False) -> None:
        """Create an uninitialized open gripper state for safe lifecycle testing."""

        super().__init__()
        self.fail_on_execute = fail_on_execute
        self.status = GripperStatus(initialized=False, position=1000, gripping=False)

    async def initialize(self) -> GripperStatus:
        """Mark the gripper initialized while preserving its existing simulated position."""

        self.status = GripperStatus(initialized=True, position=self.status.position, gripping=False)
        return self.status

    async def get_status(self) -> GripperStatus:
        """Return the latest simulated gripper feedback without external I/O."""

        return self.status

    async def execute(self, command: GripperCommand) -> GripperStatus:
        """Apply an authorized command and update position/gripping state in memory.

        HOLD and STOP retain current state. The simulation intentionally does not infer
        contact force, object geometry, dropped-object events, or communication timing.
        """

        if self.fail_on_execute:
            raise RuntimeError("Simulated gripper execution failed.")
        if command.action in (GripperAction.HOLD, GripperAction.STOP):
            return self.status
        position = self.status.position if command.target_position is None else command.target_position
        self.status = GripperStatus(
            initialized=True,
            position=position,
            gripping=command.action == GripperAction.CLOSE,
        )
        return self.status

    async def synchronize(self, status: GripperStatus) -> None:
        """Overwrite a mirror prediction with authoritative primary gripper telemetry."""

        self.status = status


class SimulatedCameraAdapter(FrameDispatchingVisionAdapter):
    """A healthy synthetic camera that publishes a frame reference, not image I/O."""

    manifest = ComponentManifest(
        "simulated-camera",
        "0.1.0",
        "vision",
        ("vision", "frame-source"),
        "SimulatedCameraAdapter",
    )

    def __init__(self, healthy: bool = True, frame_reference: str = "simulation://frame-1") -> None:
        """Create a camera with configurable health and metadata-only frame reference."""

        super().__init__()
        self.healthy = healthy
        self.frame_reference = frame_reference

    async def capture(self) -> ImageFrame:
        """Emit one synthetic frame and notify observers without opening a camera device."""

        frame = ImageFrame(
            camera_id="sim-camera",
            captured_at=time.time(),
            frame_reference=self.frame_reference,
            calibration_id="sim-camera-calibration",
            healthy=self.healthy,
        )
        await self.emit_frame(frame)
        return frame
