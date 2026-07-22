"""Offline tests for the safety-bounded Hikvision USB3 Vision adapter."""

import asyncio
import sys
import threading
import unittest
from ctypes import POINTER, Structure, c_ubyte, c_uint, c_ulonglong, cast
from unittest.mock import patch

from gripper_ai_controller.adapters.hikvision import HikvisionAdapter, HikvisionAdapterError
from gripper_ai_controller.adapters.hikvision.client import (
    MvsCameraClientError,
    MvsCapturedFrame,
    MvsUsbCameraClient,
    MvsUsbDevice,
)
from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.web.service import CameraPreviewService


class FakeMvsUsbClient:
    """Emulate the adapter-facing MVS client without importing native SDK files."""

    def __init__(self, payload=None, open_error=None, capture_error=None):
        self.payload = payload or MvsCapturedFrame(b"\x10\x20\x30", 1, 1, "rgb8")
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


class BlockingMvsUsbClient(FakeMvsUsbClient):
    """Hold a fake native capture until a shutdown test explicitly releases it."""

    def __init__(self):
        """Create synchronization points that model a worker-thread MVS capture call."""

        super().__init__()
        self.capture_started = threading.Event()
        self.allow_capture_return = threading.Event()
        self.capture_returned = threading.Event()
        self.close_while_capturing = False

    def capture_frame(self, timeout_ms):
        """Block in the executor thread to expose unsafe cancellation behavior deterministically."""

        self.calls.append(("capture_frame", timeout_ms))
        self.capture_started.set()
        self.allow_capture_return.wait(2.0)
        self.capture_returned.set()
        return self.payload

    def close_camera(self):
        """Record whether an adapter tried to release the handle before capture completion."""

        self.close_while_capturing = not self.capture_returned.is_set()
        super().close_camera()


class FakeFrameInfo(Structure):
    """Provide only the MVS frame-info fields read by the native client wrapper."""

    _fields_ = [
        ("nExtendWidth", c_uint),
        ("nExtendHeight", c_uint),
        ("nWidth", c_uint),
        ("nHeight", c_uint),
        ("nFrameLen", c_uint),
        ("nFrameLenEx", c_ulonglong),
        ("enPixelType", c_uint),
    ]


class FakeFrameOut(Structure):
    """Provide a ctypes-compatible image pointer and frame-info payload for unit tests."""

    _fields_ = [("pBufAddr", POINTER(c_ubyte)), ("stFrameInfo", FakeFrameInfo)]


class FakePixelConvertParams(Structure):
    """Model the conversion fields written and read by ``MvsUsbCameraClient``."""

    _fields_ = [
        ("nWidth", c_uint),
        ("nHeight", c_uint),
        ("enSrcPixelType", c_uint),
        ("pSrcData", POINTER(c_ubyte)),
        ("nSrcDataLen", c_uint),
        ("enDstPixelType", c_uint),
        ("pDstBuffer", POINTER(c_ubyte)),
        ("nDstLen", c_uint),
        ("nDstBufferSize", c_uint),
    ]


class FakeMvsSdk:
    """Expose the minimum MVS module contract needed for native client frame tests."""

    PixelType_Gvsp_Mono8 = 1
    PixelType_Gvsp_RGB8_Packed = 2
    PixelType_BayerRG8 = 3
    MV_FRAME_OUT = FakeFrameOut
    MV_CC_PIXEL_CONVERT_PARAM_EX = FakePixelConvertParams


class FakeNativeMvsCamera:
    """Simulate MVS frame allocation, RGB conversion, and release without a native DLL."""

    def __init__(
        self,
        pixel_type,
        source_payload,
        width,
        height,
        converted_payload=None,
        conversion_length=None,
    ):
        """Store deterministic frame metadata and an optional vendor-converted RGB payload."""

        self.pixel_type = pixel_type
        self.source_payload = source_payload
        self.width = width
        self.height = height
        self.converted_payload = converted_payload
        self.conversion_length = conversion_length
        self.source_buffer = None
        self.start_calls = 0
        self.free_calls = 0

    def MV_CC_StartGrabbing(self):
        """Record acquisition start without changing camera configuration."""

        self.start_calls += 1
        return 0

    def MV_CC_GetImageBuffer(self, frame, timeout_ms):
        """Populate one ctypes frame structure with an MVS-owned source buffer."""

        del timeout_ms
        self.source_buffer = (c_ubyte * len(self.source_payload))(*self.source_payload)
        frame.pBufAddr = cast(self.source_buffer, POINTER(c_ubyte))
        frame.stFrameInfo.nExtendWidth = self.width
        frame.stFrameInfo.nExtendHeight = self.height
        frame.stFrameInfo.nWidth = self.width
        frame.stFrameInfo.nHeight = self.height
        frame.stFrameInfo.nFrameLen = len(self.source_payload)
        frame.stFrameInfo.nFrameLenEx = len(self.source_payload)
        frame.stFrameInfo.enPixelType = self.pixel_type
        return 0

    def MV_CC_ConvertPixelTypeEx(self, conversion):
        """Write one configured RGB result into the caller-owned destination buffer."""

        for index, value in enumerate(self.converted_payload):
            conversion.pDstBuffer[index] = value
        conversion.nDstLen = (
            len(self.converted_payload)
            if self.conversion_length is None
            else self.conversion_length
        )
        return 0

    def MV_CC_FreeImageBuffer(self, frame):
        """Record release of the same MVS frame buffer after success or failure."""

        del frame
        self.free_calls += 1
        return 0


