"""
全自动相机内参标定

基于现有的 ChArUco 标定基础设施，提供机械臂自动运动采集标定图像的能力。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional
import numpy as np

from gripper_ai_controller.calibration.camera import (
    detect_charuco_observation,
    calibrate_charuco_camera,
    CameraCalibrationInput,
)
from gripper_ai_controller.calibration.models import (
    CharucoBoardSpec,
    DEFAULT_CHARUCO_BOARD,
    CharucoObservation,
)
from gripper_ai_controller.domain.models import RobotCommand, Pose3D


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalibrationPose:
    """标定采集位姿"""
    x: float  # TCP position (mm)
    y: float
    z: float
    rx: float  # TCP orientation (rad)
    ry: float
    rz: float
    description: str  # "正面中心", "左侧倾斜", etc.


@dataclass
class AutoCalibrationConfig:
    """自动标定配置"""
    # 工作空间配置
    workspace_center: Tuple[float, float, float] = (300.0, 0.0, 100.0)  # (x, y, z) mm

    # 采集参数
    min_images: int = 25  # 最少图像数（ChArUco推荐25张）
    target_images: int = 30  # 目标图像数
    capture_stabilization_sec: float = 0.5  # 运动后稳定时间

    # 运动参数
    move_velocity: float = 0.1  # 运动速度（归一化，0-1）
    move_acceleration: float = 0.1  # 运动加速度
    move_timeout_sec: float = 10.0  # 运动超时
    position_tolerance_mm: float = 5.0  # 位置到达容差

    # 标定板参数
    board_spec: CharucoBoardSpec = DEFAULT_CHARUCO_BOARD


class AutoIntrinsicCalibration:
    """全自动相机内参标定器"""

    def __init__(
        self,
        robot_adapter,
        vision_adapter,
        config: Optional[AutoCalibrationConfig] = None,
    ):
        """
        初始化自动标定器

        Args:
            robot_adapter: 机器人适配器（需要支持 execute_command 和 get_status）
            vision_adapter: 视觉适配器（需要支持 capture）
            config: 标定配置（可选，使用默认配置）
        """
        self.robot = robot_adapter
        self.vision = vision_adapter
        self.config = config or AutoCalibrationConfig()

        # 自动生成采集位姿序列
        self.capture_poses = self._generate_capture_poses()
        logger.info(f"生成 {len(self.capture_poses)} 个标定采集位姿")

    def _generate_capture_poses(self) -> List[CalibrationPose]:
        """
        自动生成采集位姿序列

        策略：在工作空间中心周围生成多个观测角度
        - 5个不同距离（近→远）
        - 每个距离多个角度（正面、左、右、前、后）
        - 覆盖画面的不同区域
        """
        poses = []
        cx, cy, cz = self.config.workspace_center

        # 距离序列：从近到远（相机到标定板的距离）
        distances = [200, 250, 300, 350, 400]  # mm

        for i, dist in enumerate(distances):
            # 1. 正面中心位置（直视）
            poses.append(CalibrationPose(
                x=cx,
                y=cy,
                z=cz + dist,
                rx=np.pi,  # 向下看
                ry=0.0,
                rz=0.0,
                description=f"正面中心-{dist}mm"
            ))

            # 2. 左侧倾斜
            poses.append(CalibrationPose(
                x=cx - 80,
                y=cy,
                z=cz + dist,
                rx=np.pi - 0.3,  # 向下并向右倾斜
                ry=0.0,
                rz=0.3,
                description=f"左侧倾斜-{dist}mm"
            ))

            # 3. 右侧倾斜
            poses.append(CalibrationPose(
                x=cx + 80,
                y=cy,
                z=cz + dist,
                rx=np.pi - 0.3,
                ry=0.0,
                rz=-0.3,
                description=f"右侧倾斜-{dist}mm"
            ))

            # 4. 前方倾斜
            poses.append(CalibrationPose(
                x=cx,
                y=cy - 80,
                z=cz + dist,
                rx=np.pi - 0.3,
                ry=0.3,
                rz=0.0,
                description=f"前方倾斜-{dist}mm"
            ))

            # 5. 后方倾斜
            poses.append(CalibrationPose(
                x=cx,
                y=cy + 80,
                z=cz + dist,
                rx=np.pi - 0.3,
                ry=-0.3,
                rz=0.0,
                description=f"后方倾斜-{dist}mm"
            ))

            # 6. 对角倾斜（仅在中间距离添加）
            if i == 2:  # 300mm距离
                poses.append(CalibrationPose(
                    x=cx - 60,
                    y=cy - 60,
                    z=cz + dist,
                    rx=np.pi - 0.25,
                    ry=0.2,
                    rz=0.2,
                    description=f"对角倾斜1-{dist}mm"
                ))
                poses.append(CalibrationPose(
                    x=cx + 60,
                    y=cy + 60,
                    z=cz + dist,
                    rx=np.pi - 0.25,
                    ry=-0.2,
                    rz=-0.2,
                    description=f"对角倾斜2-{dist}mm"
                ))

        return poses

    async def run(
        self,
        calibration_id: str,
        camera_id: str,
        output_path: str,
        save_images: bool = False,
    ) -> dict:
        """
        执行全自动标定流程

        Args:
            calibration_id: 标定ID（唯一标识符）
            camera_id: 相机ID
            output_path: 输出JSON路径
            save_images: 是否保存采集的图像（用于调试）

        Returns:
            标定结果字典
        """
        logger.info("=" * 60)
        logger.info("🤖 开始自动相机内参标定")
        logger.info("=" * 60)

        # 阶段1: 准备
        logger.info("\n[阶段1] 准备机械臂和相机...")
        await self._prepare()

        # 阶段2: 自动采集
        logger.info(f"\n[阶段2] 自动采集标定图像（目标: {self.config.target_images} 张）...")
        observations, image_size = await self._auto_capture(save_images, output_path)

        if len(observations) < self.config.min_images:
            raise RuntimeError(
                f"采集失败：只获得 {len(observations)} 张有效图像"
                f"（需要至少 {self.config.min_images} 张）"
            )

        logger.info(f"✓ 成功采集 {len(observations)} 个有效观测")

        # 阶段3: 计算标定
        logger.info("\n[阶段3] 计算相机内参...")
        result = self._calibrate(
            calibration_id=calibration_id,
            camera_id=camera_id,
            observations=observations,
            image_width=image_size[0],
            image_height=image_size[1],
        )

        # 阶段4: 保存结果
        logger.info(f"\n[阶段4] 保存标定结果到 {output_path}...")
        self._save_result(result, output_path)

        # 阶段5: 返回安全位置
        logger.info("\n[阶段5] 机械臂返回安全位置...")
        await self._return_home()

        logger.info("\n" + "=" * 60)
        logger.info("✅ 自动标定完成！")
        logger.info("=" * 60)
        logger.info(f"重投影误差: {result.reprojection_error_px:.3f} 像素")
        logger.info(f"有效观测数: {result.views_used}")
        logger.info(f"焦距 fx: {result.intrinsics.fx_px:.2f}")
        logger.info(f"焦距 fy: {result.intrinsics.fy_px:.2f}")
        logger.info(f"主点 cx: {result.intrinsics.cx_px:.2f}")
        logger.info(f"主点 cy: {result.intrinsics.cy_px:.2f}")
        logger.info("=" * 60)

        return result.to_dict()

    async def _prepare(self):
        """准备阶段：检查机械臂和相机状态"""
        # 1. 检查机械臂
        status = await self.robot.get_status()
        if not status.initialized:
            raise RuntimeError("机械臂未初始化")
        if not status.enabled:
            raise RuntimeError("机械臂未使能")

        logger.info(f"✓ 机械臂状态正常: {status.tcp_pose.frame_id}")

        # 2. 检查相机
        frame = await self.vision.capture()
        if frame is None or frame.data is None:
            raise RuntimeError("相机无法采集图像")

        logger.info(f"✓ 相机状态正常: {frame.width}x{frame.height}")

    async def _auto_capture(
        self,
        save_images: bool,
        output_path: str,
    ) -> Tuple[List[CharucoObservation], Tuple[int, int]]:
        """
        自动采集标定图像

        Returns:
            (观测列表, 图像尺寸)
        """
        observations = []
        image_size = None
        images_dir = None

        if save_images:
            images_dir = Path(output_path).parent / "calibration_images"
            images_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"图像保存目录: {images_dir}")

        for i, pose in enumerate(self.capture_poses):
            # 如果已经采集足够的图像，提前结束
            if len(observations) >= self.config.target_images:
                logger.info(f"✓ 已达到目标图像数 ({self.config.target_images})，停止采集")
                break

            logger.info(f"\n[{i+1}/{len(self.capture_poses)}] 位姿: {pose.description}")

            # 1. 移动到目标位姿
            success = await self._move_to_pose(pose)
            if not success:
                logger.warning(f"  ⚠️ 移动失败，跳过此位姿")
                continue

            # 2. 等待稳定
            await asyncio.sleep(self.config.capture_stabilization_sec)

            # 3. 采集图像
            frame = await self.vision.capture()
            if frame is None or frame.data is None:
                logger.warning(f"  ⚠️ 图像采集失败，跳过此位姿")
                continue

            # 记录图像尺寸
            if image_size is None:
                image_size = (frame.width, frame.height)

            # 4. 检测 ChArUco 标定板
            img_array = self._frame_to_array(frame)
            observation = detect_charuco_observation(img_array, self.config.board_spec)

            if observation is not None:
                observations.append(observation)
                logger.info(f"  ✓ 检测到标定板，角点数: {len(observation.corners)}")
                logger.info(f"  ✓ 进度: {len(observations)}/{self.config.target_images}")

                # 保存图像（如果启用）
                if save_images and images_dir:
                    import cv2
                    img_path = images_dir / f"calib_{len(observations):03d}.png"
                    cv2.imwrite(str(img_path), img_array)
            else:
                logger.warning(f"  ⚠️ 未检测到标定板，跳过此位姿")

        if image_size is None:
            raise RuntimeError("未能采集任何有效图像")

        return observations, image_size

    async def _move_to_pose(self, pose: CalibrationPose) -> bool:
        """
        移动机械臂到指定位姿

        Returns:
            是否成功
        """
        try:
            # 构造运动命令
            target_pose = Pose3D(
                x=pose.x,
                y=pose.y,
                z=pose.z,
                rx=pose.rx,
                ry=pose.ry,
                rz=pose.rz,
                frame_id="robot_base"
            )

            command = RobotCommand(
                command_type="MOVE_LINEAR",
                target_pose=target_pose,
                velocity=self.config.move_velocity,
                acceleration=self.config.move_acceleration
            )

            # 执行运动
            await self.robot.execute_command(command)

            # 等待运动完成
            start_time = asyncio.get_event_loop().time()

            while True:
                status = await self.robot.get_status()

                # 检查是否到达
                current_pose = status.tcp_pose
                distance = np.linalg.norm([
                    current_pose.x - target_pose.x,
                    current_pose.y - target_pose.y,
                    current_pose.z - target_pose.z
                ])

                if distance < self.config.position_tolerance_mm:
                    logger.debug(f"  到达目标位置，误差: {distance:.2f}mm")
                    return True

                # 检查超时
                if asyncio.get_event_loop().time() - start_time > self.config.move_timeout_sec:
                    logger.warning(f"  运动超时（{self.config.move_timeout_sec}s）")
                    return False

                await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"  运动异常: {e}")
            return False

    def _calibrate(
        self,
        calibration_id: str,
        camera_id: str,
        observations: List[CharucoObservation],
        image_width: int,
        image_height: int,
    ):
        """计算相机内参"""
        # 构造标定输入
        calibration_input = CameraCalibrationInput(
            calibration_id=calibration_id,
            camera_id=camera_id,
            board_spec=self.config.board_spec,
            image_width_px=image_width,
            image_height_px=image_height,
            observations=tuple(observations),
            minimum_views=self.config.min_images,
        )

        # 执行标定计算
        result = calibrate_charuco_camera(calibration_input)

        return result

    def _save_result(self, result, output_path: str):
        """保存标定结果"""
        import json

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"✓ 标定结果已保存: {output_path}")

    async def _return_home(self):
        """返回安全位置"""
        try:
            # 移动到工作空间中心上方的安全位置
            cx, cy, cz = self.config.workspace_center
            safe_pose = Pose3D(
                x=cx,
                y=cy,
                z=cz + 400.0,  # 上方400mm
                rx=np.pi,
                ry=0.0,
                rz=0.0,
                frame_id="robot_base"
            )

            command = RobotCommand(
                command_type="MOVE_LINEAR",
                target_pose=safe_pose,
                velocity=0.2,
                acceleration=0.2
            )

            await self.robot.execute_command(command)
            await asyncio.sleep(2.0)  # 等待到达

            logger.info("✓ 已返回安全位置")
        except Exception as e:
            logger.warning(f"返回安全位置失败: {e}")

    def _frame_to_array(self, frame) -> np.ndarray:
        """将Frame对象转换为numpy数组"""
        # frame.data 应该已经是 numpy 数组
        return frame.data
