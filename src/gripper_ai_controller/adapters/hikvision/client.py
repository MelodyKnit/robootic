"""Synchronous MVS USB camera boundary used only by the Hikvision adapter."""

import importlib
import os
from ctypes import POINTER, byref, c_bool, c_ubyte, cast, memset, sizeof, string_at
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


@dataclass(frozen=True)
class MvsCapturedFrame:
    """One normalized MVS frame that does not expose ctypes or vendor enum values."""

    pixel_payload: bytes
    width: int
    height: int
    pixel_format: str


@dataclass(frozen=True)
class MvsFloatNodeValue:
    """A vendor float node value with the device-reported writable range."""

    value: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class MvsEnumNodeValue:
    """A vendor enum node represented through stable symbolic option strings."""

    value: str
    options: Sequence[str]


@dataclass(frozen=True)
class MvsBoolNodeValue:
    """A vendor boolean node value normalized without exposing ctypes values."""

    value: bool


class MvsUsbCameraClient:
    """Wrap the blocking MVS SDK lifecycle and frame-copy operations.

    The client owns vendor node access but exposes no arbitrary node names to callers outside
    the Hikvision adapter. It enumerates, opens, starts/stops acquisition, copies frames, and
    reads or writes only adapter-whitelisted parameter nodes before releasing all SDK resources
    in the reverse lifecycle order.
    """

    _FRAME_DELIVERY_MODES = ("latest_only", "sequential")

    def __init__(self, frame_delivery_mode: str = "latest_only") -> None:
        """Create a client with no loaded SDK module or allocated camera resource.

        ``latest_only`` is the interactive-preview default. MVS discards queued frames before
        each delivery so a slow consumer sees the current camera image instead of a FIFO backlog.
        ``sequential`` remains available for an explicit future offline, frame-by-frame workflow.
        """

        if frame_delivery_mode not in self._FRAME_DELIVERY_MODES:
            raise ValueError(
                "MVS frame_delivery_mode must be one of: {0}.".format(
                    ", ".join(self._FRAME_DELIVERY_MODES)
                )
            )

        self._sdk = None  # type: Optional[Any]
        self._camera = None  # type: Optional[Any]
        self._initialized = False
        self._handle_created = False
        self._opened = False
        self._grabbing = False
        self._frame_delivery_mode = frame_delivery_mode

    @property
    def frame_delivery_mode(self) -> str:
        """Return the configured MVS frame-delivery policy without querying the camera."""

        return self._frame_delivery_mode

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

    def capture_frame(self, timeout_ms: int) -> MvsCapturedFrame:
        """Acquire and normalize one frame before always releasing the SDK buffer.

        Mono8 is retained as a generic ``mono8`` payload so a camera that exposes only
        mono output does not depend on undocumented SDK conversion behavior. Higher-bit-depth
        monochrome frames are converted to Mono8, which is directly encodable by the browser
        preview. RGB8 is copied directly; Bayer and other supported color formats are converted
        to RGB8 while the MVS-owned source buffer remains valid.
        """

        self._require_open_camera()
        self.start_grabbing()

        frame = self._sdk.MV_FRAME_OUT()
        memset(byref(frame), 0, sizeof(frame))
        self._require_success("MV_CC_GetImageBuffer", self._camera.MV_CC_GetImageBuffer(frame, timeout_ms))
        try:
            frame_info = frame.stFrameInfo
            width = int(getattr(frame_info, "nExtendWidth", 0) or frame_info.nWidth)
            height = int(getattr(frame_info, "nExtendHeight", 0) or frame_info.nHeight)
            frame_length = int(frame_info.nFrameLen)
            extended_length = int(getattr(frame_info, "nFrameLenEx", 0))
            if not frame.pBufAddr or frame_length <= 0:
                raise MvsCameraClientError("MVS returned an empty image buffer.")
            if width <= 0 or height <= 0:
                raise MvsCameraClientError("MVS returned invalid image dimensions.")
            if extended_length > 0xFFFFFFFF:
                raise MvsCameraClientError("MVS returned a frame too large for pixel conversion.")
            if extended_length not in (0, frame_length):
                raise MvsCameraClientError("MVS returned inconsistent frame-length metadata.")

            source_pixel_type = int(frame_info.enPixelType)
            if source_pixel_type == self._sdk.PixelType_Gvsp_Mono8:
                expected_length = width * height
                if frame_length != expected_length:
                    raise MvsCameraClientError("MVS Mono8 frame length does not match its dimensions.")
                return MvsCapturedFrame(string_at(frame.pBufAddr, frame_length), width, height, "mono8")
            if source_pixel_type == self._sdk.PixelType_Gvsp_RGB8_Packed:
                expected_length = width * height * 3
                if frame_length != expected_length:
                    raise MvsCameraClientError("MVS RGB8 frame length does not match its dimensions.")
                return MvsCapturedFrame(string_at(frame.pBufAddr, frame_length), width, height, "rgb8")
            if source_pixel_type in self._high_bit_depth_monochrome_pixel_types():
                return self._convert_to_mono8(frame, width, height, source_pixel_type, frame_length)
            return self._convert_to_rgb8(frame, width, height, source_pixel_type, frame_length)
        finally:
            if frame.pBufAddr:
                self._require_success("MV_CC_FreeImageBuffer", self._camera.MV_CC_FreeImageBuffer(frame))

    def start_grabbing(self) -> None:
        """Configure delivery and start acquisition before any frame buffer is requested.

        The connected USB camera returns ``MV_E_CALLORDER`` if its grab strategy is changed after
        ``MV_CC_StartGrabbing``. Applying the strategy while the handle is open but inactive keeps
        the first ``MV_CC_GetImageBuffer`` on the selected latest-only or sequential policy and
        permits the same setup after a parameter update restarts acquisition.
        """

        self._require_open_camera()
        if self._grabbing:
            return
        self._configure_frame_delivery_mode()
        self._require_success("MV_CC_StartGrabbing", self._camera.MV_CC_StartGrabbing())
        self._grabbing = True

    def stop_grabbing(self) -> None:
        """Stop acquisition before a node that requires a stopped stream is updated."""

        self._require_open_camera()
        if not self._grabbing:
            return
        self._require_success("MV_CC_StopGrabbing", self._camera.MV_CC_StopGrabbing())
        self._grabbing = False

    def get_float_node(self, node_name: str) -> MvsFloatNodeValue:
        """Read one MVS float node together with its device-reported writable range."""

        self._require_open_camera()
        value = self._sdk.MVCC_FLOATVALUE()
        memset(byref(value), 0, sizeof(value))
        self._require_success(
            "MV_CC_GetFloatValue({0})".format(node_name),
            self._camera.MV_CC_GetFloatValue(node_name, value),
        )
        return MvsFloatNodeValue(float(value.fCurValue), float(value.fMin), float(value.fMax))

    def set_float_node(self, node_name: str, value: float) -> None:
        """Write one validated MVS float node without persisting it to camera storage."""

        self._require_open_camera()
        self._require_success(
            "MV_CC_SetFloatValue({0})".format(node_name),
            self._camera.MV_CC_SetFloatValue(node_name, value),
        )

    def get_enum_node(self, node_name: str) -> MvsEnumNodeValue:
        """Read one MVS enum node and resolve its supported values to symbolic names."""

        self._require_open_camera()
        value = self._sdk.MVCC_ENUMVALUE_EX()
        memset(byref(value), 0, sizeof(value))
        self._require_success(
            "MV_CC_GetEnumValueEx({0})".format(node_name),
            self._camera.MV_CC_GetEnumValueEx(node_name, value),
        )
        options = []  # type: List[str]
        current_value = int(value.nCurValue)
        current_symbolic = None  # type: Optional[str]
        for index in range(int(value.nSupportedNum)):
            entry = self._sdk.MVCC_ENUMENTRY()
            memset(byref(entry), 0, sizeof(entry))
            entry.nValue = value.nSupportValue[index]
            self._require_success(
                "MV_CC_GetEnumEntrySymbolic({0})".format(node_name),
                self._camera.MV_CC_GetEnumEntrySymbolic(node_name, entry),
            )
            symbolic = self.decode_string(entry.chSymbolic)
            options.append(symbolic)
            if int(entry.nValue) == current_value:
                current_symbolic = symbolic
        if current_symbolic is None:
            raise MvsCameraClientError("MVS enum node {0} has no symbolic current value.".format(node_name))
        return MvsEnumNodeValue(current_symbolic, tuple(options))

    def set_enum_node(self, node_name: str, value: str) -> None:
        """Write one validated symbolic MVS enum value without persisting camera settings."""

        self._require_open_camera()
        self._require_success(
            "MV_CC_SetEnumValueByString({0})".format(node_name),
            self._camera.MV_CC_SetEnumValueByString(node_name, value),
        )

    def get_bool_node(self, node_name: str) -> MvsBoolNodeValue:
        """Read one MVS boolean node through the vendor's ctypes API."""

        self._require_open_camera()
        value = c_bool()
        self._require_success(
            "MV_CC_GetBoolValue({0})".format(node_name),
            self._camera.MV_CC_GetBoolValue(node_name, value),
        )
        return MvsBoolNodeValue(bool(value.value))

    def set_bool_node(self, node_name: str, value: bool) -> None:
        """Write one validated MVS boolean node without using a persistent user-set command."""

        self._require_open_camera()
        self._require_success(
            "MV_CC_SetBoolValue({0})".format(node_name),
            self._camera.MV_CC_SetBoolValue(node_name, bool(value)),
        )

    def _configure_frame_delivery_mode(self) -> None:
        """Apply the selected MVS grab strategy while the opened stream is inactive.

        This method intentionally fails closed. The connected USB model also rejects this API
        after stream startup with ``MV_E_CALLORDER``, so callers must invoke it before starting
        acquisition. Older or incomplete vendor SDK copies cannot silently use sequential
        delivery when the configured preview policy requires latest-only frames; callers receive
        a normalized client error instead.
        """

        constant_name = (
            "MV_GrabStrategy_LatestImagesOnly"
            if self._frame_delivery_mode == "latest_only"
            else "MV_GrabStrategy_OneByOne"
        )
        try:
            strategy = getattr(self._sdk, constant_name)
        except AttributeError as error:
            raise MvsCameraClientError(
                "The copied MVS SDK does not provide {0}.".format(constant_name)
            ) from error
        try:
            result = self._camera.MV_CC_SetGrabStrategy(strategy)
        except AttributeError as error:
            raise MvsCameraClientError(
                "The copied MVS camera binding lacks MV_CC_SetGrabStrategy."
            ) from error
        self._require_success("MV_CC_SetGrabStrategy", result)

    def _convert_to_rgb8(
        self,
        frame: Any,
        width: int,
        height: int,
        source_pixel_type: int,
        source_length: int,
    ) -> MvsCapturedFrame:
        """Use MVS conversion before buffer release for supported non-RGB pixel formats."""

        destination_size = width * height * 3
        if destination_size <= 0 or destination_size > 0xFFFFFFFF:
            raise MvsCameraClientError("MVS RGB conversion output size is unsupported.")
        destination = (c_ubyte * destination_size)()
        conversion = self._sdk.MV_CC_PIXEL_CONVERT_PARAM_EX()
        memset(byref(conversion), 0, sizeof(conversion))
        conversion.nWidth = width
        conversion.nHeight = height
        conversion.enSrcPixelType = source_pixel_type
        conversion.pSrcData = cast(frame.pBufAddr, POINTER(c_ubyte))
        conversion.nSrcDataLen = source_length
        conversion.enDstPixelType = self._sdk.PixelType_Gvsp_RGB8_Packed
        conversion.pDstBuffer = cast(destination, POINTER(c_ubyte))
        conversion.nDstBufferSize = destination_size
        self._require_success("MV_CC_ConvertPixelTypeEx", self._camera.MV_CC_ConvertPixelTypeEx(conversion))
        destination_length = int(conversion.nDstLen)
        if destination_length != destination_size:
            raise MvsCameraClientError("MVS RGB conversion returned an unexpected output length.")
        return MvsCapturedFrame(bytes(destination[:destination_length]), width, height, "rgb8")

    def _convert_to_mono8(
        self,
        frame: Any,
        width: int,
        height: int,
        source_pixel_type: int,
        source_length: int,
    ) -> MvsCapturedFrame:
        """Down-convert a higher-bit-depth monochrome MVS frame for JPEG encoding.

        MVS cameras can expose Mono10, Mono12, Mono14, or Mono16 image formats. Asking the
        SDK to convert those source formats directly to RGB8 fails on some camera models. Mono8
        is the compatible intermediate representation and preserves a usable grayscale preview.
        """

        destination_size = width * height
        if destination_size <= 0 or destination_size > 0xFFFFFFFF:
            raise MvsCameraClientError("MVS Mono8 conversion output size is unsupported.")
        destination = (c_ubyte * destination_size)()
        conversion = self._sdk.MV_CC_PIXEL_CONVERT_PARAM_EX()
        memset(byref(conversion), 0, sizeof(conversion))
        conversion.nWidth = width
        conversion.nHeight = height
        conversion.enSrcPixelType = source_pixel_type
        conversion.pSrcData = cast(frame.pBufAddr, POINTER(c_ubyte))
        conversion.nSrcDataLen = source_length
        conversion.enDstPixelType = self._sdk.PixelType_Gvsp_Mono8
        conversion.pDstBuffer = cast(destination, POINTER(c_ubyte))
        conversion.nDstBufferSize = destination_size
        self._require_success("MV_CC_ConvertPixelTypeEx", self._camera.MV_CC_ConvertPixelTypeEx(conversion))
        destination_length = int(conversion.nDstLen)
        if destination_length != destination_size:
            raise MvsCameraClientError("MVS Mono8 conversion returned an unexpected output length.")
        return MvsCapturedFrame(bytes(destination[:destination_length]), width, height, "mono8")

    def _high_bit_depth_monochrome_pixel_types(self) -> Sequence[int]:
        """Return MVS monochrome source constants available in the copied SDK version."""

        names = (
            "PixelType_Gvsp_Mono10",
            "PixelType_Gvsp_Mono10_Packed",
            "PixelType_Gvsp_Mono12",
            "PixelType_Gvsp_Mono12_Packed",
            "PixelType_Gvsp_Mono14",
            "PixelType_Gvsp_Mono16",
        )
        return tuple(getattr(self._sdk, name) for name in names if hasattr(self._sdk, name))

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

    def _require_open_camera(self) -> None:
        """Raise when a vendor node operation is requested before a successful open."""

        if not self._opened or self._camera is None:
            raise MvsCameraClientError("MVS USB camera must be open before this operation.")

    @staticmethod
    def _require_success(operation: str, result: int) -> None:
        """Normalize nonzero MVS result codes into one client-level exception."""

        if result != 0:
            raise MvsCameraClientError("{0} failed with MVS error 0x{1:08X}.".format(operation, result))
