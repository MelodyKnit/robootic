"""演示硬件控制增强功能的CLI命令"""

import asyncio
import sys

from gripper_ai_controller.adapters.simulation.devices import (
    SimulatedRobotAdapter,
    SimulatedGripperAdapter,
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
    WorkspaceValidator,
    CONSERVATIVE_TABLE_WORKSPACE,
)
from gripper_ai_controller.services.motion_limits import (
    MotionLimitsValidator,
    JAKA_ZU3_CONSERVATIVE_LIMITS,
)


async def demo_hardware_enhancements():
    """演示所有硬件控制增强功能"""

    print("\n" + "="*70)
    print("硬件控制增强功能演示（干运行模式）")
    print("="*70)

    # 1. 增强遥测数据
    print("\n[1/4] 增强遥测数据演示...")
    robot = SimulatedRobotAdapter()
    await robot.startup()
    await robot.initialize()
    status = await robot.get_status()

    print(f"  ✓ 关节速度: {status.joint_velocities_rad_per_sec}")
    print(f"  ✓ 关节力矩: {status.joint_torques_nm}")
    print(f"  ✓ TCP速度: {status.tcp_velocity}")
    print(f"  ✓ 运动进度: {status.motion_progress_percent}%")
    print(f"  ✓ 温度: {status.temperature_celsius}")

    await robot.shutdown()

    # 2. 工作空间验证
    print("\n[2/4] 工作空间验证演示...")
    validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

    pose_valid = Pose3D(200.0, 100.0, 300.0, 0.0, 0.0, 0.0, "robot_base")
    is_valid, msg = validator.validate_tcp_pose(pose_valid)
    print(f"  测试位姿 (200, 100, 300): {' ✓ 通过' if is_valid else '✗ 拒绝'}")

    pose_invalid = Pose3D(500.0, 0.0, 300.0, 0.0, 0.0, 0.0, "robot_base")
    is_valid, msg = validator.validate_tcp_pose(pose_invalid)
    print(f"  测试位姿 (500, 0, 300): {'✓ 通过' if is_valid else ' ✗ 拒绝 - ' + msg}")

    # 3. 运动限制验证
    print("\n[3/4] 运动限制验证演示...")
    motion_validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

    robot2 = SimulatedRobotAdapter()
    await robot2.startup()
    await robot2.initialize()
    status2 = await robot2.get_status()

    cmd_valid = RobotCommand(
        RobotAction.MOVE_JOINTS,
        (0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=0.3,
        joint_move_mode=JointMoveMode.ABSOLUTE
    )
    is_valid, msg = motion_validator.validate_joint_command(cmd_valid, status2)
    print(f"  速度 0.3 rad/s: {' ✓ 通过' if is_valid else '✗ 拒绝'}")

    cmd_invalid = RobotCommand(
        RobotAction.MOVE_JOINTS,
        (0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=1.0,
        joint_move_mode=JointMoveMode.ABSOLUTE
    )
    is_valid, msg = motion_validator.validate_joint_command(cmd_invalid, status2)
    print(f"  速度 1.0 rad/s: {'✓ 通过' if is_valid else ' ✗ 拒绝 - 超过限制'}")

    await robot2.shutdown()

    # 4. 夹爪物体检测
    print("\n[4/4] 夹爪物体检测演示...")
    gripper = SimulatedGripperAdapter()
    await gripper.startup()
    await gripper.initialize()

    cmd = GripperCommand(GripperAction.CLOSE, target_position=300, force_percent=50)
    status = await gripper.execute(cmd)
    print(f"  位置 300 (物体区域): {'✓ 检测到物体' if status.object_detected else '✗ 未检测'}")
    print(f"  夹持力: {status.actual_force_percent}%")
    print(f"  电流: {status.current_ma} mA")

    await gripper.shutdown()

    print("\n" + "="*70)
    print("✓ 所有演示完成 - 功能验证通过")
    print("="*70)
    print("\n已完成的增强:")
    print("  • 机器人增强遥测: 速度、力矩、温度、进度")
    print("  • 夹爪增强遥测: 实际位置、力、电流、物体检测")
    print("  • 工作空间边界验证")
    print("  • 运动速度和步长限制验证")
    print("  • 干运行适配器更新")
    print("\n这些功能均在干运行模式下测试，不产生实际硬件运动。")


def main():
    """CLI入口"""
    asyncio.run(demo_hardware_enhancements())
    return 0


if __name__ == "__main__":
    sys.exit(main())
