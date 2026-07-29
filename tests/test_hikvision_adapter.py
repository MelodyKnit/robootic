"""Offline tests for the safety-bounded Hikvision USB3 Vision adapter."""

import asyncio
import sys
import threading
import unittest
from ctypes import POINTER, Structure, c_ubyte, c_uint, c_ulonglong, cast
from types import SimpleNamespace
from unittest.mock import patch

from gripper_ai_controller.adapters.hikvision import HikvisionAdapter, HikvisionAdapterError
from gripper_ai_controller.adapters.hikvision.client import (
    MvsBoolNodeValue,
    MvsCameraClientError,
    MvsCapturedFrame,
    MvsEnumNodeValue,
    MvsFloatNodeValue,
    MvsUsbCameraClient,
    MvsUsbDevice,
)
from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.domain.ports import CameraParameterError
from gripper_ai_controller.web.service import CameraPreviewService


class FakeMvsUsbClient:
    """Emulate the adapter-facing MVS client without importing native SDK files."""

    def __init__(
        self,
        payload=None,
        open_error=None,
        capture_error=None,
        set_error=None,
        devices=None,
    ):
        self.payload = payload or MvsCapturedFrame(b"\x10\x20\x30", 1, 1, "rgb8")
        self.open_error = open_error
        self.capture_error = capture_error
        self.set_error = set_error
        self.calls = []
        self.devices = tuple(
            devices
            or (MvsUsbDevice("serial-a", "model-a", object()),)
        )
        self.selected_device_id = None
        self.float_nodes = {
            "ExposureTime": MvsFloatNodeValue(8000.0, 20.0, 100000.0),
            "Gain": MvsFloatNodeValue(0.0, 0.0, 24.0),
            "AcquisitionFrameRate": MvsFloatNodeValue(30.0, 1.0, 60.0),
        }
        self.enum_nodes = {
            "ExposureAuto": MvsEnumNodeValue("Off", ("Off", "Continuous")),
            "GainAuto": MvsEnumNodeValue("Off", ("Off", "Continuous")),
            "PixelFormat": MvsEnumNodeValue("RGB8", ("RGB8", "Mono8")),
        }
        self.bool_nodes = {"AcquisitionFrameRateEnable": MvsBoolNodeValue(True)}

    def open_camera(self, camera_serial):
        self.calls.append(("open_camera", camera_serial))
        if self.open_error is not None:
            raise self.open_error
        selected = MvsUsbCameraClient.select_device(self.devices, camera_serial)
        self.selected_device_id = MvsUsbCameraClient.camera_device_id(
            selected.serial_number
        )

    def open_camera_device(self, device_id):
        self.calls.append(("open_camera_device", device_id))
        if self.open_error is not None:
            raise self.open_error
        selected = MvsUsbCameraClient.select_device_by_id(self.devices, device_id)
        self.selected_device_id = MvsUsbCameraClient.camera_device_id(
            selected.serial_number
        )

    def discover_usb_devices(self):
        self.calls.append(("discover_usb_devices",))
        return self.devices

    def capture_frame(self, timeout_ms):
        self.calls.append(("capture_frame", timeout_ms))
        if self.capture_error is not None:
            raise self.capture_error
        return self.payload

    def close_camera(self):
        self.calls.append(("close_camera",))
        self.selected_device_id = None

    def start_grabbing(self):
        self.calls.append(("start_grabbing",))

    def stop_grabbing(self):
        self.calls.append(("stop_grabbing",))

    def get_float_node(self, node_name):
        self.calls.append(("get_float_node", node_name))
        return self.float_nodes[node_name]

    def set_float_node(self, node_name, value):
        self.calls.append(("set_float_node", node_name, value))
        if self.set_error is not None:
            raise self.set_error
        current = self.float_nodes[node_name]
        self.float_nodes[node_name] = MvsFloatNodeValue(value, current.minimum, current.maximum)

    def get_enum_node(self, node_name):
        self.calls.append(("get_enum_node", node_name))
        return self.enum_nodes[node_name]

    def set_enum_node(self, node_name, value):
        self.calls.append(("set_enum_node", node_name, value))
        if self.set_error is not None:
            raise self.set_error
        current = self.enum_nodes[node_name]
        self.enum_nodes[node_name] = MvsEnumNodeValue(value, current.options)

    def get_bool_node(self, node_name):
        self.calls.append(("get_bool_node", node_name))
        return self.bool_nodes[node_name]

    def set_bool_node(self, node_name, value):
        self.calls.append(("set_bool_node", node_name, value))
        if self.set_error is not None:
            raise self.set_error
        self.bool_nodes[node_name] = MvsBoolNodeValue(value)


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


