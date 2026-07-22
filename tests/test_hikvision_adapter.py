"""Offline tests for the safety-bounded Hikvision USB3 Vision adapter."""

import asyncio
import sys
import unittest
from unittest.mock import patch

from gripper_ai_controller.adapters.hikvision import HikvisionAdapter, HikvisionAdapterError
from gripper_ai_controller.adapters.hikvision.client import (
    MvsCameraClientError,
    MvsUsbCameraClient,
    MvsUsbDevice,
)
from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config


class FakeMvsUsbClient:
    """Emulate the adapter-facing MVS client without importing native SDK files."""

    def __init__(self, payload=b"frame-bytes", open_error=None, capture_error=None):
        self.payload = payload
        self.open_error = open_error
        self.capture_error = capture_error
        self.calls = []

    def open_camera(self, camera_serial):
        self.calls.append(("open_camera", camera_serial))
        if self.open_error is not None:
            raise self.open_error

    def capture_frame(self, timeout_ms):
        self.calls.append(("capture_frame", timeout_ms))
        if self.capture_error is not None:
            raise self.capture_error
        return self.payload

    def close_camera(self):
        self.calls.append(("close_camera",))


class HikvisionAdapterTests(unittest.TestCase):
    """Verify camera lifecycle and frame mapping through injected fake MVS clients."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_capture_maps_one_fake_mvs_frame_without_parameter_writes(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter(
                camera_id="camera-a",
                calibration_id="calibration-a",
                camera_serial="serial-a",
                frame_timeout_ms=250,
                client_factory=lambda: client,
            )
            received = []

            @adapter.on_frame()
            async def record_frame(frame):
                received.append(frame)

            await adapter.startup()
            frame = await adapter.capture()
            await adapter.shutdown()

            self.assertTrue(frame.healthy)
            self.assertEqual("camera-a", frame.camera_id)
            self.assertEqual("calibration-a", frame.calibration_id)
            self.assertEqual("mvs://camera-a/frame-1", frame.frame_reference)
            self.assertEqual(b"frame-bytes", frame.pixel_payload)
            self.assertEqual([frame], received)
            self.assertEqual(
                [("open_camera", "serial-a"), ("capture_frame", 250), ("close_camera",)],
                client.calls,
            )
            self.assertFalse(adapter.connected)
            self.assertFalse(adapter.healthy)

        self.run_async(scenario())

    def test_capture_failure_marks_the_adapter_unhealthy_and_preserves_shutdown_cleanup(self):
        async def scenario():
            client = FakeMvsUsbClient(capture_error=RuntimeError("frame timeout"))
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)
            received = []

            @adapter.on_frame()
            async def record_frame(frame):
                received.append(frame)

            await adapter.startup()
            with self.assertRaises(HikvisionAdapterError):
                await adapter.capture()
            self.assertEqual([], received)
            self.assertFalse(adapter.healthy)
            await adapter.shutdown()
            self.assertEqual(("close_camera",), client.calls[-1])

        self.run_async(scenario())

    def test_observer_failure_propagates_without_marking_a_successful_capture_unhealthy(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            @adapter.on_frame()
            async def fail_after_capture(frame):
                raise RuntimeError("observer processing failed")

            await adapter.startup()
            with self.assertRaisesRegex(RuntimeError, "observer processing failed"):
                await adapter.capture()
            self.assertTrue(adapter.healthy)
            await adapter.shutdown()

        self.run_async(scenario())

    def test_open_failure_keeps_the_adapter_stopped_and_attempts_client_cleanup(self):
        async def scenario():
            client = FakeMvsUsbClient(open_error=RuntimeError("camera unavailable"))
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)
            with self.assertRaises(HikvisionAdapterError):
                await adapter.startup()
            self.assertFalse(adapter.started)
            self.assertFalse(adapter.connected)
            self.assertIn(("close_camera",), client.calls)

        self.run_async(scenario())

    def test_device_selection_requires_a_serial_when_multiple_cameras_exist(self):
        first = MvsUsbDevice("serial-a", "model-a", object())
        second = MvsUsbDevice("serial-b", "model-b", object())

        self.assertIs(second, MvsUsbCameraClient.select_device((first, second), "serial-b"))
        with self.assertRaises(MvsCameraClientError):
            MvsUsbCameraClient.select_device((first, second), None)
        with self.assertRaises(MvsCameraClientError):
            MvsUsbCameraClient.select_device((first,), "serial-missing")

    def test_missing_local_mvs_runtime_is_reported_without_leaking_import_error(self):
        client = MvsUsbCameraClient()

        with patch(
            "gripper_ai_controller.adapters.hikvision.client.importlib.import_module",
            side_effect=ImportError("runtime is unavailable"),
        ):
            with self.assertRaisesRegex(MvsCameraClientError, "project-local MVS"):
                client._load_sdk()

    def test_example_configuration_constructs_without_loading_the_mvs_sdk(self):
        config = load_runtime_config("configs/hikvision-usb.example.json")

        self.assertIsInstance(config.vision, HikvisionAdapter)
        self.assertFalse(config.vision.started)
        self.assertNotIn("gripper_ai_controller.adapters.hikvision.sdk.MvCameraControl_class", sys.modules)


if __name__ == "__main__":
    unittest.main()
