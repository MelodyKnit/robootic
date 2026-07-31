"""Pure in-memory JAKA motion adapter for offline validation and prediction."""

from dataclasses import replace
from typing import Optional

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.adapters.jaka.motion import (
    JakaJointMotionCompiler,
    JakaJointMovePreview,
    JakaMotionConstraint,
)
from gripper_ai_controller.domain.models import (
    ComponentManifest,
    JointMoveMode,
    Pose3D,
    RobotAction,
    RobotCommand,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import RobotAdapter


class JakaDryRunRobotAdapter(BaseAdapter, RobotAdapter):
    """Predict JAKA Zu 3 joint state without importing an SDK or opening a connection.

    This adapter is intentionally limited to validated joint motion previews and
    logical stop commands. It does not estimate forward kinematics, collision risk,
    controller timing beyond a simple per-axis duration estimate, or device telemetry.
    """

    manifest = ComponentManifest(
        "jaka-dry-run-robot",
        "0.1.0",
        "robot",
        ("robot", "jaka", "dry-run", "joint-motion-preview", "mirror-sync"),
        "JakaDryRunRobotAdapter",
    )

    def __init__(
        self,
        initial_joint_positions_rad: Optional[object] = None,
        joint_lower_limits_rad: Optional[object] = None,
        joint_upper_limits_rad: Optional[object] = None,
        maximum_joint_speed_rad_per_second: Optional[object] = None,
        maximum_joint_step_rad: Optional[object] = None,
    ) -> None:
        """Create a configurable, offline JAKA Zu 3 state predictor.

        Constructor inputs accept JSON-decoded scalars and arrays. Custom software
        joint limits may tighten the defaults but cannot relax their 10-degree inset
        from physical limits. The speed and step caps may only tighten their
        conservative defaults.
        """

        super().__init__()
        self.motion_compiler = JakaJointMotionCompiler(
            joint_lower_limits_rad=joint_lower_limits_rad,
            joint_upper_limits_rad=joint_upper_limits_rad,
            maximum_joint_speed_rad_per_second=maximum_joint_speed_rad_per_second,
            maximum_joint_step_rad=maximum_joint_step_rad,
        )
        self.motion_constraint = JakaMotionConstraint(self.motion_compiler)
        predicted_positions = self.motion_compiler.validate_initial_joint_positions(
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            if initial_joint_positions_rad is None
            else initial_joint_positions_rad
        )
        self.status = RobotStatus(
            initialized=False,
            joint_positions_rad=predicted_positions,
            tcp_pose=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "robot_base"),
            connected=False,
            powered=False,
            enabled=False,
            joint_velocities_rad_per_sec=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            joint_torques_nm=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            tcp_velocity=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            motion_progress_percent=0.0,
            temperature_celsius=(25.0, 25.0, 25.0, 25.0, 25.0, 25.0),
            payload_kg=0.0,
            collision_detected=False,
        )

    @property
    def control_mode(self) -> str:
        """Return the browser-facing mode for this purely in-memory target."""

        return "simulation"

    @property
    def manual_motion_enabled(self) -> bool:
        """Report that simulation can always accept the gated manual motion path."""

        return True

    async def initialize(self) -> RobotStatus:
        """Mark the pure prediction target ready without contacting physical hardware."""

        self.status = replace(
            self.status,
            initialized=True,
            connected=True,
            powered=True,
            enabled=True,
            motion_progress_percent=0.0,
        )
        return self.status

    async def get_status(self) -> RobotStatus:
        """Return the most recently predicted robot state without performing I/O."""

        return self.status

    async def reconnect(self) -> RobotStatus:
        """Reset the pure target lifecycle without opening any hardware connection."""

        await self.shutdown()
        await self.startup()
        return await self.initialize()

    async def operator_power_on(self) -> RobotStatus:
        """Mark the in-memory target powered through the same manual-control contract."""

        self.ensure_started()
        return await self.initialize()

    async def operator_enable(self) -> RobotStatus:
        """Mark the in-memory target enabled through the same manual-control contract."""

        self.ensure_started()
        return await self.initialize()

    async def operator_joint_move(
        self,
        command: RobotCommand,
        expected_source_joint_positions_rad: object,
        position_tolerance_rad: object,
    ) -> RobotStatus:
        """Apply one verified absolute preview through the in-memory manual-control path."""

        self.ensure_started()
        if command.action != RobotAction.MOVE_JOINTS:
            raise ValueError("Manual JAKA control supports only MOVE_JOINTS.")
        if command.joint_move_mode != JointMoveMode.ABSOLUTE:
            raise ValueError("Manual JAKA control supports only absolute joint moves.")
        if self.status.moving:
            raise ValueError("JAKA must report in-position before manual motion.")
        self.motion_compiler.require_source_position_match(
            expected_source_joint_positions_rad,
            self.status.joint_positions_rad,
            position_tolerance_rad,
        )
        status = await self.execute(command)
        self.motion_compiler.require_completed_position_match(
            command.joint_positions_rad,
            status.joint_positions_rad,
            position_tolerance_rad,
        )
        return status

    def preview(self, command: RobotCommand) -> JakaJointMovePreview:
        """Compile an immutable offline preview against the current predicted state."""

        return self.motion_compiler.preview(command, self.status)

    async def execute(self, command: RobotCommand) -> RobotStatus:
        """Apply a validated JAKA joint preview to predicted state only.

        ``STOP`` produces a logical ``motion_abort`` preview and deliberately retains
        the current joints. No code path in this class imports an SDK, opens a socket,
        or communicates with a controller.
        """

        preview = self.preview(command)
        if command.action == RobotAction.MOVE_JOINTS:
            self.status = replace(
                self.status,
                joint_positions_rad=preview.target_joint_positions_rad,
                moving=False,
                motion_progress_percent=100.0,
                joint_velocities_rad_per_sec=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
        elif command.action == RobotAction.STOP:
            self.status = replace(
                self.status,
                moving=False,
                joint_velocities_rad_per_sec=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                tcp_velocity=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
        return self.status

    async def synchronize(self, status: RobotStatus) -> None:
        """Replace the offline prediction with authoritative target telemetry."""

        if not isinstance(status, RobotStatus):
            raise TypeError("JAKA dry-run synchronization requires RobotStatus.")
        self.status = status

    async def on_shutdown(self) -> None:
        """Mark the offline target unavailable while retaining its predicted joints."""

        self.status = replace(
            self.status,
            initialized=False,
            connected=False,
            powered=False,
            enabled=False,
        )
