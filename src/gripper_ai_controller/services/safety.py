"""Deterministic command authorization independent of planners and SDKs."""

import time

from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.domain.models import (
    GripperAction,
    GripperCommand,
    GripperStatus,
    CommandEnvelope,
    PerceptionResult,
    RobotCommand,
    RobotStatus,
    SafetyDecision,
)


class SafetyPolicy:
    """Applies documented command bounds and live device-state interlocks."""

    def __init__(self, limits: SafetyLimits) -> None:
        """Store the project-configured bounds used consistently by every dispatch path."""

        self.limits = limits

    def evaluate(
        self,
        command: CommandEnvelope,
        robot_status: RobotStatus,
        gripper_status: GripperStatus,
        perception: PerceptionResult,
        now: float = None,
    ) -> SafetyDecision:
        """Authorize only healthy, calibrated, current, and bounded commands."""

        now = time.time() if now is None else now
        if not perception.valid:
            return SafetyDecision(False, "Perception result is invalid: {0}".format(perception.reason))
        if now - perception.captured_at > self.limits.maximum_perception_age_seconds:
            return SafetyDecision(False, "Perception result is stale.")
        for detected in perception.objects:
            if detected.confidence < self.limits.minimum_perception_confidence:
                return SafetyDecision(False, "Perception confidence is below the configured minimum.")
            for candidate in detected.grasp_candidates:
                if candidate.pose.frame_id != "robot_base":
                    return SafetyDecision(False, "Grasp candidates must use the robot_base frame.")

        payload = command.payload
        if isinstance(payload, RobotCommand):
            return self._evaluate_robot(payload, robot_status)
        if isinstance(payload, GripperCommand):
            return self._evaluate_gripper(payload, gripper_status)
        return SafetyDecision(False, "Unsupported command payload.")

    def _evaluate_robot(self, command: RobotCommand, status: RobotStatus) -> SafetyDecision:
        """Validate state interlocks for a normalized robot command."""

        if status.emergency_stopped:
            return SafetyDecision(False, "Robot reports an emergency stop.")
        if status.faulted:
            return SafetyDecision(False, "Robot reports a fault.")
        if not status.initialized:
            return SafetyDecision(False, "Robot must be initialized before motion.")
        if command.action.value == "move_joints" and len(command.joint_positions_rad) != 6:
            return SafetyDecision(False, "Joint moves require six joint positions.")
        return SafetyDecision(True, "Command passed deterministic safety checks.")

    def _evaluate_gripper(self, command: GripperCommand, status: GripperStatus) -> SafetyDecision:
        """Validate documented PGI limits and live gripper interlocks."""

        if status.emergency_stopped:
            return SafetyDecision(False, "Gripper reports an emergency stop.")
        if status.faulted:
            return SafetyDecision(False, "Gripper reports a fault.")
        if command.action in (GripperAction.OPEN, GripperAction.CLOSE) and not status.initialized:
            return SafetyDecision(False, "Gripper must be initialized before motion.")
        if command.target_position is not None:
            if not 0 <= command.target_position <= self.limits.maximum_position:
                return SafetyDecision(False, "Target position is outside the configured range.")
        if command.force_percent is not None:
            if not self.limits.minimum_force_percent <= command.force_percent <= self.limits.maximum_force_percent:
                return SafetyDecision(False, "Force is outside the configured range.")
        if command.speed_percent is not None:
            if not self.limits.minimum_speed_percent <= command.speed_percent <= self.limits.maximum_speed_percent:
                return SafetyDecision(False, "Speed is outside the configured range.")
        return SafetyDecision(True, "Command passed deterministic safety checks.")
