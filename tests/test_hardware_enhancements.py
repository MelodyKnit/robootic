"""Unit tests for hardware control enhancements."""

import unittest

from gripper_ai_controller.adapters.jaka.dry_run import JakaDryRunRobotAdapter
from gripper_ai_controller.adapters.simulation.devices import (
    SimulatedGripperAdapter,
    SimulatedRobotAdapter,
)
from gripper_ai_controller.domain.models import (
    GripperAction,
    GripperCommand,
    Pose3D,
    RobotAction,
    RobotCommand,
    JointMoveMode,
)
from gripper_ai_controller.services.workspace import (
    WorkspaceConstraint,
    WorkspaceValidator,
    JAKA_ZU3_DEFAULT_WORKSPACE,
    CONSERVATIVE_TABLE_WORKSPACE,
)
from gripper_ai_controller.services.motion_limits import (
    MotionLimits,
    MotionLimitsValidator,
    JAKA_ZU3_CONSERVATIVE_LIMITS,
)


class TestEnhancedTelemetry(unittest.TestCase):
    """Test enhanced telemetry data in robot and gripper status."""

    async def test_robot_enhanced_fields(self):
        """Verify robot status includes enhanced telemetry fields."""
        adapter = SimulatedRobotAdapter()
        await adapter.startup()
        await adapter.initialize()

        status = await adapter.get_status()

        # Check new telemetry fields exist and have reasonable values
        self.assertIsNotNone(status.joint_velocities_rad_per_sec)
        self.assertEqual(len(status.joint_velocities_rad_per_sec), 6)
        self.assertIsNotNone(status.joint_torques_nm)
        self.assertEqual(len(status.joint_torques_nm), 6)
        self.assertIsNotNone(status.tcp_velocity)
        self.assertEqual(len(status.tcp_velocity), 6)
        self.assertIsNotNone(status.motion_progress_percent)
        self.assertGreaterEqual(status.motion_progress_percent, 0.0)
        self.assertLessEqual(status.motion_progress_percent, 100.0)
        self.assertIsNotNone(status.temperature_celsius)
        self.assertEqual(len(status.temperature_celsius), 6)
        self.assertIsNotNone(status.payload_kg)
        self.assertFalse(status.collision_detected)

        await adapter.shutdown()

    async def test_gripper_enhanced_fields(self):
        """Verify gripper status includes enhanced telemetry fields."""
        adapter = SimulatedGripperAdapter()
        await adapter.startup()
        await adapter.initialize()

        status = await adapter.get_status()

        # Check new telemetry fields
        self.assertIsNotNone(status.actual_position)
        self.assertEqual(status.actual_position, status.position)
        self.assertIsNotNone(status.actual_force_percent)
        self.assertIsNotNone(status.current_ma)
        self.assertFalse(status.object_detected)
        self.assertTrue(status.position_reached)
        self.assertFalse(status.force_reached)

        await adapter.shutdown()

    async def test_gripper_object_detection_simulation(self):
        """Test that simulated gripper detects objects when closing."""
        adapter = SimulatedGripperAdapter()
        await adapter.startup()
        await adapter.initialize()

        # Close gripper to position 300 (object detected when < 500)
        command = GripperCommand(
            action=GripperAction.CLOSE,
            target_position=300,
            force_percent=50,
        )
        status = await adapter.execute(command)

        # Should detect object
        self.assertTrue(status.object_detected)
        self.assertTrue(status.gripping)
        self.assertEqual(status.actual_position, 300)
        self.assertGreater(status.actual_force_percent, 0)
        self.assertGreater(status.current_ma, 0)

        await adapter.shutdown()


class TestWorkspaceValidator(unittest.TestCase):
    """Test workspace boundary validation."""

    def test_workspace_constraint_validation(self):
        """Test workspace constraint construction and validation."""
        # Valid constraint
        constraint = WorkspaceConstraint(
            x_min_mm=-500, x_max_mm=500,
            y_min_mm=-500, y_max_mm=500,
            z_min_mm=0, z_max_mm=800,
            name="test_workspace"
        )
        self.assertEqual(constraint.name, "test_workspace")

        # Invalid constraint (min >= max)
        with self.assertRaises(ValueError):
            WorkspaceConstraint(
                x_min_mm=500, x_max_mm=-500,
                y_min_mm=-500, y_max_mm=500,
                z_min_mm=0, z_max_mm=800,
            )

    def test_tcp_pose_within_workspace(self):
        """Test validation of TCP pose within workspace."""
        validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

        # Valid pose (within bounds)
        pose = Pose3D(
            x_mm=200.0, y_mm=100.0, z_mm=300.0,
            rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
            frame_id="robot_base"
        )
        is_valid, message = validator.validate_tcp_pose(pose)
        self.assertTrue(is_valid)
        self.assertEqual(message, "OK")

    def test_tcp_pose_outside_workspace_x(self):
        """Test validation rejects TCP pose outside X boundary."""
        validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

        # X too large
        pose = Pose3D(
            x_mm=500.0, y_mm=0.0, z_mm=300.0,
            rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
            frame_id="robot_base"
        )
        is_valid, message = validator.validate_tcp_pose(pose)
        self.assertFalse(is_valid)
        self.assertIn("X坐标", message)
        self.assertIn("超过最大值", message)

    def test_tcp_pose_outside_workspace_z(self):
        """Test validation rejects TCP pose below minimum Z."""
        validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

        # Z too low (below table)
        pose = Pose3D(
            x_mm=0.0, y_mm=0.0, z_mm=10.0,  # Below 50mm minimum
            rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
            frame_id="robot_base"
        )
        is_valid, message = validator.validate_tcp_pose(pose)
        self.assertFalse(is_valid)
        self.assertIn("Z坐标", message)
        self.assertIn("低于最小值", message)

    def test_tcp_pose_wrong_frame(self):
        """Test validation rejects poses not in robot_base frame."""
        validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

        pose = Pose3D(
            x_mm=0.0, y_mm=0.0, z_mm=300.0,
            rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
            frame_id="camera_frame"  # Wrong frame
        )
        is_valid, message = validator.validate_tcp_pose(pose)
        self.assertFalse(is_valid)
        self.assertIn("robot_base frame", message)