def create_native_client_for_frame(camera):
    """Create an opened MVS client wired to ctypes fakes without importing vendor modules."""

    client = MvsUsbCameraClient()
    client._sdk = FakeMvsSdk()
    client._camera = camera
    client._opened = True
    return client


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
            self.assertEqual(b"\x10\x20\x30", frame.pixel_payload)
            self.assertEqual(1, frame.width)
            self.assertEqual(1, frame.height)
            self.assertEqual("rgb8", frame.pixel_format)
            self.assertEqual([frame], received)
            self.assertEqual(
                [("open_camera", "serial-a"), ("capture_frame", 250), ("close_camera",)],
                client.calls,
            )
            self.assertFalse(adapter.connected)
            self.assertFalse(adapter.healthy)

        self.run_async(scenario())

    def test_capture_preserves_normalized_mono8_metadata_for_browser_encoding(self):
        async def scenario():
            client = FakeMvsUsbClient(MvsCapturedFrame(b"\x00\xff", 2, 1, "mono8"))
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            frame = await adapter.capture()
            await adapter.shutdown()

            self.assertEqual(b"\x00\xff", frame.pixel_payload)
            self.assertEqual(2, frame.width)
            self.assertEqual(1, frame.height)
            self.assertEqual("mono8", frame.pixel_format)

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

    def test_preview_shutdown_waits_for_in_flight_native_capture_before_closing_camera(self):
        async def scenario():
            client = BlockingMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)
            preview = CameraPreviewService(
                "camera-a",
                adapter,
                WebPreviewSettings(stream_fps=10, capture_retry_seconds=0.01),
            )
            await preview.startup()
            for _ in range(100):
                if client.capture_started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(client.capture_started.is_set())

            shutdown_task = asyncio.create_task(preview.shutdown())
            await asyncio.sleep(0.05)
            self.assertFalse(shutdown_task.done())
            self.assertFalse(client.close_while_capturing)

            client.allow_capture_return.set()
            await asyncio.wait_for(shutdown_task, timeout=1.0)
            self.assertTrue(client.capture_returned.is_set())
            self.assertFalse(client.close_while_capturing)
            self.assertEqual(("close_camera",), client.calls[-1])

        self.run_async(scenario())

    def test_device_selection_requires_a_serial_when_multiple_cameras_exist(self):
        first = MvsUsbDevice("serial-a", "model-a", object())
        second = MvsUsbDevice("serial-b", "model-b", object())

        self.assertIs(second, MvsUsbCameraClient.select_device((first, second), "serial-b"))
        with self.assertRaises(MvsCameraClientError):
            MvsUsbCameraClient.select_device((first, second), None)
        with self.assertRaises(MvsCameraClientError):
            MvsUsbCameraClient.select_device((first,), "serial-missing")

    def test_native_client_normalizes_mono_rgb_and_bayer_frames_and_releases_buffers(self):
        cases = (
            (FakeMvsSdk.PixelType_Gvsp_Mono8, b"\x00\xff", 2, 1, None, b"\x00\xff", "mono8"),
            (
                FakeMvsSdk.PixelType_Gvsp_RGB8_Packed,
                b"\x01\x02\x03\x04\x05\x06",
                2,
                1,
                None,
                b"\x01\x02\x03\x04\x05\x06",
                "rgb8",
            ),
            (FakeMvsSdk.PixelType_BayerRG8, b"\x11", 1, 1, b"\x07\x08\x09", b"\x07\x08\x09", "rgb8"),
        )

        for pixel_type, source, width, height, converted, expected, expected_format in cases:
            with self.subTest(pixel_type=pixel_type):
                camera = FakeNativeMvsCamera(pixel_type, source, width, height, converted)
                client = create_native_client_for_frame(camera)
                frame = client.capture_frame(250)

                self.assertEqual(expected, frame.pixel_payload)
                self.assertEqual(width, frame.width)
                self.assertEqual(height, frame.height)
                self.assertEqual(expected_format, frame.pixel_format)
                self.assertEqual(1, camera.start_calls)
                self.assertEqual(1, camera.free_calls)

    def test_native_client_releases_the_buffer_when_rgb_conversion_length_is_invalid(self):
        camera = FakeNativeMvsCamera(
            FakeMvsSdk.PixelType_BayerRG8,
            b"\x11",
            1,
            1,
            b"\x07\x08\x09",
            conversion_length=2,
        )
        client = create_native_client_for_frame(camera)

        with self.assertRaisesRegex(MvsCameraClientError, "unexpected output length"):
            client.capture_frame(250)
        self.assertEqual(1, camera.free_calls)

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
