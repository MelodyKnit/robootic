"""Hikvision USB3 Vision adapter with bounded acquisition and parameter controls."""

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.adapters.hikvision.client import MvsCapturedFrame, MvsUsbCameraClient
from gripper_ai_controller.domain.models import (
    CameraParameter,
    CameraParameterApplyMode,
    CameraParameterKind,
    CameraParameterUpdateResult,
    CameraParameterValue,
    ComponentManifest,
    ImageFrame,
)
from gripper_ai_controller.domain.ports import CameraParameterAdapter, CameraParameterError


class HikvisionAdapterError(RuntimeError):
    """Report a normalized Hikvision adapter failure without exposing MVS SDK types."""


@dataclass(frozen=True)
class _CameraParameterNode:
    """Map one browser-safe parameter identifier to one fixed MVS feature node."""

    key: str
    node_name: str
    kind: CameraParameterKind
    apply_mode: CameraParameterApplyMode
    unit: Optional[str] = None


_CAMERA_PARAMETER_NODES = (
    _CameraParameterNode(
        "exposure_auto",
        "ExposureAuto",
        CameraParameterKind.ENUM,
        CameraParameterApplyMode.LIVE,
    ),
    _CameraParameterNode(
        "exposure_time_us",
        "ExposureTime",
        CameraParameterKind.FLOAT,
        CameraParameterApplyMode.LIVE,
        "us",
    ),
    _CameraParameterNode(
        "gain_auto",
        "GainAuto",
        CameraParameterKind.ENUM,
        CameraParameterApplyMode.LIVE,
    ),
    _CameraParameterNode(
        "gain_db",
        "Gain",
        CameraParameterKind.FLOAT,
        CameraParameterApplyMode.LIVE,
        "dB",
    ),
    _CameraParameterNode(
        "frame_rate_enabled",
        "AcquisitionFrameRateEnable",
        CameraParameterKind.BOOLEAN,
        CameraParameterApplyMode.LIVE,
    ),
    _CameraParameterNode(
        "frame_rate_fps",
        "AcquisitionFrameRate",
        CameraParameterKind.FLOAT,
        CameraParameterApplyMode.LIVE,
        "fps",
    ),
    _CameraParameterNode(
        "pixel_format",
        "PixelFormat",
        CameraParameterKind.ENUM,
        CameraParameterApplyMode.RESTART,
    ),
)
"""The only MVS nodes browser clients can inspect or update in this adapter."""


