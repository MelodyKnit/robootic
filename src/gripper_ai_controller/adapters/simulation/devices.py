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
        """Create a camera with a deterministic in-memory RGB test image."""

        super().__init__()
        self.healthy = healthy
        self.frame_reference = frame_reference
        self.width = 320
        self.height = 240
        self.pixel_payload = self._build_pixel_payload()

    async def capture(self) -> ImageFrame:
        """Emit one synthetic RGB frame and notify observers without opening a device."""

        frame = ImageFrame(
            camera_id="sim-camera",
            captured_at=time.time(),
            frame_reference=self.frame_reference,
            calibration_id="sim-camera-calibration",
            healthy=self.healthy,
            pixel_payload=self.pixel_payload,
            width=self.width,
            height=self.height,
            pixel_format="rgb8",
        )
        await self.emit_frame(frame)
        return frame

    def _build_pixel_payload(self) -> bytes:
        """Create one reusable visible RGB calibration-style image without file I/O."""

        pixels = bytearray(self.width * self.height * 3)
        for y_value in range(self.height):
            for x_value in range(self.width):
                offset = (y_value * self.width + x_value) * 3
                pixels[offset] = 28 + (x_value * 40 // self.width)
                pixels[offset + 1] = 80 + (y_value * 80 // self.height)
                pixels[offset + 2] = 110
                if 96 <= x_value < 224 and 72 <= y_value < 168:
                    pixels[offset] = 220
                    pixels[offset + 1] = 150
                    pixels[offset + 2] = 45
        return bytes(pixels)
