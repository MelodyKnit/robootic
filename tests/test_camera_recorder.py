"""录制和截图功能的完整自动化单元测试"""

import asyncio
from pathlib import Path
import tempfile
import unittest
import numpy as np

from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.web.recorder import CameraRecorder, RecordingError


class TestCameraRecorder(unittest.TestCase):
    """测试 CameraRecorder 截图和视频录制的正确性"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.recorder = CameraRecorder(self.output_dir)

        # 构造假的 RGB8 图像帧
        data = np.zeros((480, 640, 3), dtype=np.uint8)
        data[:, :] = [100, 150, 200]
        self.sample_frame = ImageFrame(
            camera_id="test_cam",
            captured_at=1000.0,
            frame_reference=None,
            calibration_id=None,
            healthy=True,
            pixel_payload=data.tobytes(),
            width=640,
            height=480,
            pixel_format="rgb8",
        )

    def tearDown(self) -> None:
        self.recorder.cleanup()
        self.temp_dir.cleanup()

    def test_capture_screenshot_success(self) -> None:
        """验证单帧截图能正常写入并生成有效的 JPG 文件"""
        filepath = self.recorder.capture_screenshot(self.sample_frame, prefix="unit_test")
        self.assertTrue(filepath.is_file())
        self.assertTrue(filepath.name.startswith("unit_test_"))
        self.assertTrue(filepath.name.endswith(".jpg"))
        self.assertGreater(filepath.stat().st_size, 0)

    def test_video_recording_lifecycle(self) -> None:
        """验证视频录制的启动、逐帧写入、帧数累加和正常停止流程"""
        self.assertFalse(self.recorder.is_recording)

        video_path = self.recorder.start_recording(self.sample_frame, fps=25, prefix="video_test")
        self.assertTrue(self.recorder.is_recording)
        self.assertEqual(self.recorder.frame_count, 1)

        # 再写入两帧
        self.recorder.write_frame(self.sample_frame)
        self.recorder.write_frame(self.sample_frame)
        self.assertEqual(self.recorder.frame_count, 3)

        # 停止录制
        result = self.recorder.stop_recording()
        self.assertIsNotNone(result)
        path, count = result  # type: ignore
        self.assertFalse(self.recorder.is_recording)
        self.assertEqual(count, 3)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)

    def test_duplicate_start_recording_raises(self) -> None:
        """验证重复启动录制时抛出显式 RecordingError 异常"""
        self.recorder.start_recording(self.sample_frame)
        with self.assertRaises(RecordingError):
            self.recorder.start_recording(self.sample_frame)


if __name__ == "__main__":
    unittest.main()
