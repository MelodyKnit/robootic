"""Named primary and mirror execution targets."""

import time
from dataclasses import dataclass

from gripper_ai_controller.domain.models import (
    AdapterExecutionReport,
    CommandEnvelope,
    GripperCommand,
    RobotCommand,
    RobotStatus,
    SafetyDecision,
    TargetRole,
    TelemetrySnapshot,
)
from gripper_ai_controller.domain.ports import GripperAdapter, RobotAdapter, RobotMotionConstraint


@dataclass
class ExecutionTarget:
    """One coordinated robot/gripper pair receiving normalized commands."""

    name: str
    role: TargetRole
    robot: RobotAdapter
    gripper: GripperAdapter
    robot_motion_constraint: RobotMotionConstraint = None

    def evaluate_robot_motion(self, command: RobotCommand, status: RobotStatus) -> SafetyDecision:
        """Apply an optional adapter-specific constraint before core authorization.

        Targets without a specialized constraint retain the existing generic runtime
        policy. Constraints receive telemetry captured for the current dispatch cycle,
        so relative joint commands are evaluated against the same state later used by
        the adapter.
        """

        if self.robot_motion_constraint is None:
            return SafetyDecision(True, "No adapter-specific robot motion constraint is configured.")
        return self.robot_motion_constraint.evaluate(command, status)

    async def startup(self) -> None:
        """Start and initialize both adapters for this target."""

        await self.robot.startup()
        await self.gripper.startup()
        await self.robot.initialize()
        await self.gripper.initialize()

    async def shutdown(self) -> None:
        """Stop gripper before robot to leave the target in a conservative order."""

        await self.gripper.shutdown()
        await self.robot.shutdown()

    async def telemetry(self) -> TelemetrySnapshot:
        """Read a synchronized state snapshot from the target's two adapters."""

        return TelemetrySnapshot(
            captured_at=time.time(),
            robot=await self.robot.get_status(),
            gripper=await self.gripper.get_status(),
        )

    async def execute(self, command: CommandEnvelope) -> AdapterExecutionReport:
        """Route a typed command only to its matching adapter."""

        try:
            if isinstance(command.payload, GripperCommand):
                await self.gripper.execute(command.payload)
            elif isinstance(command.payload, RobotCommand):
                await self.robot.execute(command.payload)
            else:
                raise TypeError("Unsupported command payload.")
            return AdapterExecutionReport(
                target_name=self.name,
                command_id=command.command_id,
                succeeded=True,
                message="Command executed.",
                telemetry=await self.telemetry(),
            )
        except Exception as error:
            return AdapterExecutionReport(
                target_name=self.name,
                command_id=command.command_id,
                succeeded=False,
                message=str(error),
            )

    async def synchronize(self, telemetry: TelemetrySnapshot) -> None:
        """Correct a mirror's predicted state with authoritative primary telemetry."""

        await self.robot.synchronize(telemetry.robot)
        await self.gripper.synchronize(telemetry.gripper)
