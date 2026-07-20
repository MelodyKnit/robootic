"""Stateful simulation adapters that never open a device, socket, or camera."""

import time

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
from gripper_ai_controller.domain.ports import GripperAdapter, RobotAdapter, VisionAdapter


class SimulatedRobotAdapter(RobotAdapter):
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

        self.fail_on_execute = fail_on_execute
        self.status = RobotStatus(
            initialized=False,
            joint_positions_rad=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            tcp_pose=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "robot_base"),
        )

    async def initialize(self) -> RobotStatus:
        """Mark the in-memory robot initialized without producing a physical motion."""

        self.status = RobotStatus(True, self.status.joint_positions_rad, self.status.tcp_pose)
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
            self.status = RobotStatus(True, command.joint_positions_rad, self.status.tcp_pose)
        elif command.action == RobotAction.MOVE_LINEAR and command.target_pose is not None:
            self.status = RobotStatus(True, self.status.joint_positions_rad, command.target_pose)
        return self.status

    async def synchronize(self, status: RobotStatus) -> None:
        """Replace predicted mirror state with telemetry from the authoritative target."""

        self.status = status


class SimulatedGripperAdapter(GripperAdapter):
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


class SimulatedCameraAdapter(VisionAdapter):
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

        self.healthy = healthy
        self.frame_reference = frame_reference

    async def capture(self) -> ImageFrame:
        """Emit one timestamped synthetic frame without reading pixels or a camera device."""

        return ImageFrame(
            camera_id="sim-camera",
            captured_at=time.time(),
            frame_reference=self.frame_reference,
            calibration_id="sim-camera-calibration",
            healthy=self.healthy,
        )
