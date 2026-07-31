"""
自动标定插件 - Web集成

提供可视化、交互式的相机标定功能。
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum

from gripper_ai_controller.core.components import Plugin, ComponentManifest
from gripper_ai_controller.calibration.auto_intrinsic import (
    AutoIntrinsicCalibration,
    AutoCalibrationConfig,
)


class CalibrationPhase(str, Enum):
    """标定阶段"""
    IDLE = "idle"
    PREPARING = "preparing"
    INTRINSIC = "intrinsic"
    HAND_EYE = "handeye"
    VALIDATING = "validating"
    COMPLETE = "complete"
    ERROR = "error"


class CalibrationState(str, Enum):
    """标定状态"""
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class CalibrationStatus:
    """标定状态快照"""
    phase: CalibrationPhase
    state: CalibrationState
    current_pose: int
    total_poses: int
    captured_images: int
    target_images: int
    current_pose_description: str
    progress_percent: float
    error_message: Optional[str] = None


class AutoCalibrationPlugin(Plugin):
    """
    自动标定插件

    提供Web界面集成的自动标定功能，支持实时进度推送和交互控制。
    """

    manifest = ComponentManifest(
        "auto-calibration",
        "0.1.0",
        "plugin",
        ("calibration", "web-integration"),
        "build_auto_calibration_plugin",
    )
    ui_kind = "auto-calibration"

    def __init__(
        self,
        robot_adapter,
        vision_adapter,
        config: Optional[AutoCalibrationConfig] = None,
    ):
        """
        初始化自动标定插件

        Args:
            robot_adapter: 机器人适配器
            vision_adapter: 视觉适配器
            config: 标定配置
        """
        self.robot = robot_adapter
        self.vision = vision_adapter
        self.config = config or AutoCalibrationConfig()

        # 标定器实例
        self.calibrator: Optional[AutoIntrinsicCalibration] = None

        # 状态管理
        self._phase = CalibrationPhase.IDLE
        self._state = CalibrationState.IDLE
        self._current_pose = 0
        self._total_poses = 0
        self._captured_images = 0
        self._error_message: Optional[str] = None

        # 控制标志
        self._paused = False
        self._stop_requested = False

        self._started = False
        self._shutdown = False

    async def startup(self) -> None:
        """启动插件"""
        if self._shutdown:
            raise RuntimeError("已停止的标定插件无法重启")

        # 创建标定器实例
        self.calibrator = AutoIntrinsicCalibration(
            robot_adapter=self.robot,
            vision_adapter=self.vision,
            config=self.config,
        )

        self._started = True
        self._state = CalibrationState.READY

    async def shutdown(self) -> None:
        """关闭插件"""
        if self._shutdown:
            return

        self._started = False
        self._shutdown = True
        self.calibrator = None

    def get_status(self) -> CalibrationStatus:
        """获取当前状态"""
        return CalibrationStatus(
            phase=self._phase,
            state=self._state,
            current_pose=self._current_pose,
            total_poses=self._total_poses,
            captured_images=self._captured_images,
            target_images=self.config.target_images,
            current_pose_description=self._get_current_pose_description(),
            progress_percent=self._calculate_progress(),
            error_message=self._error_message,
        )

    def _get_current_pose_description(self) -> str:
        """获取当前位姿描述"""
        if self.calibrator and 0 <= self._current_pose < len(self.calibrator.capture_poses):
            return self.calibrator.capture_poses[self._current_pose].description
        return ""

    def _calculate_progress(self) -> float:
        """计算进度百分比"""
        if self._total_poses == 0:
            return 0.0
        return (self._current_pose / self._total_poses) * 100.0

    async def start_intrinsic_calibration(
        self,
        calibration_id: str,
        camera_id: str,
        output_path: str,
    ) -> dict:
        """
        启动内参标定

        Args:
            calibration_id: 标定ID
            camera_id: 相机ID
            output_path: 输出路径

        Returns:
            标定结果
        """
        if self._state != CalibrationState.READY:
            raise RuntimeError(f"无法启动标定，当前状态: {self._state}")

        self._phase = CalibrationPhase.INTRINSIC
        self._state = CalibrationState.RUNNING
        self._total_poses = len(self.calibrator.capture_poses)
        self._current_pose = 0
        self._captured_images = 0
        self._error_message = None

        try:
            # 执行标定（需要修改run方法支持进度回调）
            result = await self.calibrator.run(
                calibration_id=calibration_id,
                camera_id=camera_id,
                output_path=output_path,
                save_images=False,
            )

            self._phase = CalibrationPhase.COMPLETE
            self._state = CalibrationState.COMPLETE

            return result

        except Exception as e:
            self._phase = CalibrationPhase.ERROR
            self._state = CalibrationState.ERROR
            self._error_message = str(e)
            raise

    def pause(self):
        """暂停标定"""
        if self._state == CalibrationState.RUNNING:
            self._paused = True
            self._state = CalibrationState.PAUSED

    def resume(self):
        """恢复标定"""
        if self._state == CalibrationState.PAUSED:
            self._paused = False
            self._state = CalibrationState.RUNNING

    def stop(self):
        """停止标定"""
        if self._state in (CalibrationState.RUNNING, CalibrationState.PAUSED):
            self._stop_requested = True
            self._state = CalibrationState.STOPPING


def build_auto_calibration_plugin(
    robot_adapter,
    vision_adapter,
    config: Optional[dict] = None,
) -> AutoCalibrationPlugin:
    """
    构建自动标定插件

    Args:
        robot_adapter: 机器人适配器
        vision_adapter: 视觉适配器
        config: 配置字典

    Returns:
        AutoCalibrationPlugin实例
    """
    if config:
        calibration_config = AutoCalibrationConfig(
            workspace_center=tuple(config.get("workspace_center", (300.0, 0.0, 100.0))),
            target_images=config.get("target_images", 30),
            min_images=config.get("min_images", 25),
        )
    else:
        calibration_config = None

    return AutoCalibrationPlugin(
        robot_adapter=robot_adapter,
        vision_adapter=vision_adapter,
        config=calibration_config,
    )
