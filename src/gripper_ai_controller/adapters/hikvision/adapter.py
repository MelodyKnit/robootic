"""Hikvision USB3 Vision adapter that acquires frames without camera reconfiguration."""

import asyncio
import time
from typing import Any, Callable, Optional

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.adapters.hikvision.client import MvsUsbCameraClient
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame


class HikvisionAdapterError(RuntimeError):
    """Report a normalized Hikvision adapter failure without exposing MVS SDK types."""


class HikvisionAdapter(FrameDispatchingVisionAdapter):
    """Acquire raw frames from one MVS USB3 Vision camera.

    This adapter does not run inference and never writes trigger, exposure, gain, frame-rate,
    persistence, or other device parameters. A configured serial chooses one camera; omitting it
    is allowed only when exactly one MVS USB camera is enumerated.
    """

    manifest = ComponentManifest(
        "hikvision-camera",
        "0.1.0",
        "vision",
        ("vision", "mvs", "usb3-vision", "frame-source", "parameter-write-disabled"),
        "HikvisionAdapter",
    )

    def __init__(
        self,
        camera_id: str,
        calibration_id: Optional[str] = None,
        camera_serial: Optional[str] = None,
        frame_timeout_ms: int = 1000,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Store local camera selection and frame metadata without opening the device."""

        super().__init__()
        if not camera_id:
            raise ValueError("Hikvision camera_id must be a non-empty string.")
        if frame_timeout_ms <= 0:
            raise ValueError("Hikvision frame_timeout_ms must be greater than zero.")
        self.camera_id = camera_id
        self.calibration_id = calibration_id
        self.camera_serial = camera_serial
        self.frame_timeout_ms = frame_timeout_ms
        self.client_factory = client_factory or self.create_vendor_client
        self._client = None  # type: Optional[Any]
        self._connected = False
        self._healthy = False
        self._frame_sequence = 0
        self._capture_lock = None  # type: Optional[Any]

    @property
    def connected(self) -> bool:
        """Return whether the MVS camera handle is currently open."""

        return self._connected

    @property
    def healthy(self) -> bool:
        """Return whether the latest open or frame operation completed successfully."""

        return self._healthy

    @staticmethod
    def create_vendor_client() -> MvsUsbCameraClient:
        """Create the lazy project-local MVS client without importing native code yet."""

        return MvsUsbCameraClient()

    async def on_startup(self) -> None:
        """Open the selected MVS USB camera without starting acquisition or writing parameters."""

        self._client = self.client_factory()
        try:
            await self._call_client_method("open_camera", self.camera_serial)
        except Exception as error:
            try:
                await self._call_client_method("close_camera")
            except Exception:
                pass
            self._client = None
            raise HikvisionAdapterError("Unable to open the configured Hikvision USB camera.") from error
        self._connected = True
        self._healthy = True

    async def on_shutdown(self) -> None:
        """Close the MVS camera and release SDK resources without persisting camera settings."""

        try:
            if self._client is not None:
                await self._call_client_method("close_camera")
        finally:
            self._client = None
            self._connected = False
            self._healthy = False

    async def capture(self) -> ImageFrame:
        """Copy one raw frame from the active camera without running perception or saving files."""

        self.ensure_started()
        if self._capture_lock is None:
            self._capture_lock = asyncio.Lock()
        async with self._capture_lock:
            try:
                pixel_payload = await self._call_client_method("capture_frame", self.frame_timeout_ms)
            except Exception as error:
                self._healthy = False
                raise HikvisionAdapterError("Unable to acquire a frame from the Hikvision USB camera.") from error
            self._frame_sequence += 1
            self._healthy = True
            frame = ImageFrame(
                camera_id=self.camera_id,
                captured_at=time.time(),
                frame_reference="mvs://{0}/frame-{1}".format(self.camera_id, self._frame_sequence),
                calibration_id=self.calibration_id,
                healthy=True,
                pixel_payload=pixel_payload,
            )
            await self.emit_frame(frame)
            return frame

    async def _call_client_method(self, method_name: str, *arguments: Any) -> Any:
        """Run one blocking MVS client operation outside the asyncio event-loop thread."""

        if self._client is None:
            raise HikvisionAdapterError("The Hikvision MVS client is not available.")
        try:
            method = getattr(self._client, method_name)
        except AttributeError as error:
            raise HikvisionAdapterError("The Hikvision MVS client lacks {0}.".format(method_name)) from error
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, method, *arguments)
