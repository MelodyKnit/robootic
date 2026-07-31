"""相机录制和截图服务"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import cv2
import numpy


class RecordingService:
    """管理相机画面的录制和截图"""

    def __init__(
        self,
        output_dir: str,
        default_fps: int = 30,
        enabled: bool = True
    ):
        self.output_dir = Path(output_dir)
        self.default_fps = default_fps
        self.enabled = enabled
        self._active_recordings: Dict[str, Dict[str, Any]] = {}
        self._recording_lock = asyncio.Lock()

        if self.enabled:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "videos").mkdir(exist_ok=True)
            (self.output_dir / "snapshots").mkdir(exist_ok=True)

    async def save_snapshot(
        self,
        frame_data: bytes,
        camera_id: str,
        prefix: str = "snapshot"
    ) -> str:
        """保存单帧截图"""

        if not self.enabled:
            raise ValueError("Recording service is disabled")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{prefix}_{camera_id}_{timestamp}.jpg"
        filepath = self.output_dir / "snapshots" / filename

        # 异步写入文件
        await asyncio.to_thread(filepath.write_bytes, frame_data)

        return str(filepath.relative_to(self.output_dir.parent))

    async def start_recording(
        self,
        camera_id: str,
        fps: Optional[int] = None,
        codec: str = "mp4v",
        prefix: str = "recording"
    ) -> str:
        """开始录制视频"""

        if not self.enabled:
            raise ValueError("Recording service is disabled")

        async with self._recording_lock:
            if camera_id in self._active_recordings:
                raise ValueError(f"Camera {camera_id} is already recording")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{camera_id}_{timestamp}.mp4"
            filepath = self.output_dir / "videos" / filename

            recording_fps = fps or self.default_fps
            recording_id = f"{camera_id}_{timestamp}"

            self._active_recordings[camera_id] = {
                "recording_id": recording_id,
                "filepath": filepath,
                "fps": recording_fps,
                "codec": codec,
                "writer": None,
                "frame_count": 0,
                "started_at": datetime.now().isoformat(),
                "width": None,
                "height": None
            }

            return recording_id

    async def add_frame(self, camera_id: str, frame_data: bytes) -> bool:
        """添加一帧到当前录制"""

        if camera_id not in self._active_recordings:
            return False

        recording = self._active_recordings[camera_id]

        # 解码 JPEG
        nparr = numpy.frombuffer(frame_data, numpy.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return False

        height, width = frame.shape[:2]

        # 首次写入时初始化 VideoWriter
        if recording["writer"] is None:
            fourcc = cv2.VideoWriter_fourcc(*recording["codec"])
            recording["writer"] = cv2.VideoWriter(
                str(recording["filepath"]),
                fourcc,
                recording["fps"],
                (width, height)
            )
            recording["width"] = width
            recording["height"] = height

        recording["writer"].write(frame)
        recording["frame_count"] += 1

        return True

    async def stop_recording(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """停止录制并返回录制信息"""

        async with self._recording_lock:
            if camera_id not in self._active_recordings:
                return None

            recording = self._active_recordings.pop(camera_id)

            if recording["writer"] is not None:
                recording["writer"].release()

            ended_at = datetime.now().isoformat()
            duration_seconds = recording["frame_count"] / recording["fps"]

            result = {
                "recording_id": recording["recording_id"],
                "filepath": str(recording["filepath"].relative_to(self.output_dir.parent)),
                "frame_count": recording["frame_count"],
                "fps": recording["fps"],
                "width": recording["width"],
                "height": recording["height"],
                "duration_seconds": duration_seconds,
                "started_at": recording["started_at"],
                "ended_at": ended_at
            }

            # 保存元数据
            metadata_path = recording["filepath"].with_suffix(".json")
            await asyncio.to_thread(
                metadata_path.write_text,
                json.dumps(result, ensure_ascii=False, indent=2)
            )

            return result

    def is_recording(self, camera_id: str) -> bool:
        """检查相机是否正在录制"""
        return camera_id in self._active_recordings

    def get_recording_status(self, camera_id: str) -> Optional[Dict[str, Any]]:
        """获取当前录制状态"""

        if camera_id not in self._active_recordings:
            return None

        recording = self._active_recordings[camera_id]
        return {
            "recording_id": recording["recording_id"],
            "frame_count": recording["frame_count"],
            "fps": recording["fps"],
            "started_at": recording["started_at"],
            "duration_seconds": recording["frame_count"] / recording["fps"]
        }

    async def list_recordings(self, camera_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出已录制的视频"""

        videos_dir = self.output_dir / "videos"
        if not videos_dir.exists():
            return []

        recordings = []
        pattern = f"*_{camera_id}_*.json" if camera_id else "*.json"

        for metadata_file in videos_dir.glob(pattern):
            try:
                metadata = json.loads(metadata_file.read_text())
                video_file = metadata_file.with_suffix(".mp4")
                if video_file.exists():
                    metadata["size_bytes"] = video_file.stat().st_size
                    recordings.append(metadata)
            except Exception:
                continue

        recordings.sort(key=lambda x: x["started_at"], reverse=True)
        return recordings

    async def list_snapshots(self, camera_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出已保存的截图"""

        snapshots_dir = self.output_dir / "snapshots"
        if not snapshots_dir.exists():
            return []

        snapshots = []
        pattern = f"*_{camera_id}_*.jpg" if camera_id else "*.jpg"

        for snapshot_file in snapshots_dir.glob(pattern):
            stat = snapshot_file.stat()
            snapshots.append({
                "filename": snapshot_file.name,
                "filepath": str(snapshot_file.relative_to(self.output_dir.parent)),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })

        snapshots.sort(key=lambda x: x["created_at"], reverse=True)
        return snapshots

    async def delete_recording(self, recording_id: str) -> bool:
        """删除录制文件"""

        videos_dir = self.output_dir / "videos"
        for metadata_file in videos_dir.glob(f"*{recording_id}*.json"):
            try:
                video_file = metadata_file.with_suffix(".mp4")
                if video_file.exists():
                    video_file.unlink()
                metadata_file.unlink()
                return True
            except Exception:
                continue

        return False

    async def delete_snapshot(self, filename: str) -> bool:
        """删除截图文件"""

        snapshot_file = self.output_dir / "snapshots" / filename
        if snapshot_file.exists():
            try:
                snapshot_file.unlink()
                return True
            except Exception:
                pass

        return False
