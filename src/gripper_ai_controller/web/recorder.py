"""相机录制和截图功能的实现模块"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import cv2
import numpy as np

from gripper_ai_controller.domain.models import ImageFrame


class RecordingError(Exception):
    """录制或截图过程中的错误"""


class CameraRecorder:
    """相机录制和截图管理器
    
    负责将相机画面保存为图片或视频文件。
    """
    
    def __init__(self, output_dir: Path):
        """初始化录制器
        
        Args:
            output_dir: 输出文件的根目录
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._is_recording = False
        self._current_video_path: Optional[Path] = None
        self._frame_count = 0
        
    def capture_screenshot(self, frame: ImageFrame, prefix: str = "capture") -> Path:
        """截取单帧图片
        
        Args:
            frame: 图像帧
            prefix: 文件名前缀
            
        Returns:
            保存的文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{prefix}_{timestamp}.jpg"
        filepath = self.output_dir / filename
        
        # 转换图像格式
        if frame.encoding == "bgr8":
            image = frame.data
        elif frame.encoding == "rgb8":
            image = cv2.cvtColor(frame.data, cv2.COLOR_RGB2BGR)
        elif frame.encoding in ("mono8", "mono16"):
            image = frame.data
        else:
            raise RecordingError(f"Unsupported encoding: {frame.encoding}")
        
        # 保存图片
        success = cv2.imwrite(str(filepath), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not success:
            raise RecordingError(f"Failed to write screenshot: {filepath}")
        
        return filepath
    
    def start_recording(
        self, 
        frame: ImageFrame, 
        fps: int = 30, 
        prefix: str = "recording"
    ) -> Path:
        """开始视频录制
        
        Args:
            frame: 首帧(用于获取分辨率)
            fps: 帧率
            prefix: 文件名前缀
            
        Returns:
            视频文件路径
        """
        if self._is_recording:
            raise RecordingError("Already recording")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.avi"
        self._current_video_path = self.output_dir / filename
        
        # 获取帧尺寸
        height, width = frame.data.shape[:2]
        
        # 创建视频写入器 (使用 MJPEG 编解码器,兼容性好)
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        self._video_writer = cv2.VideoWriter(
            str(self._current_video_path),
            fourcc,
            fps,
            (width, height)
        )
        
        if not self._video_writer.isOpened():
            self._video_writer = None
            raise RecordingError(f"Failed to open video writer: {self._current_video_path}")
        
        self._is_recording = True
        self._frame_count = 0
        
        # 写入首帧
        self.write_frame(frame)
        
        return self._current_video_path
    
    def write_frame(self, frame: ImageFrame) -> None:
        """写入一帧到正在录制的视频
        
        Args:
            frame: 图像帧
        """
        if not self._is_recording or self._video_writer is None:
            return
        
        # 转换为BGR格式
        if frame.encoding == "bgr8":
            image = frame.data
        elif frame.encoding == "rgb8":
            image = cv2.cvtColor(frame.data, cv2.COLOR_RGB2BGR)
        elif frame.encoding in ("mono8", "mono16"):
            # 转换灰度图为BGR
            if frame.encoding == "mono16":
                image = (frame.data / 256).astype(np.uint8)
            else:
                image = frame.data
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            return  # 跳过不支持的格式
        
        self._video_writer.write(image)
        self._frame_count += 1
    
    def stop_recording(self) -> Optional[Tuple[Path, int]]:
        """停止录制
        
        Returns:
            (视频文件路径, 帧数) 或 None
        """
        if not self._is_recording:
            return None
        
        self._is_recording = False
        
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
        
        result = (self._current_video_path, self._frame_count)
        self._current_video_path = None
        self._frame_count = 0
        
        return result
    
    @property
    def is_recording(self) -> bool:
        """是否正在录制"""
        return self._is_recording
    
    @property
    def frame_count(self) -> int:
        """当前录制的帧数"""
        return self._frame_count
    
    def cleanup(self) -> None:
        """清理资源"""
        if self._is_recording:
            self.stop_recording()
