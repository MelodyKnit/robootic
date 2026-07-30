"""Motion limits validation for robot velocity and acceleration safety."""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import RobotCommand, RobotStatus, RobotAction


@dataclass(frozen=True)
class MotionLimits:
    """Motion velocity and acceleration limits definition.

    All velocities are in rad/s for joints and mm/s for TCP.
    All accelerations are in rad/s² for joints and mm/s² for TCP.
    """

    max_joint_velocity_rad_per_sec: Tuple[float, float, float, float, float, float]
    max_joint_acceleration_rad_per_sec2: Tuple[float, float, float, float, float, float]
    max_tcp_velocity_mm_per_sec: float
    max_tcp_acceleration_mm_per_sec2: float
    max_joint_step_rad: float = 0.175  # ~10 degrees per command

    def __post_init__(self) -> None:
        """Validate limits consistency."""
        if len(self.max_joint_velocity_rad_per_sec) != 6:
            raise ValueError("max_joint_velocity_rad_per_sec must have 6 values")
        if len(self.max_joint_acceleration_rad_per_sec2) != 6:
            raise ValueError("max_joint_acceleration_rad_per_sec2 must have 6 values")

        for i, vel in enumerate(self.max_joint_velocity_rad_per_sec):
            if vel <= 0:
                raise ValueError(f"Joint {i+1} max velocity must be positive, got {vel}")

        if self.max_tcp_velocity_mm_per_sec <= 0:
            raise ValueError(f"TCP max velocity must be positive, got {self.max_tcp_velocity_mm_per_sec}")


