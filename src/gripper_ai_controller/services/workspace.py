"""Workspace boundary validation for robot motion safety."""

from dataclasses import dataclass
from typing import Tuple

from gripper_ai_controller.domain.models import Pose3D


@dataclass(frozen=True)
class WorkspaceConstraint:
    """3D workspace boundary constraint definition.

    Defines a rectangular workspace volume in robot base coordinates.
    All coordinates are in millimeters.
    """

    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    z_min_mm: float
    z_max_mm: float
    name: str = "default"

    def __post_init__(self) -> None:
        """Validate constraint consistency."""
        if self.x_min_mm >= self.x_max_mm:
            raise ValueError(f"Invalid X range: {self.x_min_mm} >= {self.x_max_mm}")
        if self.y_min_mm >= self.y_max_mm:
            raise ValueError(f"Invalid Y range: {self.y_min_mm} >= {self.y_max_mm}")
        if self.z_min_mm >= self.z_max_mm:
            raise ValueError(f"Invalid Z range: {self.z_min_mm} >= {self.z_max_mm}")


class WorkspaceValidator:
    """Validates that robot TCP poses remain within safe workspace boundaries.

    This validator checks TCP positions against a defined 3D rectangular workspace.
    It does NOT perform full collision detection or check joint-space singularities.

    Usage:
        constraint = WorkspaceConstraint(
            x_min_mm=-500, x_max_mm=500,
            y_min_mm=-500, y_max_mm=500,
            z_min_mm=0, z_max_mm=800,
            name="table_workspace"
        )
        validator = WorkspaceValidator(constraint)
        valid, message = validator.validate_tcp_pose(robot_status.tcp_pose)
    """

    def __init__(self, constraint: WorkspaceConstraint) -> None:
        """Initialize validator with a workspace constraint."""
        self.constraint = constraint

    def validate_tcp_pose(self, pose: Pose3D) -> Tuple[bool, str]:
        """Check if a TCP pose is within the workspace boundary.

        Args:
            pose: The TCP pose to validate

        Returns:
            Tuple of (is_valid, message):
                - is_valid: True if pose is within bounds
                - message: "OK" if valid, otherwise detailed error message
        """
        if pose.frame_id != "robot_base":
            return False, f"TCP pose must be in robot_base frame, got: {pose.frame_id}"

        # Check X boundary
        if pose.x_mm < self.constraint.x_min_mm:
            return False, (
                f"TCP X坐标 {pose.x_mm:.1f}mm 低于最小值 {self.constraint.x_min_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )
        if pose.x_mm > self.constraint.x_max_mm:
            return False, (
                f"TCP X坐标 {pose.x_mm:.1f}mm 超过最大值 {self.constraint.x_max_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )

        # Check Y boundary
        if pose.y_mm < self.constraint.y_min_mm:
            return False, (
                f"TCP Y坐标 {pose.y_mm:.1f}mm 低于最小值 {self.constraint.y_min_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )
        if pose.y_mm > self.constraint.y_max_mm:
            return False, (
                f"TCP Y坐标 {pose.y_mm:.1f}mm 超过最大值 {self.constraint.y_max_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )

        # Check Z boundary
        if pose.z_mm < self.constraint.z_min_mm:
            return False, (
                f"TCP Z坐标 {pose.z_mm:.1f}mm 低于最小值 {self.constraint.z_min_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )
        if pose.z_mm > self.constraint.z_max_mm:
            return False, (
                f"TCP Z坐标 {pose.z_mm:.1f}mm 超过最大值 {self.constraint.z_max_mm:.1f}mm "
                f"(工作空间: {self.constraint.name})"
            )

        return True, "OK"

    def validate_joint_positions(
        self, joint_positions_rad: Tuple[float, ...]
    ) -> Tuple[bool, str]:
        """Check if joint positions would place TCP within workspace.

        NOTE: This requires forward kinematics which is not yet implemented.
        Currently returns True with a warning message.

        Args:
            joint_positions_rad: Six-axis joint positions in radians

        Returns:
            Tuple of (is_valid, message):
                - is_valid: Always True for now
                - message: Warning that FK is needed
        """
        # TODO: Implement forward kinematics
        # This will require a robot model (JAKA Zu 3 DH parameters)
        return True, "Workspace validation requires forward kinematics (not yet implemented)"

    def get_constraint_info(self) -> str:
        """Return a human-readable description of the workspace constraint."""
        return (
            f"工作空间 '{self.constraint.name}':\n"
            f"  X: [{self.constraint.x_min_mm:.1f}, {self.constraint.x_max_mm:.1f}] mm\n"
            f"  Y: [{self.constraint.y_min_mm:.1f}, {self.constraint.y_max_mm:.1f}] mm\n"
            f"  Z: [{self.constraint.z_min_mm:.1f}, {self.constraint.z_max_mm:.1f}] mm"
        )


# Default workspace constraints for common robot configurations
JAKA_ZU3_DEFAULT_WORKSPACE = WorkspaceConstraint(
    x_min_mm=-500.0,
    x_max_mm=500.0,
    y_min_mm=-500.0,
    y_max_mm=500.0,
    z_min_mm=0.0,  # Table height
    z_max_mm=800.0,  # Maximum reach height
    name="jaka_zu3_default",
)

CONSERVATIVE_TABLE_WORKSPACE = WorkspaceConstraint(
    x_min_mm=-400.0,
    x_max_mm=400.0,
    y_min_mm=-400.0,
    y_max_mm=400.0,
    z_min_mm=50.0,  # Minimum safe height above table
    z_max_mm=600.0,  # Conservative maximum height
    name="conservative_table",
)