class HikvisionAdapter(FrameDispatchingVisionAdapter, CameraParameterAdapter):
    """Acquire raw frames from one MVS USB3 Vision camera.

    This adapter does not run inference. Its parameter capability is deliberately limited to
    exposure, gain, frame-rate, and pixel-format nodes defined above; it never exposes arbitrary
    MVS node names, trigger control, UserSet persistence, or raw SDK clients. A configured serial
    chooses one camera; omitting it is allowed only when exactly one MVS USB camera is enumerated.
    """

    manifest = ComponentManifest(
        "hikvision-camera",
        "0.1.0",
        "vision",
        ("vision", "mvs", "usb3-vision", "frame-source", "camera-parameters"),
        "HikvisionAdapter",
    )

    _FRAME_DELIVERY_MODES = ("latest_only", "sequential")

    def __init__(
        self,
        camera_id: str,
        calibration_id: Optional[str] = None,
        camera_serial: Optional[str] = None,
        frame_timeout_ms: int = 1000,
        frame_delivery_mode: str = "latest_only",
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Store local camera selection and frame metadata without opening the device."""

        super().__init__()
        if not camera_id:
            raise ValueError("Hikvision camera_id must be a non-empty string.")
        if frame_timeout_ms <= 0:
            raise ValueError("Hikvision frame_timeout_ms must be greater than zero.")
        if frame_delivery_mode not in self._FRAME_DELIVERY_MODES:
            raise ValueError(
                "Hikvision frame_delivery_mode must be one of: {0}.".format(
                    ", ".join(self._FRAME_DELIVERY_MODES)
                )
            )
        self.camera_id = camera_id
        self.calibration_id = calibration_id
        self.camera_serial = camera_serial
        self.frame_timeout_ms = frame_timeout_ms
        self.frame_delivery_mode = frame_delivery_mode
        self.client_factory = client_factory
        self._client = None  # type: Optional[Any]
        self._connected = False
        self._healthy = False
        self._capture_lock = None  # type: Optional[Any]
        self._native_executor = None  # type: Optional[ThreadPoolExecutor]

    @property
    def connected(self) -> bool:
        """Return whether the MVS camera handle is currently open."""

        return self._connected

    @property
    def healthy(self) -> bool:
        """Return whether the latest open or frame operation completed successfully."""

        return self._healthy

    @staticmethod
    def create_vendor_client(frame_delivery_mode: str = "latest_only") -> MvsUsbCameraClient:
        """Create the lazy project-local MVS client without importing native code yet."""

        return MvsUsbCameraClient(frame_delivery_mode=frame_delivery_mode)

    async def on_startup(self) -> None:
        """Open the selected MVS USB camera without starting acquisition or changing settings."""

        self._client = (
            self.client_factory()
            if self.client_factory is not None
            else self.create_vendor_client(self.frame_delivery_mode)
        )
        try:
            await self._call_client_method("open_camera", self.camera_serial)
        except Exception as error:
            try:
                await self._call_client_method("close_camera")
            except Exception:
                pass
            self._client = None
            self._shutdown_native_executor()
            raise HikvisionAdapterError("Unable to open the configured Hikvision USB camera.") from error
        self._connected = True
        self._healthy = True

    async def on_shutdown(self) -> None:
        """Close the MVS camera after any in-flight native capture has returned safely.

        ``capture`` invokes the blocking MVS SDK in an executor thread. Sharing its
        lock here prevents a concurrent shutdown from destroying the SDK handle while
        that thread is still using the handle or its MVS-managed image buffer.
        """

        try:
            if self._capture_lock is None:
                await self._close_client()
            else:
                async with self._capture_lock:
                    await self._close_client()
        finally:
            self._capture_lock = None
            self._shutdown_native_executor()

    async def capture(self) -> ImageFrame:
        """Map one normalized MVS frame without running perception or saving files."""

        self.ensure_started()
        if self._capture_lock is None:
            self._capture_lock = asyncio.Lock()
        async with self._capture_lock:
            try:
                captured_frame = await self._call_client_method("capture_frame", self.frame_timeout_ms)
            except asyncio.CancelledError:
                # Let the executor-backed call complete before BaseAdapter may close the SDK handle.
                raise
            except Exception as error:
                self._healthy = False
                raise HikvisionAdapterError("Unable to acquire a frame from the Hikvision USB camera.") from error
            if not isinstance(captured_frame, MvsCapturedFrame):
                self._healthy = False
                raise HikvisionAdapterError("The Hikvision MVS client returned an invalid normalized frame.")
            self._healthy = True
            frame = ImageFrame(
                camera_id=self.camera_id,
                captured_at=time.time(),
                frame_reference="mvs://{0}/live".format(self.camera_id),
                calibration_id=self.calibration_id,
                healthy=True,
                pixel_payload=captured_frame.pixel_payload,
                width=captured_frame.width,
                height=captured_frame.height,
                pixel_format=captured_frame.pixel_format,
            )
            await self.emit_frame(frame)
            return frame

    async def get_camera_parameters(self) -> Tuple[CameraParameter, ...]:
        """Read the current runtime-supported whitelist without exposing vendor nodes.

        A camera model can omit a node or make it unavailable in its current state. Those
        individual entries are omitted instead of inventing ranges or options; an all-node
        failure remains an adapter error so the web service can report the camera unavailable.
        """

        self.ensure_started()
        async with self._capture_lock_for_current_loop():
            return await self._read_supported_parameters()

    async def update_camera_parameters(
        self, updates: Mapping[str, CameraParameterValue]
    ) -> CameraParameterUpdateResult:
        """Validate and apply fixed-whitelist settings without persisting them to the device.

        Capture, node writes, and stop/start acquisition all share one lock. Parameters marked
        ``restart`` stop only the stream, apply their values, and always attempt to restart the
        stream before the result or error leaves this adapter.
        """

        self.ensure_started()
        if not updates:
            raise CameraParameterError("At least one camera parameter update is required.")
        async with self._capture_lock_for_current_loop():
            parameters = await self._read_supported_parameters()
            parameter_by_key = {parameter.key: parameter for parameter in parameters}
            normalized_updates = self._normalize_updates(updates, parameter_by_key)
            self._validate_parameter_dependencies(parameter_by_key, normalized_updates)
            restart_required = any(
                parameter_by_key[key].apply_mode == CameraParameterApplyMode.RESTART
                for key in normalized_updates
            )
            try:
                if restart_required:
                    await self._call_client_method("stop_grabbing")
                try:
                    await self._write_parameters(normalized_updates)
                finally:
                    if restart_required:
                        await self._call_client_method("start_grabbing")
                updated_parameters = await self._read_supported_parameters()
            except CameraParameterError:
                raise
            except Exception as error:
                self._healthy = False
                raise HikvisionAdapterError(
                    "Unable to apply the requested Hikvision camera parameter update."
                ) from error
            self._healthy = True
            return CameraParameterUpdateResult(updated_parameters, restart_required)

    async def _call_client_method(self, method_name: str, *arguments: Any) -> Any:
        """Run one blocking MVS client operation outside the asyncio event-loop thread."""

        if self._client is None:
            raise HikvisionAdapterError("The Hikvision MVS client is not available.")
        try:
            method = getattr(self._client, method_name)
        except AttributeError as error:
            raise HikvisionAdapterError("The Hikvision MVS client lacks {0}.".format(method_name)) from error
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(self._native_executor_for_current_lifecycle(), method, *arguments)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Cancellation of the coroutine cannot stop a native SDK thread. Wait for
            # it to finish before allowing lifecycle shutdown to release the handle.
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise

    async def _read_supported_parameters(self) -> Tuple[CameraParameter, ...]:
        """Read every available fixed node, skipping features unsupported by this camera model."""

        parameters = []
        for definition in _CAMERA_PARAMETER_NODES:
            try:
                parameters.append(await self._read_parameter(definition))
            except asyncio.CancelledError:
                raise
            except Exception:
                # MVS reports unsupported, unavailable, or access-locked optional nodes through
                # the same result channel. The whitelist is intentionally capability-driven.
                continue
        if not parameters:
            raise HikvisionAdapterError("No supported Hikvision camera parameters are currently readable.")
        return tuple(parameters)

    async def _read_parameter(self, definition: _CameraParameterNode) -> CameraParameter:
        """Map one vendor node value and device-reported constraints to a normalized descriptor."""

        if definition.kind == CameraParameterKind.FLOAT:
            value = await self._call_client_method("get_float_node", definition.node_name)
            return CameraParameter(
                key=definition.key,
                kind=definition.kind,
                apply_mode=definition.apply_mode,
                value=float(value.value),
                minimum=float(value.minimum),
                maximum=float(value.maximum),
                unit=definition.unit,
            )
        if definition.kind == CameraParameterKind.ENUM:
            value = await self._call_client_method("get_enum_node", definition.node_name)
            return CameraParameter(
                key=definition.key,
                kind=definition.kind,
                apply_mode=definition.apply_mode,
                value=value.value,
                unit=definition.unit,
                options=tuple(value.options),
            )
        value = await self._call_client_method("get_bool_node", definition.node_name)
        return CameraParameter(
            key=definition.key,
            kind=definition.kind,
            apply_mode=definition.apply_mode,
            value=bool(value.value),
            unit=definition.unit,
        )

    @staticmethod
    def _normalize_updates(
        updates: Mapping[str, CameraParameterValue],
        parameter_by_key: Mapping[str, CameraParameter],
    ) -> Mapping[str, CameraParameterValue]:
        """Reject unknown, malformed, or out-of-range values before calling the vendor SDK."""

        normalized = {}
        for key, value in updates.items():
            parameter = parameter_by_key.get(key)
            if parameter is None:
                raise CameraParameterError("The requested camera parameter is unavailable.")
            if parameter.kind == CameraParameterKind.FLOAT:
                if type(value) not in (int, float) or not math.isfinite(float(value)):
                    raise CameraParameterError("The requested camera parameter requires a finite number.")
                numeric_value = float(value)
                if parameter.minimum is None or parameter.maximum is None:
                    raise CameraParameterError("The camera did not report a writable numeric range.")
                if numeric_value < parameter.minimum or numeric_value > parameter.maximum:
                    raise CameraParameterError("The requested camera parameter value is outside the device range.")
                normalized[key] = numeric_value
            elif parameter.kind == CameraParameterKind.ENUM:
                if not isinstance(value, str) or value not in parameter.options:
                    raise CameraParameterError("The requested camera enum value is not supported by the device.")
                normalized[key] = value
            elif type(value) is bool:
                normalized[key] = value
            else:
                raise CameraParameterError("The requested camera parameter requires a boolean value.")
        return normalized

    @staticmethod
    def _validate_parameter_dependencies(
        parameters: Mapping[str, CameraParameter],
        updates: Mapping[str, CameraParameterValue],
    ) -> None:
        """Enforce device feature dependencies even when a caller bypasses the web UI."""

        projected = {key: parameter.value for key, parameter in parameters.items()}
        projected.update(updates)
        if "exposure_time_us" in updates and projected.get("exposure_auto") not in (None, "Off"):
            raise CameraParameterError("Manual exposure requires automatic exposure to be Off.")
        if "gain_db" in updates and projected.get("gain_auto") not in (None, "Off"):
            raise CameraParameterError("Manual gain requires automatic gain to be Off.")
        if "frame_rate_fps" in updates and projected.get("frame_rate_enabled") is not True:
            raise CameraParameterError("Manual frame rate requires frame-rate control to be enabled.")

    async def _write_parameters(self, updates: Mapping[str, CameraParameterValue]) -> None:
        """Write a validated batch in dependency order without calling MVS persistence features."""

        for definition in _CAMERA_PARAMETER_NODES:
            if definition.key not in updates:
                continue
            value = updates[definition.key]
            if definition.kind == CameraParameterKind.FLOAT:
                await self._call_client_method("set_float_node", definition.node_name, float(value))
            elif definition.kind == CameraParameterKind.ENUM:
                await self._call_client_method("set_enum_node", definition.node_name, value)
            else:
                await self._call_client_method("set_bool_node", definition.node_name, bool(value))

    def _capture_lock_for_current_loop(self) -> Any:
        """Create the one native-operation lock lazily after an asyncio loop is active."""

        if self._capture_lock is None:
            self._capture_lock = asyncio.Lock()
        return self._capture_lock

    def _native_executor_for_current_lifecycle(self) -> ThreadPoolExecutor:
        """Return the one-worker executor that serializes all vendor SDK calls.

        MVS manages camera handles and image buffers outside Python. A private, bounded executor
        prevents capture, parameter reads, restart operations, and shutdown from competing with
        unrelated default-executor work such as JPEG encoding or model inference.
        """

        if self._native_executor is None:
            self._native_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="hikvision-mvs",
            )
        return self._native_executor

    def _shutdown_native_executor(self) -> None:
        """Join the dedicated MVS worker after serialized lifecycle cleanup completes."""

        if self._native_executor is None:
            return
        self._native_executor.shutdown(wait=True)
        self._native_executor = None

    async def _close_client(self) -> None:
        """Release the current client and state only after a serialized close attempt."""

        try:
            if self._client is not None:
                await self._call_client_method("close_camera")
        finally:
            self._client = None
            self._connected = False
            self._healthy = False
