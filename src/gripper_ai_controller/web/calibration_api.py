"""
标定API路由

提供RESTful API和WebSocket接口。
"""

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field

from gripper_ai_controller.web.calibration_service import CalibrationService


logger = logging.getLogger(__name__)


# ============================================================================
# 请求/响应模型
# ============================================================================

class StartIntrinsicCalibrationRequest(BaseModel):
    """启动内参标定请求"""
    calibration_id: str = Field(..., description="标定ID（唯一标识）")
    camera_id: str = Field(..., description="相机ID")
    output_dir: str = Field(default="localstore/calibration", description="输出目录")


class CalibrationConfigRequest(BaseModel):
    """标定配置请求"""
    workspace_center_x: float = Field(default=300.0, description="工作空间中心X坐标(mm)")
    workspace_center_y: float = Field(default=0.0, description="工作空间中心Y坐标(mm)")
    workspace_center_z: float = Field(default=100.0, description="工作空间中心Z坐标(mm)")
    target_images: int = Field(default=30, ge=25, le=50, description="目标采集图像数")
    min_images: int = Field(default=25, ge=10, le=30, description="最少图像数")


class CalibrationStatusResponse(BaseModel):
    """标定状态响应"""
    phase: str
    state: str
    current_pose: int
    total_poses: int
    captured_images: int
    target_images: int
    current_pose_description: str
    progress_percent: float
    error_message: Optional[str] = None


# ============================================================================
# WebSocket连接管理
# ============================================================================

class CalibrationWebSocketManager:
    """WebSocket连接管理器"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """断开连接"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket连接断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                disconnected.append(connection)

        # 清理断开的连接
        for conn in disconnected:
            self.disconnect(conn)


# ============================================================================
# 全局实例
# ============================================================================

# 注意：这些实例需要在应用启动时注入
_calibration_service: Optional[CalibrationService] = None
_ws_manager = CalibrationWebSocketManager()


def set_calibration_service(service: CalibrationService):
    """设置标定服务实例（由应用启动时调用）"""
    global _calibration_service
    _calibration_service = service

    # 订阅标定事件，广播到WebSocket
    service.subscribe(_broadcast_calibration_event)


async def _broadcast_calibration_event(event: dict):
    """广播标定事件到所有WebSocket连接"""
    await _ws_manager.broadcast(event)


def get_calibration_service() -> CalibrationService:
    """获取标定服务实例"""
    if _calibration_service is None:
        raise RuntimeError("标定服务未初始化")
    return _calibration_service


# ============================================================================
# 路由定义
# ============================================================================

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.post("/intrinsic/start")
async def start_intrinsic_calibration(request: StartIntrinsicCalibrationRequest):
    """
    启动内参标定

    启动自动内参标定流程，机械臂将自动移动到多个位姿采集标定图像。
    """
    service = get_calibration_service()

    try:
        result = await service.start_intrinsic_calibration(
            calibration_id=request.calibration_id,
            camera_id=request.camera_id,
            output_dir=request.output_dir,
        )
        return {"success": True, "data": result}

    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"启动标定失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动标定失败: {str(e)}")


@router.post("/intrinsic/pause")
async def pause_calibration():
    """暂停当前标定"""
    service = get_calibration_service()

    try:
        await service.pause()
        return {"success": True, "message": "标定已暂停"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intrinsic/resume")
async def resume_calibration():
    """恢复标定"""
    service = get_calibration_service()

    try:
        await service.resume()
        return {"success": True, "message": "标定已恢复"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intrinsic/stop")
async def stop_calibration():
    """停止标定"""
    service = get_calibration_service()

    try:
        await service.stop()
        return {"success": True, "message": "标定已停止"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/intrinsic/status", response_model=CalibrationStatusResponse)
async def get_calibration_status():
    """获取当前标定状态"""
    service = get_calibration_service()

    try:
        status = service.get_status()
        return CalibrationStatusResponse(
            phase=status.phase.value,
            state=status.state.value,
            current_pose=status.current_pose,
            total_poses=status.total_poses,
            captured_images=status.captured_images,
            target_images=status.target_images,
            current_pose_description=status.current_pose_description,
            progress_percent=status.progress_percent,
            error_message=status.error_message,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_calibration_config():
    """获取标定配置"""
    service = get_calibration_service()

    try:
        config = service.plugin.config
        return {
            "success": True,
            "data": {
                "workspace_center": {
                    "x": config.workspace_center[0],
                    "y": config.workspace_center[1],
                    "z": config.workspace_center[2],
                },
                "target_images": config.target_images,
                "min_images": config.min_images,
                "capture_stabilization_sec": config.capture_stabilization_sec,
                "move_velocity": config.move_velocity,
                "move_acceleration": config.move_acceleration,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_calibration_config(request: CalibrationConfigRequest):
    """更新标定配置"""
    service = get_calibration_service()

    try:
        # 注意：这里需要重建配置对象（因为是frozen dataclass）
        from gripper_ai_controller.calibration.auto_intrinsic import AutoCalibrationConfig

        new_config = AutoCalibrationConfig(
            workspace_center=(
                request.workspace_center_x,
                request.workspace_center_y,
                request.workspace_center_z,
            ),
            target_images=request.target_images,
            min_images=request.min_images,
        )

        service.plugin.config = new_config

        return {"success": True, "message": "配置已更新"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/results")
async def list_calibration_results():
    """列出历史标定结果"""
    service = get_calibration_service()

    try:
        results = service.get_history()
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/results/{calibration_id}")
async def get_calibration_result(calibration_id: str):
    """获取特定标定结果"""
    service = get_calibration_service()

    try:
        result = service.get_result(calibration_id)
        if result is None:
            raise HTTPException(status_code=404, detail="标定结果不存在")

        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/results/{calibration_id}")
async def delete_calibration_result(calibration_id: str):
    """删除标定结果"""
    service = get_calibration_service()

    try:
        success = service.delete_result(calibration_id)
        if not success:
            raise HTTPException(status_code=404, detail="标定结果不存在")

        return {"success": True, "message": "标定结果已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws")
async def calibration_websocket(websocket: WebSocket):
    """
    标定WebSocket连接

    客户端连接后将接收实时标定进度和事件推送。
    """
    await _ws_manager.connect(websocket)

    try:
        while True:
            # 保持连接，接收客户端消息（如果需要双向通信）
            data = await websocket.receive_text()
            message = json.loads(data)

            # 处理客户端消息
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket错误: {e}", exc_info=True)
        _ws_manager.disconnect(websocket)


def install_calibration_routes(app):
    """
    安装标定路由到FastAPI应用

    Args:
        app: FastAPI应用实例
    """
    app.include_router(router)
    logger.info("标定API路由已安装")