class MotionLimitsValidator:
    """Validates that robot commands respect velocity and acceleration limits.

    This validator checks commanded velocities and step sizes against configured limits.
    It does NOT compute actual accelerations (requires motion history).

    Usage:
        limits = JAKA_ZU3_CONSERVATIVE_LIMITS
        validator = MotionLimitsValidator(limits)
        valid, message = validator.validate_joint_command(command, current_status)
    """

    def __init__(self, limits: MotionLimits) -> None:
        """Initialize validator with motion limits."""
        self.limits = limits

    def validate_joint_command(
        self,
        command: RobotCommand,
        current_status: RobotStatus,
    ) -> Tuple[bool, str]:
        """Validate a joint motion command against velocity and step limits.

        Args:
            command: The robot command to validate
            current_status: Current robot status for step size calculation

        Returns:
            Tuple of (is_valid, message):
                - is_valid: True if command respects all limits
                - message: "OK" if valid, otherwise detailed error message
        """
        if command.action != RobotAction.MOVE_JOINTS:
            return True, "Not a joint motion command"

        # Check commanded velocity
        if command.speed > 0:
            for i, max_vel in enumerate(self.limits.max_joint_velocity_rad_per_sec):
                if command.speed > max_vel:
                    return False, (
                        f"关节运动速度 {command.speed:.3f} rad/s 超过关节 {i+1} 的限制 {max_vel:.3f} rad/s"
                    )

        # Check joint step sizes (only if we have current positions)
        if len(command.joint_positions_rad) != 6:
            return False, f"关节运动命令必须包含6个关节角度，收到 {len(command.joint_positions_rad)} 个"

        if len(current_status.joint_positions_rad) == 6:
            for i, (target, current) in enumerate(
                zip(command.joint_positions_rad, current_status.joint_positions_rad)
            ):
                if not math.isfinite(target):
                    return False, f"关节 {i+1} 目标角度无效: {target}"

                step = abs(target - current)
                if step > self.limits.max_joint_step_rad:
                    return False, (
                        f"关节 {i+1} 单步运动 {step:.3f} rad ({math.degrees(step):.1f}°) "
                        f"超过最大单步限制 {self.limits.max_joint_step_rad:.3f} rad "
                        f"({math.degrees(self.limits.max_joint_step_rad):.1f}°)"
                    )

        return True, "OK"

    def validate_linear_command(
        self,
        command: RobotCommand,
        current_status: RobotStatus,
    ) -> Tuple[bool, str]:
        """Validate a linear motion command against TCP velocity limits.

        Args:
            command: The robot command to validate
            current_status: Current robot status for distance calculation

        Returns:
            Tuple of (is_valid, message):
                - is_valid: True if command respects TCP velocity limit
                - message: "OK" if valid, otherwise detailed error message
        """
        if command.action != RobotAction.MOVE_LINEAR:
            return True, "Not a linear motion command"

        if command.target_pose is None:
            return False, "直线运动命令缺少目标位姿"

        # Check commanded TCP velocity
        if command.speed > 0:
            if command.speed > self.limits.max_tcp_velocity_mm_per_sec:
                return False, (
                    f"TCP直线运动速度 {command.speed:.1f} mm/s "
                    f"超过限制 {self.limits.max_tcp_velocity_mm_per_sec:.1f} mm/s"
                )

        return True, "OK"

    def estimate_motion_duration(
        self,
        command: RobotCommand,
        current_status: RobotStatus,
    ) -> Optional[float]:
        """Estimate the duration of a motion command in seconds.

        This is a simple estimation based on distance and speed.
        It does NOT account for acceleration, deceleration, or actual trajectory.

        Args:
            command: The robot command to estimate
            current_status: Current robot status

        Returns:
            Estimated duration in seconds, or None if cannot estimate
        """
        if command.action == RobotAction.MOVE_JOINTS:
            if command.speed <= 0 or len(current_status.joint_positions_rad) != 6:
                return None

            # Find the joint with maximum angular distance
            max_distance = 0.0
            for target, current in zip(command.joint_positions_rad, current_status.joint_positions_rad):
                distance = abs(target - current)
                max_distance = max(max_distance, distance)

            return max_distance / command.speed

        elif command.action == RobotAction.MOVE_LINEAR:
            if command.target_pose is None or command.speed <= 0:
                return None

            # Calculate Euclidean distance
            dx = command.target_pose.x_mm - current_status.tcp_pose.x_mm
            dy = command.target_pose.y_mm - current_status.tcp_pose.y_mm
            dz = command.target_pose.z_mm - current_status.tcp_pose.z_mm
            distance = math.sqrt(dx**2 + dy**2 + dz**2)

            return distance / command.speed

        return None

    def get_limits_info(self) -> str:
        """Return a human-readable description of the motion limits."""
        lines = ["运动限制:"]
        lines.append("  关节速度限制 (rad/s):")
        for i, vel in enumerate(self.limits.max_joint_velocity_rad_per_sec):
            lines.append(f"    J{i+1}: {vel:.3f} rad/s ({math.degrees(vel):.1f} °/s)")
        lines.append(f"  TCP最大速度: {self.limits.max_tcp_velocity_mm_per_sec:.1f} mm/s")
        lines.append(f"  单步最大角度: {self.limits.max_joint_step_rad:.3f} rad ({math.degrees(self.limits.max_joint_step_rad):.1f}°)")
        return "\n".join(lines)


# Default motion limits for JAKA Zu 3 (conservative values)
JAKA_ZU3_CONSERVATIVE_LIMITS = MotionLimits(
    max_joint_velocity_rad_per_sec=(0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
    max_joint_acceleration_rad_per_sec2=(2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
    max_tcp_velocity_mm_per_sec=200.0,  # 20 cm/s
    max_tcp_acceleration_mm_per_sec2=500.0,
    max_joint_step_rad=0.175,  # ~10 degrees
)

# More aggressive limits for faster operation (use with caution)
JAKA_ZU3_NORMAL_LIMITS = MotionLimits(
    max_joint_velocity_rad_per_sec=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    max_joint_acceleration_rad_per_sec2=(3.0, 3.0, 3.0, 3.0, 3.0, 3.0),
    max_tcp_velocity_mm_per_sec=500.0,  # 50 cm/s
    max_tcp_acceleration_mm_per_sec2=1000.0,
    max_joint_step_rad=0.349,  # ~20 degrees
)
