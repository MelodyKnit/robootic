"""Offline tests for instance-scoped vision frame observer registration."""

import asyncio
import unittest

from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter


class VisionAdapterFrameObserverTests(unittest.TestCase):
    """Verify the shared ``VisionAdapter.on_frame`` observer contract."""

    def run_async(self, coroutine):
        """Execute one async test scenario with Python 3.7-compatible asyncio APIs."""

        return asyncio.run(coroutine)

    def test_decorator_alias_receives_the_same_captured_frame_once(self):
        """Support the concise ``@on_frame()`` form after explicit adapter binding."""

        async def scenario():
            camera = SimulatedCameraAdapter(frame_reference="simulation://decorator-frame")
            received = []
            on_frame = camera.on_frame

            @on_frame()
            async def record_frame(frame):
                received.append(frame)

            captured = await camera.capture()

            self.assertEqual([captured], received)

        self.run_async(scenario())

    def test_observers_are_adapter_scoped_and_keep_registration_order(self):
        """Keep multi-camera subscriptions isolated while delivering one stable sequence."""

        async def scenario():
            first_camera = SimulatedCameraAdapter(frame_reference="simulation://first-frame")
            second_camera = SimulatedCameraAdapter(frame_reference="simulation://second-frame")
            calls = []

            @first_camera.on_frame()
            async def record_first_early(frame):
                calls.append(("first-early", frame.frame_reference))

            @first_camera.on_frame()
            async def record_first_late(frame):
                calls.append(("first-late", frame.frame_reference))

            @second_camera.on_frame()
            async def record_second(frame):
                calls.append(("second", frame.frame_reference))

            await first_camera.capture()
            await second_camera.capture()

            self.assertEqual(
                [
                    ("first-early", "simulation://first-frame"),
                    ("first-late", "simulation://first-frame"),
                    ("second", "simulation://second-frame"),
                ],
                calls,
            )

        self.run_async(scenario())

    def test_repeated_registration_is_idempotent(self):
        """Avoid duplicate delivery when a component reload registers the same callable again."""

        async def scenario():
            camera = SimulatedCameraAdapter()
            received = []

            async def record_frame(frame):
                received.append(frame)

            camera.on_frame(record_frame)
            camera.on_frame(record_frame)
            captured = await camera.capture()

            self.assertEqual([captured], received)

        self.run_async(scenario())

    def test_observers_added_during_delivery_begin_with_the_next_frame(self):
        """Use a registration snapshot so one frame has a stable observer sequence."""

        async def scenario():
            camera = SimulatedCameraAdapter()
            calls = []

            async def record_late(frame):
                calls.append("late")

            @camera.on_frame()
            async def record_early(frame):
                calls.append("early")
                camera.on_frame(record_late)

            await camera.capture()
            await camera.capture()

            self.assertEqual(["early", "early", "late"], calls)

        self.run_async(scenario())

    def test_synchronous_observers_are_rejected_during_registration(self):
        """Require handlers to be awaitable before a camera begins delivering frames."""

        camera = SimulatedCameraAdapter()

        def synchronous_handler(frame):
            return frame

        with self.assertRaisesRegex(TypeError, "async def"):
            camera.on_frame(synchronous_handler)


if __name__ == "__main__":
    unittest.main()
