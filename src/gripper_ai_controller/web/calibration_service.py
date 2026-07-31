"""
标定服务 - 业务逻辑层

提供标定流程的编排、状态管理和事件推送。
"""

import asyncio
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from enum import Enum

from gripper_ai_controller.plugins.auto_calibration import (
    AutoCalibrationPlugin,
    CalibrationPhase,
    CalibrationState,
    CalibrationStatus,
)


logger = logging.getLogger(__name__)


@dataclass
class CalibrationProgressEvent:
    """标定进度事件"""
    timestamp: str
    phase: str
    current_pose: int
    total_poses: int
    captured_images: int
    target_images: int
    current_pose_description: str
    state: str
    progress_percent: float


@dataclass
class CalibrationPoseCompleteEvent:
    """位姿采集完成事件"""
    timestamp: str
    pose_index: int
    description: str
    success: bool
    corner_count: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class CalibrationPhaseCompleteEvent:
    """阶段完成事件"""
    timestamp: str
    phase: str
    result: Dict[str, Any]


@dataclass
class CalibrationErrorEvent:
    """标定错误事件"""
    timestamp: str
    message: str
    recoverable: bool


class CalibrationService:
    """
    标定服务

    负责标定流程的编排、状态管理和WebSocket事件推送。
    """

    def __init__(self, plugin: AutoCalibrationPlugin):
        """
        初始化标定服务

        Args:
            plugin: 自动标定插件实例
        """
        self.plugin = plugin
        self._event_callbacks = []
        self._current_calibration_id: Optional[str] = None
        self._running_task: Optional[asyncio.Task] = None

    def subscribe(self, callback: Callable):
        """
        订阅标定事件

        Args:
            callback: 事件回调函数
        """
        self._event_callbacks.append(callback)

    def unsubscribe(self, callback: Callable):
        """取消订阅"""
        if callback in self._event_callbacks:
            self._event_callbacks.remove(callback)

    async def _emit_event(self, event_type: str, data: Any):
        """发送事件到所有订阅者"""
        event = {
            "type": event_type,
            "data": data if isinstance(data, dict) else asdict(data),
        }

        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"事件回调错误: {e}")

    def get_status(self) -> CalibrationStatus:
        """获取当前状态"""
        return self.plugin.get_status()

    async def start_intrinsic_calibration(
        self,
        calibration_id: str,
        camera_id: str,
        output_dir: str = "localstore/calibration",
    ) -> dict:
        """
        启动内参标定

        Args:
            calibration_id: 标定ID
            camera_id: 相机ID
            output_dir: 输出目录

        Returns:
            标定结果字典
        """
        if self._running_task and not self._running_task.done():
            raise RuntimeError("已有标定任务正在运行")

        self._current_calibration_id = calibration_id

        # 准备输出路径
        output_path = Path(output_dir) / f"{calibration_id}_intrinsic.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建异步任务
        self._running_task = asyncio.create_task(
            self._run_intrinsic_calibration(
                calibration_id=calibration_id,
                camera_id=camera_id,
                output_path=str(output_path),
            )
        )

        return {"calibration_id": calibration_id, "status": "started"}

    async def _run_intrinsic_calibration(
        self,
        calibration_id: str,
        camera_id: str,
        output_path: str,
    ):
        """执行内参标定（带进度推送）"""
        try:
            # 阶段1: 准备
            await self._emit_event(
                "calibration.progress",
                CalibrationProgressEvent(
                    timestamp=datetime.now().isoformat(),
                    phase="preparing",
                    current_pose=0,
                    total_poses=0,
                    captured_images=0,
                    target_images=self.plugin.config.target_images,
                    current_pose_description="准备中...",
                    state="running",
                    progress_percent=0.0,
                )
            )

            # 执行标定（这里需要增强AutoIntrinsicCalibration以支持进度回调）
            result = await self.plugin.start_intrinsic_calibration(
                calibration_id=calibration_id,
                camera_id=camera_id,
                output_path=output_path,
            )

            # 完成
            await self._emit_event(
                "calibration.phase_complete",
                CalibrationPhaseCompleteEvent(
                    timestamp=datetime.now().isoformat(),
                    phase="intrinsic",
                    result=result,
                )
            )

            return result

        except Exception as e:
            logger.error(f"标定失败: {e}", exc_info=True)
            await self._emit_event(
                "calibration.error",
                CalibrationErrorEvent(
                    timestamp=datetime.now().isoformat(),
                    message=str(e),
                    recoverable=False,
                )
            )
            raise

    async def pause(self):
        """暂停标定"""
        self.plugin.pause()
        await self._emit_event(
            "calibration.state_changed",
            {"state": "paused", "timestamp": datetime.now().isoformat()}
        )

    async def resume(self):
        """恢复标定"""
        self.plugin.resume()
        await self._emit_event(
            "calibration.state_changed",
            {"state": "running", "timestamp": datetime.now().isoformat()}
        )

    async def stop(self):
        """停止标定"""
        self.plugin.stop()
        if self._running_task:
            self._running_task.cancel()
        await self._emit_event(
            "calibration.state_changed",
            {"state": "stopped", "timestamp": datetime.now().isoformat()}
        )

    def get_history(self) -> list:
        """获取标定历史记录"""
        # TODO: 从文件系统读取历史标定结果
        history_dir = Path("localstore/calibration")
        if not history_dir.exists():
            return []

        results = []
        for json_file in history_dir.glob("*_intrinsic.json"):
            try:
                import json
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    results.append({
                        "id": json_file.stem,
                        "file": str(json_file),
                        "timestamp": data.get("calibration_date", "unknown"),
                        "camera_id": data.get("camera_id", "unknown"),
                        "reprojection_error": data.get("reprojection_error_px", 0),
                        "views_used": data.get("views_used", 0),
                    })
            except Exception as e:
                logger.error(f"读取标定结果失败 {json_file}: {e}")

        return sorted(results, key=lambda x: x["timestamp"], reverse=True)

    def get_result(self, calibration_id: str) -> Optional[dict]:
        """获取特定标定结果"""
        result_path = Path(f"localstore/calibration/{calibration_id}_intrinsic.json")
        if not result_path.exists():
            return None

        try:
            import json
            with open(result_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取标定结果失败: {e}")
            return None

    def delete_result(self, calibration_id: str) -> bool:
        """删除标定结果"""
        result_path = Path(f"localstore/calibration/{calibration_id}_intrinsic.json")
        if not result_path.exists():
            return False

        try:
            result_path.unlink()
            return True
        except Exception as e:
            logger.error(f"删除标定结果失败: {e}")
            return False
