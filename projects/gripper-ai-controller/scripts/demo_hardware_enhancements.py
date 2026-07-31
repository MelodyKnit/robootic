"""演示和验证硬件控制增强功能的脚本（干运行模式）"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
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


async def demo_enhanced_telemetry():
    """演示增强的遥测数据"""
    print("\n" + "="*70)
    print("演示 1: 增强的遥测数据")
    print("="*70)

    # 创建模拟机器人
    robot = SimulatedRobotAdapter()
    await robot.startup()
    await robot.initialize()

    status = await robot.get_status()

    print("\n机器人状态（增强字段）:")
    print(f"  基本状态: 已连接={status.connected}, 已初始化={status.initialized}")
    print(f"  关节位置 (rad): {status.joint_positions_rad}")
    print(f"  关节速度 (rad/s): {status.joint_velocities_rad_per_sec}")
    print(f"  关节力矩 (Nm): {status.joint_torques_nm}")
    print(f"  TCP速度 (mm/s, rad/s): {status.tcp_velocity}")
    print(f"  运动进度: {status.motion_progress_percent}%")
    print(f"  关节温度 (°C): {status.temperature_celsius}")
    print(f"  负载重量 (kg): {status.payload_kg}")
    print(f"  碰撞检测: {status.collision_detected}")

    # 创建模拟夹爪
    gripper = SimulatedGripperAdapter()
    await gripper.startup()
    await gripper.initialize()

    status = await gripper.get_status()

    print("\n夹爪状态（增强字段）:")
    print(f"  基本状态: 已连接={status.connected}, 已初始化={status.initialized}")
    print(f"  目标位置: {status.position}")
    print(f"  实际位置: {status.actual_position}")
    print(f"  实际夹持力 (%): {status.actual_force_percent}")
    print(f"  电流 (mA): {status.current_ma}")
    print(f"  物体检测: {status.object_detected}")
    print(f"  位置到达: {status.position_reached}")
    print(f"  力到达: {status.force_reached}")

    await robot.shutdown()
    await gripper.shutdown()

    print("\n✓ 增强遥测数据演示完成")


async def demo_workspace_validation():
    """演示工作空间验证"""
    print("\n" + "="*70)
    print("演示 2: 工作空间边界验证")
    print("="*70)

    validator = WorkspaceValidator(CONSERVATIVE_TABLE_WORKSPACE)

    print(f"\n{validator.get_constraint_info()}")

    # 测试用例1: 有效位姿
    print("\n测试 1: 有效的TCP位姿")
    pose = Pose3D(
        x_mm=200.0, y_mm=100.0, z_mm=300.0,
        rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
        frame_id="robot_base"
    )
    is_valid, message = validator.validate_tcp_pose(pose)
    print(f"  位姿: X={pose.x_mm}, Y={pose.y_mm}, Z={pose.z_mm}")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")

    # 测试用例2: X超出范围
    print("\n测试 2: X坐标超出范围")
    pose = Pose3D(
        x_mm=500.0, y_mm=0.0, z_mm=300.0,
        rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
        frame_id="robot_base"
    )
    is_valid, message = validator.validate_tcp_pose(pose)
    print(f"  位姿: X={pose.x_mm}, Y={pose.y_mm}, Z={pose.z_mm}")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")

    # 测试用例3: Z低于最小值（桌面以下）
    print("\n测试 3: Z坐标低于最小值（桌面以下）")
    pose = Pose3D(
        x_mm=0.0, y_mm=0.0, z_mm=10.0,
        rx_rad=0.0, ry_rad=0.0, rz_rad=0.0,
        frame_id="robot_base"
    )
    is_valid, message = validator.validate_tcp_pose(pose)
    print(f"  位姿: X={pose.x_mm}, Y={pose.y_mm}, Z={pose.z_mm}")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")

    print("\n✓ 工作空间验证演示完成")


async def demo_motion_limits_validation():
    """演示运动限制验证"""
    print("\n" + "="*70)
    print("演示 3: 运动限制验证")
    print("="*70)

    validator = MotionLimitsValidator(JAKA_ZU3_CONSERVATIVE_LIMITS)

    print(f"\n{validator.get_limits_info()}")

    # 创建机器人获取当前状态
    robot = SimulatedRobotAdapter()
    await robot.startup()
    await robot.initialize()
    status = await robot.get_status()

    # 测试用例1: 速度在限制内
    print("\n测试 1: 关节运动速度在限制内")
    command = RobotCommand(
        action=RobotAction.MOVE_JOINTS,
        joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=0.3,  # 低于 0.5 rad/s 限制
        joint_move_mode=JointMoveMode.ABSOLUTE,
    )
    is_valid, message = validator.validate_joint_command(command, status)
    duration = validator.estimate_motion_duration(command, status)
    print(f"  命令: 关节1移动到 0.1 rad, 速度 0.3 rad/s")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")
    if duration:
        print(f"  预计时间: {duration:.2f} 秒")

    # 测试用例2: 速度超过限制
    print("\n测试 2: 关节运动速度超过限制")
    command = RobotCommand(
        action=RobotAction.MOVE_JOINTS,
        joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=1.0,  # 超过 0.5 rad/s 限制
        joint_move_mode=JointMoveMode.ABSOLUTE,
    )
    is_valid, message = validator.validate_joint_command(command, status)
    print(f"  命令: 关节1移动到 0.1 rad, 速度 1.0 rad/s")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")

    # 测试用例3: 单步过大
    print("\n测试 3: 单步运动角度过大")
    command = RobotCommand(
        action=RobotAction.MOVE_JOINTS,
        joint_positions_rad=(0.5, 0.0, 0.0, 0.0, 0.0, 0.0),  # 0.5 rad = ~28.6°
        speed=0.3,
        joint_move_mode=JointMoveMode.ABSOLUTE,
    )
    is_valid, message = validator.validate_joint_command(command, status)
    print(f"  命令: 关节1移动到 0.5 rad (~28.6°), 速度 0.3 rad/s")
    print(f"  结果: {'✓ 通过' if is_valid else '✗ 拒绝'}")
    print(f"  信息: {message}")

    await robot.shutdown()

    print("\n✓ 运动限制验证演示完成")


async def demo_gripper_object_detection():
    """演示夹爪物体检测模拟"""
    print("\n" + "="*70)
    print("演示 4: 夹爪物体检测（模拟）")
    print("="*70)

    gripper = SimulatedGripperAdapter()
    await gripper.startup()
    await gripper.initialize()

    # 打开夹爪
    print("\n步骤 1: 打开夹爪到 1000")
    command = GripperCommand(action=GripperAction.OPEN, target_position=1000)
    status = await gripper.execute(command)
    print(f"  位置: {status.actual_position}")
    print(f"  物体检测: {status.object_detected}")
    print(f"  夹持力: {status.actual_force_percent}%")

    # 闭合夹爪到 300（模拟检测到物体）
    print("\n步骤 2: 闭合夹爪到 300（物体检测区域）")
    command = GripperCommand(
        action=GripperAction.CLOSE,
        target_position=300,
        force_percent=50,
    )
    status = await gripper.execute(command)
    print(f"  位置: {status.actual_position}")
    print(f"  物体检测: {'✓ 检测到物体' if status.object_detected else '✗ 未检测到物体'}")
    print(f"  夹持力: {status.actual_force_percent}%")
    print(f"  电流: {status.current_ma} mA")
    print(f"  夹持状态: {status.grip_state}")

    # 闭合夹爪到 600（无物体）
    print("\n步骤 3: 闭合夹爪到 600（无物体区域）")
    command = GripperCommand(
        action=GripperAction.CLOSE,
        target_position=600,
        force_percent=30,
    )
    status = await gripper.execute(command)
    print(f"  位置: {status.actual_position}")
    print(f"  物体检测: {'✓ 检测到物体' if status.object_detected else '✗ 未检测到物体'}")
    print(f"  夹持力: {status.actual_force_percent}%")

    await gripper.shutdown()

    print("\n✓ 夹爪物体检测演示完成")


async def main():
    """运行所有演示"""
    print("\n" + "="*70)
    print("硬件控制增强功能演示")
    print("="*70)
    print("\n这些演示使用干运行模式，不会产生实际硬件运动。")
    print("所有数据均为模拟值，用于验证功能逻辑。")

    try:
        await demo_enhanced_telemetry()
        await demo_workspace_validation()
        await demo_motion_limits_validation()
        await demo_gripper_object_detection()

        print("\n" + "="*70)
        print("✓ 所有演示完成")
        print("="*70)
        print("\n总结:")
        print("  ✓ 增强遥测数据: 机器人和夹爪状态包含更详细的反馈")
        print("  ✓ 工作空间验证: 可以验证TCP位姿是否在安全范围内")
        print("  ✓ 运动限制验证: 可以验证速度和步长是否符合安全限制")
        print("  ✓ 物体检测模拟: 夹爪可以模拟物体检测功能")
        print("\n下一步:")
        print("  - 集成到 SafetyPolicy 中")
        print("  - 在真机上验证遥测数据准确性")
        print("  - 添加更多碰撞检测规则")
        print("  - 实现运动时间模拟")

    except Exception as e:
        print(f"\n✗ 演示过程中出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