class BlockingOpenMvsUsbClient(FakeMvsUsbClient):
    """Hold a native open call so startup cancellation can be asserted safely."""

    def __init__(self):
        """Create synchronization points around the fake blocking SDK call."""

        super().__init__()
        self.open_started = threading.Event()
        self.allow_open_return = threading.Event()

    def open_camera(self, camera_serial):
        """Finish opening only after the cancellation test explicitly releases it."""

        self.open_started.set()
        self.allow_open_return.wait(2.0)
        super().open_camera(camera_serial)


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
    PixelType_Gvsp_Mono10 = 4
    MV_GrabStrategy_OneByOne = 10
    MV_GrabStrategy_LatestImagesOnly = 11
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
        strategy_result=0,
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
        self.stop_calls = 0
        self.free_calls = 0
        self.strategy_result = strategy_result
        self.strategy_values = []
        self.call_order = []
        self.conversion_destination_pixel_type = None

    def MV_CC_StartGrabbing(self):
        """Record acquisition start without changing camera configuration."""

        self.start_calls += 1
        self.call_order.append("start")
        return 0

    def MV_CC_SetGrabStrategy(self, strategy):
        """Record the strategy chosen before stream startup."""

        self.strategy_values.append(strategy)
        self.call_order.append("strategy")
        return self.strategy_result

    def MV_CC_StopGrabbing(self):
        """Record recovery when strategy configuration rejects the active stream."""

        self.stop_calls += 1
        self.call_order.append("stop")
        return 0

    def MV_CC_GetImageBuffer(self, frame, timeout_ms):
        """Populate one ctypes frame structure with an MVS-owned source buffer."""

        del timeout_ms
        self.call_order.append("get")
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

        self.conversion_destination_pixel_type = conversion.enDstPixelType
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


def create_native_client_for_frame(camera, frame_delivery_mode="latest_only"):
    """Create an opened MVS client wired to ctypes fakes without importing vendor modules."""

    client = MvsUsbCameraClient(frame_delivery_mode=frame_delivery_mode)
    client._sdk = FakeMvsSdk()
    client._camera = camera
    client._opened = True
    return client


