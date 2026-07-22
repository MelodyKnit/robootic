"""Synchronous MVS USB camera boundary used only by the Hikvision adapter."""

import importlib
import os
from ctypes import POINTER, byref, cast, memset, sizeof, string_at
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence


class MvsCameraClientError(RuntimeError):
    """Report an MVS SDK failure without leaking ctypes details to the adapter."""


@dataclass(frozen=True)
class MvsUsbDevice:
    """Describe one SDK-enumerated USB3 Vision device for local selection only."""

    serial_number: str
    model_name: str
    vendor_device_info: Any


class MvsUsbCameraClient:
    """Wrap the blocking MVS SDK lifecycle and frame-copy operations.

    The client never configures trigger mode, exposure, gain, frame rate, or persistence.
    It only enumerates, opens, starts acquisition on demand, copies one frame, and releases
    all SDK resources in the reverse lifecycle order.
    """

    def __init__(self) -> None:
        """Create a client with no loaded SDK module or allocated camera resource."""

        self._sdk = None  # type: Optional[Any]
        self._camera = None  # type: Optional[Any]
        self._initialized = False
        self._handle_created = False
        self._opened = False
        self._grabbing = False

    def open_camera(self, camera_serial: Optional[str]) -> None:
        """Open one locally selected USB camera without changing camera parameters."""

        if self._opened:
            return
        self._load_sdk()
        self._require_success("MV_CC_Initialize", self._sdk.MvCamera.MV_CC_Initialize())
        self._initialized = True
        try:
            selected_device = self.select_device(self.enumerate_usb_devices(), camera_serial)
            self._camera = self._sdk.MvCamera()
            self._require_success(
                "MV_CC_CreateHandleWithoutLog",
                self._camera.MV_CC_CreateHandleWithoutLog(selected_device.vendor_device_info),
            )
            self._handle_created = True
            self._require_success(
                "MV_CC_OpenDevice",
                self._camera.MV_CC_OpenDevice(self._sdk.MV_ACCESS_Exclusive, 0),
            )
            self._opened = True
        except Exception:
            self.close_camera()
            raise

    def enumerate_usb_devices(self) -> Sequence[MvsUsbDevice]:
        """Return the currently enumerated MVS USB devices without opening them."""

        self._require_loaded_sdk()
        device_list = self._sdk.MV_CC_DEVICE_INFO_LIST()
        self._require_success(
            "MV_CC_EnumDevices",
            self._sdk.MvCamera.MV_CC_EnumDevices(self._sdk.MV_USB_DEVICE, device_list),
        )
        devices = []  # type: List[MvsUsbDevice]
        for index in range(device_list.nDeviceNum):
            device_info = cast(device_list.pDeviceInfo[index], POINTER(self._sdk.MV_CC_DEVICE_INFO)).contents
            usb_info = device_info.SpecialInfo.stUsb3VInfo
            devices.append(
                MvsUsbDevice(
                    self.decode_string(usb_info.chSerialNumber),
                    self.decode_string(usb_info.chModelName),
                    device_info,
                )
            )
        return tuple(devices)

    @staticmethod
    def select_device(devices: Sequence[MvsUsbDevice], camera_serial: Optional[str]) -> MvsUsbDevice:
        """Require an explicit serial when more than one USB camera is present."""

        if camera_serial:
            for device in devices:
                if device.serial_number == camera_serial:
                    return device
            raise MvsCameraClientError("The configured MVS USB camera was not found.")
        if len(devices) != 1:
            raise MvsCameraClientError(
                "MVS USB camera selection requires a local serial number unless exactly one device is present."
            )
        return devices[0]

    def capture_frame(self, timeout_ms: int) -> bytes:
        """Start acquisition when needed, copy one frame, and always release the SDK buffer."""

        if not self._opened or self._camera is None:
            raise MvsCameraClientError("MVS USB camera must be open before frame acquisition.")
        if not self._grabbing:
            self._require_success("MV_CC_StartGrabbing", self._camera.MV_CC_StartGrabbing())
            self._grabbing = True

        frame = self._sdk.MV_FRAME_OUT()
        memset(byref(frame), 0, sizeof(frame))
        self._require_success("MV_CC_GetImageBuffer", self._camera.MV_CC_GetImageBuffer(frame, timeout_ms))
        try:
            frame_length = int(frame.stFrameInfo.nFrameLen)
            if not frame.pBufAddr or frame_length <= 0:
                raise MvsCameraClientError("MVS returned an empty image buffer.")
            return string_at(frame.pBufAddr, frame_length)
        finally:
            if frame.pBufAddr:
                self._require_success("MV_CC_FreeImageBuffer", self._camera.MV_CC_FreeImageBuffer(frame))

    def close_camera(self) -> None:
        """Release acquisition, device, handle, and SDK resources in reverse order."""

        failures = []
        if self._camera is not None and self._grabbing:
            failures.append(("MV_CC_StopGrabbing", self._camera.MV_CC_StopGrabbing()))
            self._grabbing = False
        if self._camera is not None and self._opened:
            failures.append(("MV_CC_CloseDevice", self._camera.MV_CC_CloseDevice()))
            self._opened = False
        if self._camera is not None and self._handle_created:
            failures.append(("MV_CC_DestroyHandle", self._camera.MV_CC_DestroyHandle()))
            self._handle_created = False
        self._camera = None
        if self._sdk is not None and self._initialized:
            failures.append(("MV_CC_Finalize", self._sdk.MvCamera.MV_CC_Finalize()))
            self._initialized = False

        unsuccessful = [(name, result) for name, result in failures if result != 0]
        if unsuccessful:
            operation, result = unsuccessful[0]
            raise MvsCameraClientError("{0} failed with MVS error 0x{1:08X}.".format(operation, result))

    @staticmethod
    def decode_string(values: Any) -> str:
        """Decode a null-terminated MVS ctypes character array without leaking encoding errors."""

        raw_value = memoryview(values).tobytes().split(b"\x00", 1)[0]
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                return raw_value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_value.decode("latin-1", errors="replace")

    def _load_sdk(self) -> None:
        """Expose the bundled Windows runtime before importing the vendor ctypes wrapper."""

        if self._sdk is not None:
            return
        try:
            runtime_package = importlib.import_module(
                "gripper_ai_controller.adapters.hikvision.runtime.win64"
            )
        except ImportError as error:
            raise MvsCameraClientError(
                "Unable to load the project-local MVS Windows x64 runtime and Python wrapper."
            ) from error
        runtime_locations = tuple(getattr(runtime_package, "__path__", ()))
        if len(runtime_locations) != 1:
            raise MvsCameraClientError("The bundled MVS Windows x64 runtime location is unavailable.")
        runtime_directory = runtime_locations[0]
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if runtime_directory not in path_entries:
            os.environ["PATH"] = runtime_directory + os.pathsep + os.environ.get("PATH", "")
        try:
            self._sdk = importlib.import_module(
                "gripper_ai_controller.adapters.hikvision.sdk.MvCameraControl_class"
            )
        except ImportError as error:
            raise MvsCameraClientError(
                "Unable to load the project-local MVS Windows x64 runtime and Python wrapper."
            ) from error

    def _require_loaded_sdk(self) -> None:
        """Raise when the caller tries to use the client before loading the MVS module."""

        if self._sdk is None:
            raise MvsCameraClientError("The MVS SDK has not been loaded.")

    @staticmethod
    def _require_success(operation: str, result: int) -> None:
        """Normalize nonzero MVS result codes into one client-level exception."""

        if result != 0:
            raise MvsCameraClientError("{0} failed with MVS error 0x{1:08X}.".format(operation, result))
