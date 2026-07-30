"""Latest-frame and passive-boundary tests for generic object detection."""

import asyncio
import time
import unittest

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.object_detection.models import DetectionCandidate, DetectionProvider
from gripper_ai_controller.object_detection.tracker import (
    ObjectDetectionTrackingService,
    UnknownDetectionModelError,
)


class RecordingDetectionProvider(DetectionProvider):
    """Return one deterministic box and optionally delay worker completion."""

    provider_id = "recording-provider"

    def __init__(self, label="wrench", delay=0.0):
        self.label = label
        self.delay = delay
        self.captured_times = []

    def infer(self, frame):
        self.captured_times.append(frame.captured_at)
        if self.delay:
            time.sleep(self.delay)
        return (
            DetectionCandidate(
                self.label,
                0.9,
                BoundingBox2D(0.1, 0.2, 0.3, 0.4),
                1,
            ),
        )


class ObjectDetectionTrackingTests(unittest.TestCase):
    """Verify one-worker scheduling, model switching, and safe disabled states."""

    def test_replaces_pending_frames_with_the_latest_capture(self):
        async def scenario():
            provider = RecordingDetectionProvider(delay=0.04)
            service = ObjectDetectionTrackingService(
                "camera-a", True, {"model-a": provider}, "model-a", max_analysis_fps=30
            )
            try:
                await service.submit_frame(self._frame(1.0))
                await service.submit_frame(self._frame(2.0))
                await service.submit_frame(self._frame(3.0))
                snapshot = await self._wait_for_capture(service, 3.0)
            finally:
                await service.shutdown()
            self.assertTrue(snapshot.valid)
            self.assertEqual("detection_available", snapshot.reason)
            self.assertEqual([1.0, 3.0], provider.captured_times)
            self.assertEqual("wrench", snapshot.detections[0].label)

        asyncio.run(scenario())

    def test_model_switch_clears_old_boxes_and_rejects_unknown_ids(self):
        async def scenario():
            first = RecordingDetectionProvider("wrench")
            second = RecordingDetectionProvider("pliers")
            service = ObjectDetectionTrackingService(
                "camera-a",
                True,
                {"model-a": first, "model-b": second},
                "model-a",
                max_analysis_fps=30,
            )
            try:
                await service.submit_frame(self._frame(1.0))
                before = await self._wait_for_capture(service, 1.0)
                switched = await service.select_model("model-b")
                self.assertIsNone(switched.captured_at)
                self.assertEqual((), switched.detections)
                await service.submit_frame(self._frame(2.0))
                after = await self._wait_for_capture(service, 2.0)
                with self.assertRaises(UnknownDetectionModelError):
                    await service.select_model("missing")
            finally:
                await service.shutdown()
            self.assertEqual("wrench", before.detections[0].label)
            self.assertEqual("model-b", after.selected_model_id)
            self.assertEqual("pliers", after.detections[0].label)

        asyncio.run(scenario())

    def test_disabled_service_does_not_submit_or_call_provider(self):
        async def scenario():
            provider = RecordingDetectionProvider()
            service = ObjectDetectionTrackingService(
                "camera-a", False, {"model-a": provider}, "model-a"
            )
            try:
                accepted = await service.submit_frame(self._frame(1.0))
                snapshot = await service.snapshot()
            finally:
                await service.shutdown()
            self.assertFalse(accepted)
            self.assertFalse(snapshot.enabled)
            self.assertEqual("object_detection_disabled", snapshot.reason)
            self.assertEqual([], provider.captured_times)

        asyncio.run(scenario())

    @staticmethod
    def _frame(captured_at):
        return ImageFrame(
            "camera-a", captured_at, None, None, True, b"\x10\x20\x30", 1, 1, "rgb8"
        )

    @staticmethod
    async def _wait_for_capture(service, captured_at):
        for _ in range(200):
            snapshot = await service.snapshot()
            if snapshot.captured_at == captured_at:
                return snapshot
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for object detection output.")


if __name__ == "__main__":
    unittest.main()