class HikvisionAdapterTests(unittest.TestCase):
    """Verify camera lifecycle and frame mapping through injected fake MVS clients."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_capture_maps_one_fake_mvs_frame_without_a_frame_number_or_parameter_writes(self):
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
            self.assertEqual("mvs://camera-a/live", frame.frame_reference)
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

    def test_frame_delivery_mode_defaults_to_latest_only_and_rejects_unknown_modes(self):
        adapter = HikvisionAdapter("camera-a")

        self.assertEqual("latest_only", adapter.frame_delivery_mode)
        self.assertEqual(
            "latest_only",
            HikvisionAdapter.create_vendor_client(adapter.frame_delivery_mode).frame_delivery_mode,
        )
        self.assertEqual("sequential", MvsUsbCameraClient("sequential").frame_delivery_mode)
        with self.assertRaisesRegex(ValueError, "frame_delivery_mode"):
            HikvisionAdapter("camera-a", frame_delivery_mode="fifo")
        with self.assertRaisesRegex(ValueError, "frame_delivery_mode"):
            MvsUsbCameraClient("fifo")

    def test_adapter_uses_one_worker_executor_and_releases_it_on_shutdown(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            native_executor = adapter._native_executor
            self.assertIsNotNone(native_executor)
            self.assertEqual(1, native_executor._max_workers)
            await adapter.capture()
            await adapter.shutdown()

            self.assertIsNone(adapter._native_executor)

        self.run_async(scenario())

    def test_live_parameter_update_uses_device_range_without_restarting_acquisition(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            parameters = await adapter.get_camera_parameters()
            exposure = next(parameter for parameter in parameters if parameter.key == "exposure_time_us")
            self.assertEqual(20.0, exposure.minimum)
            self.assertEqual(100000.0, exposure.maximum)

            client.calls.clear()
            result = await adapter.update_camera_parameters({"exposure_time_us": 12000.0})
            await adapter.shutdown()

            self.assertFalse(result.restarted_acquisition)
            self.assertIn(("set_float_node", "ExposureTime", 12000.0), client.calls)
            self.assertNotIn(("stop_grabbing",), client.calls)
            self.assertNotIn(("start_grabbing",), client.calls)
            updated = next(parameter for parameter in result.parameters if parameter.key == "exposure_time_us")
            self.assertEqual(12000.0, updated.value)

        self.run_async(scenario())

    def test_restart_parameter_stops_writes_and_resumes_acquisition_in_order(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            client.calls.clear()
            result = await adapter.update_camera_parameters({"pixel_format": "Mono8"})
            await adapter.shutdown()

            operation_names = [call[0] for call in client.calls]
            self.assertTrue(result.restarted_acquisition)
            self.assertLess(operation_names.index("stop_grabbing"), operation_names.index("set_enum_node"))
            self.assertLess(operation_names.index("set_enum_node"), operation_names.index("start_grabbing"))
            self.assertIn(("set_enum_node", "PixelFormat", "Mono8"), client.calls)

        self.run_async(scenario())

    def test_manual_parameter_dependencies_and_ranges_are_rejected_before_vendor_writes(self):
        async def scenario():
            client = FakeMvsUsbClient()
            client.enum_nodes["ExposureAuto"] = MvsEnumNodeValue("Continuous", ("Off", "Continuous"))
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            client.calls.clear()
            with self.assertRaisesRegex(CameraParameterError, "Manual exposure"):
                await adapter.update_camera_parameters({"exposure_time_us": 12000.0})
            self.assertNotIn(("set_float_node", "ExposureTime", 12000.0), client.calls)

            client.calls.clear()
            with self.assertRaisesRegex(CameraParameterError, "outside the device range"):
                await adapter.update_camera_parameters({"gain_db": 100.0})
            self.assertFalse(any(call[0].startswith("set_") for call in client.calls))
            await adapter.shutdown()

        self.run_async(scenario())

    def test_restart_parameter_failure_still_attempts_to_resume_acquisition(self):
        async def scenario():
            client = FakeMvsUsbClient(set_error=RuntimeError("write failed"))
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            client.calls.clear()
            with self.assertRaisesRegex(HikvisionAdapterError, "Unable to apply"):
                await adapter.update_camera_parameters({"pixel_format": "Mono8"})
            operation_names = [call[0] for call in client.calls]
            self.assertLess(operation_names.index("stop_grabbing"), operation_names.index("set_enum_node"))
            self.assertLess(operation_names.index("set_enum_node"), operation_names.index("start_grabbing"))
            await adapter.shutdown()

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

    def test_startup_cancellation_remains_cancelled_after_native_open_cleanup(self):
        async def scenario():
            client = BlockingOpenMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)
            startup_task = asyncio.create_task(adapter.startup())
            for _ in range(100):
                if client.open_started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(client.open_started.is_set())

            startup_task.cancel()
            client.allow_open_return.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(startup_task, timeout=1.0)

            self.assertFalse(adapter.started)
            self.assertFalse(adapter.connected)
            self.assertIsNone(adapter._client)
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

    def test_opaque_device_ids_are_stable_and_select_without_exposing_serials(self):
        first = MvsUsbDevice("serial-a", "model-a", object())
        second = MvsUsbDevice("serial-b", "model-b", object())
        first_id = MvsUsbCameraClient.camera_device_id(first.serial_number)

        self.assertEqual(first_id, MvsUsbCameraClient.camera_device_id("serial-a"))
        self.assertNotEqual(first_id, MvsUsbCameraClient.camera_device_id("serial-b"))
        self.assertNotIn("serial-a", first_id)
        self.assertIs(
            first,
            MvsUsbCameraClient.select_device_by_id((first, second), first_id),
        )
        with self.assertRaises(MvsCameraClientError):
            MvsUsbCameraClient.select_device_by_id((first, second), "missing")

    def test_native_discovery_pairs_initialize_and_finalize_without_opening_a_device(self):
        calls = []

        class FakeMvsLifecycle:
            @staticmethod
            def MV_CC_Initialize():
                calls.append("initialize")
                return 0

            @staticmethod
            def MV_CC_Finalize():
                calls.append("finalize")
                return 0

        client = MvsUsbCameraClient()
        client._sdk = SimpleNamespace(MvCamera=FakeMvsLifecycle)

        def enumerate_devices():
            calls.append("enumerate")
            return (MvsUsbDevice("serial-a", "model-a", object()),)

        client.enumerate_usb_devices = enumerate_devices
        devices = client.discover_usb_devices()

        self.assertEqual(("serial-a",), tuple(device.serial_number for device in devices))
        self.assertEqual(["initialize", "enumerate", "finalize"], calls)
        self.assertFalse(client._initialized)
        self.assertFalse(client._opened)

    def test_native_discovery_finalizes_after_enumeration_failure(self):
        calls = []

        class FakeMvsLifecycle:
            @staticmethod
            def MV_CC_Initialize():
                calls.append("initialize")
                return 0

            @staticmethod
            def MV_CC_Finalize():
                calls.append("finalize")
                return 0

        client = MvsUsbCameraClient()
        client._sdk = SimpleNamespace(MvCamera=FakeMvsLifecycle)

        def fail_enumeration():
            calls.append("enumerate")
            raise RuntimeError("enumeration failed")

        client.enumerate_usb_devices = fail_enumeration
        with self.assertRaisesRegex(RuntimeError, "enumeration failed"):
            client.discover_usb_devices()

        self.assertEqual(["initialize", "enumerate", "finalize"], calls)
        self.assertFalse(client._initialized)

    def test_native_discovery_clears_initialized_state_after_finalize_failure(self):
        calls = []

        class FakeMvsLifecycle:
            @staticmethod
            def MV_CC_Initialize():
                calls.append("initialize")
                return 0

            @staticmethod
            def MV_CC_Finalize():
                calls.append("finalize")
                return 1

        client = MvsUsbCameraClient()
        client._sdk = SimpleNamespace(MvCamera=FakeMvsLifecycle)
        client.enumerate_usb_devices = lambda: (
            MvsUsbDevice("serial-a", "model-a", object()),
        )

        with self.assertRaisesRegex(MvsCameraClientError, "MV_CC_Finalize"):
            client.discover_usb_devices()

        self.assertEqual(["initialize", "finalize"], calls)
        self.assertFalse(client._initialized)

    def test_automatic_single_camera_selection_becomes_a_stable_device_selection(self):
        async def scenario():
            client = FakeMvsUsbClient()
            adapter = HikvisionAdapter("camera-a", client_factory=lambda: client)

            await adapter.startup()
            selected_device_id = adapter.selected_camera_device_id
            self.assertIsNotNone(selected_device_id)
            await adapter.shutdown()

            client.calls.clear()
            await adapter.startup()
            self.assertEqual(
                ("open_camera_device", selected_device_id),
                client.calls[0],
            )
            await adapter.shutdown()

        self.run_async(scenario())

    def test_adapter_discovers_selects_and_calibrates_devices_only_while_stopped(self):
        async def scenario():
            first = MvsUsbDevice("serial-a", "model-a", object())
            second = MvsUsbDevice("serial-b", "model-b", object())
            second_id = MvsUsbCameraClient.camera_device_id("serial-b")
            client = FakeMvsUsbClient(devices=(first, second))
            adapter = HikvisionAdapter(
                "camera-a",
                calibration_id="calibration-a",
                camera_serial="serial-a",
                client_factory=lambda: client,
                device_calibration_ids={second_id: "calibration-b"},
            )

            devices = await adapter.list_camera_devices()
            self.assertEqual(2, len(devices))
            self.assertTrue(devices[0].selected)
            self.assertTrue(devices[0].calibrated)
            self.assertTrue(devices[1].calibrated)
            self.assertEqual("usb3-vision", devices[1].transport)
            self.assertNotIn("serial-a", repr(devices))
            self.assertNotIn("serial-b", repr(devices))

            selected = await adapter.configure_camera_device(second_id)
            self.assertIsNotNone(selected)
            self.assertTrue(selected.selected)
            self.assertEqual(second_id, adapter.selected_camera_device_id)
            self.assertEqual("calibration-b", adapter.calibration_id)

            client.calls.clear()
            await adapter.startup()
            self.assertEqual(("open_camera_device", second_id), client.calls[0])
            with self.assertRaisesRegex(HikvisionAdapterError, "must be stopped"):
                await adapter.configure_camera_device(
                    MvsUsbCameraClient.camera_device_id("serial-a")
                )
            self.assertEqual(second_id, adapter.selected_camera_device_id)
            await adapter.shutdown()

            restored = await adapter.configure_camera_device(None)
            self.assertIsNone(restored)
            self.assertIsNone(adapter.selected_camera_device_id)
            self.assertIsNone(adapter.calibration_id)

        self.run_async(scenario())

    def test_selected_device_id_has_priority_and_unmapped_new_device_is_uncalibrated(self):
        async def scenario():
            first = MvsUsbDevice("serial-a", "model-a", object())
            second = MvsUsbDevice("serial-b", "model-b", object())
            first_id = MvsUsbCameraClient.camera_device_id("serial-a")
            second_id = MvsUsbCameraClient.camera_device_id("serial-b")
            client = FakeMvsUsbClient(devices=(first, second))
            adapter = HikvisionAdapter(
                "camera-a",
                calibration_id="calibration-a",
                camera_serial="serial-a",
                selected_device_id=second_id,
                client_factory=lambda: client,
            )

            await adapter.startup()
            self.assertEqual(("open_camera_device", second_id), client.calls[0])
            self.assertEqual(second_id, adapter.selected_camera_device_id)
            self.assertIsNone(adapter.calibration_id)
            await adapter.shutdown()

            await adapter.configure_camera_device(first_id)
            self.assertEqual(first_id, adapter.selected_camera_device_id)
            self.assertEqual("calibration-a", adapter.calibration_id)

        self.run_async(scenario())

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
            (FakeMvsSdk.PixelType_Gvsp_Mono10, b"\x00\x01\x02\x03", 2, 1, b"\x10\x20", b"\x10\x20", "mono8"),
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

    def test_native_client_configures_latest_only_before_start_and_first_buffer(self):
        camera = FakeNativeMvsCamera(
            FakeMvsSdk.PixelType_Gvsp_Mono8,
            b"\x00\xff",
            2,
            1,
        )
        client = create_native_client_for_frame(camera)

        client.capture_frame(250)

        self.assertEqual([FakeMvsSdk.MV_GrabStrategy_LatestImagesOnly], camera.strategy_values)
        self.assertEqual(["strategy", "start", "get"], camera.call_order[:3])

    def test_native_client_configures_explicit_sequential_delivery_before_start(self):
        camera = FakeNativeMvsCamera(
            FakeMvsSdk.PixelType_Gvsp_Mono8,
            b"\x00\xff",
            2,
            1,
        )
        client = create_native_client_for_frame(camera, frame_delivery_mode="sequential")

        client.capture_frame(250)

        self.assertEqual([FakeMvsSdk.MV_GrabStrategy_OneByOne], camera.strategy_values)
        self.assertEqual(["strategy", "start", "get"], camera.call_order[:3])

    def test_native_client_rejects_strategy_failure_before_requesting_a_frame(self):
        camera = FakeNativeMvsCamera(
            FakeMvsSdk.PixelType_Gvsp_Mono8,
            b"\x00\xff",
            2,
            1,
            strategy_result=0x80000001,
        )
        client = create_native_client_for_frame(camera)

        with self.assertRaisesRegex(MvsCameraClientError, "MV_CC_SetGrabStrategy"):
            client.capture_frame(250)

        self.assertEqual(["strategy"], camera.call_order)
        self.assertEqual(0, camera.stop_calls)
        self.assertEqual(0, camera.free_calls)

    def test_native_client_converts_high_bit_depth_monochrome_to_mono8(self):
        camera = FakeNativeMvsCamera(
            FakeMvsSdk.PixelType_Gvsp_Mono10,
            b"\x00\x01\x02\x03",
            2,
            1,
            b"\x10\x20",
        )
        client = create_native_client_for_frame(camera)

        frame = client.capture_frame(250)

        self.assertEqual("mono8", frame.pixel_format)
        self.assertEqual(b"\x10\x20", frame.pixel_payload)
        self.assertEqual(FakeMvsSdk.PixelType_Gvsp_Mono8, camera.conversion_destination_pixel_type)

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