class TestMotionLimitsValidator(unittest.TestCase):
    """Test motion limits validation."""

    def test_motion_limits_construction(self):
        """Test motion limits construction and validation."""
        limits = MotionLimits(
            max_joint_velocity_rad_per_sec=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
            max_joint_acceleration_rad_per_sec2=(2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
            max_tcp_velocity_mm_per_sec=500.0,
            max_tcp_acceleration_mm_per_sec2=1000.0,
        )
        self.assertEqual(len(limits.max_joint_velocity_rad_per_sec), 6)

        # Invalid: wrong number of joints
        with self.assertRaises(ValueError):
            MotionLimits(
                max_joint_velocity_rad_per_sec=(1.0, 1.0, 1.0),  # Only 3
                max_joint_acceleration_rad_per_sec2=(2.0, 2.0, 2.0, 2.0, 2.0, 2.0),
                max_tcp_velocity_mm_per_sec=500.0,
                max_tcp_acceleration_mm_per_sec2=1000.0,
            )

    async def test_joint_command_within_limits(self):
        """Test validation of joint command within velocity limits."""
        validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

        adapter = SimulatedRobotAdapter()
        await adapter.startup()
        await adapter.initialize()
        status = await adapter.get_status()

        # Valid command (speed within limit)
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
            speed=0.3,  # Below 0.5 limit
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )
        is_valid, message = validator.validate_joint_command(command, status)
        self.assertTrue(is_valid)
        self.assertEqual(message, "OK")

        await adapter.shutdown()

    async def test_joint_command_exceeds_velocity_limit(self):
        """Test validation rejects joint command exceeding velocity limit."""
        validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

        adapter = SimulatedRobotAdapter()
        await adapter.startup()
        await adapter.initialize()
        status = await adapter.get_status()

        # Invalid command (speed too high)
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
            speed=1.0,  # Exceeds 0.5 limit
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )
        is_valid, message = validator.validate_joint_command(command, status)
        self.assertFalse(is_valid)
        self.assertIn("速度", message)
        self.assertIn("超过", message)

        await adapter.shutdown()

    async def test_joint_step_size_validation(self):
        """Test validation of joint step size limits."""
        validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

        adapter = SimulatedRobotAdapter()
        await adapter.startup()
        await adapter.initialize()
        status = await adapter.get_status()  # All joints at 0.0

        # Large step (exceeds 0.175 rad ~10 degrees)
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=(0.5, 0.0, 0.0, 0.0, 0.0, 0.0),  # 0.5 rad step
            speed=0.3,
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )
        is_valid, message = validator.validate_joint_command(command, status)
        self.assertFalse(is_valid)
        self.assertIn("单步运动", message)
        self.assertIn("超过最大单步限制", message)

        await adapter.shutdown()

    async def test_motion_duration_estimation(self):
        """Test motion duration estimation."""
        validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

        adapter = SimulatedRobotAdapter()
        await adapter.startup()
        await adapter.initialize()
        status = await adapter.get_status()

        # Move joint 1 by 0.1 rad at 0.5 rad/s
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
            speed=0.5,
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )
        duration = validator.estimate_motion_duration(command, status)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, 0.2, places=2)  # 0.1 rad / 0.5 rad/s = 0.2 s

        await adapter.shutdown()


class TestDryRunAdapterEnhancements(unittest.TestCase):
    """Test dry-run adapter with enhanced telemetry."""

    async def test_dry_run_initialization(self):
        """Test dry-run adapter initializes with enhanced telemetry."""
        adapter = JakaDryRunRobotAdapter()
        await adapter.startup()
        await adapter.initialize()

        status = await adapter.get_status()

        # Check enhanced fields are present
        self.assertIsNotNone(status.joint_velocities_rad_per_sec)
        self.assertEqual(status.joint_velocities_rad_per_sec, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertIsNotNone(status.tcp_velocity)
        self.assertEqual(status.motion_progress_percent, 0.0)
        self.assertIsNotNone(status.temperature_celsius)

        await adapter.shutdown()

    async def test_dry_run_motion_updates_telemetry(self):
        """Test dry-run adapter updates telemetry after motion."""
        adapter = JakaDryRunRobotAdapter()
        await adapter.startup()
        await adapter.initialize()

        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
            speed=0.5,
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )
        status = await adapter.execute(command)

        # After motion, velocities should be zero and progress 100%
        self.assertEqual(status.joint_velocities_rad_per_sec, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(status.motion_progress_percent, 100.0)
        self.assertFalse(status.moving)

        await adapter.shutdown()


if __name__ == "__main__":
    # Run async tests
    import asyncio

    async def run_async_tests():
        """Run all async test methods."""
        suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
        for test_group in suite:
            for test in test_group:
                test_method = getattr(test, test._testMethodName)
                if asyncio.iscoroutinefunction(test_method):
                    print(f"Running {test._testMethodName}...")
                    try:
                        await test_method()
                        print(f"✓ {test._testMethodName} passed")
                    except AssertionError as e:
                        print(f"✗ {test._testMethodName} failed: {e}")
                    except Exception as e:
                        print(f"✗ {test._testMethodName} error: {e}")

    asyncio.run(run_async_tests())
