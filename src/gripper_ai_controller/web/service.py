"""Single-camera acquisition loop and in-memory MJPEG fan-out primitives."""

import asyncio
import time
from typing import Any, Optional

from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.domain.ports import VisionAdapter
from gripper_ai_controller.web.codec import JpegFrameEncoder
from gripper_ai_controller.web.models import (
    CameraPreviewError,
    CameraPreviewStatus,
    EncodedFrame,
)


class FrameHub:
    """Store only the latest JPEG and wake every waiting browser stream subscriber."""

    def __init__(self, camera_id: str) -> None:
        """Create an empty frame cache for one configured camera identifier."""

        self.camera_id = camera_id
        # Python 3.7 requires an active event loop when an asyncio primitive is created.
        # FastAPI constructs the application before it enters its ASGI lifecycle, so this
        # condition is created lazily by the first asynchronous operation instead.
        self._condition = None  # type: Optional[Any]
        self._latest_frame = None  # type: Optional[EncodedFrame]
        self._state = "starting"
        self._error = None  # type: Optional[CameraPreviewError]
        self._sequence = 0

    async def publish(self, jpeg_payload: bytes, captured_at: float) -> EncodedFrame:
        """Replace the cached frame and notify every stream waiting for a newer sequence."""

        async with self._condition_for_current_loop():
            self._sequence += 1
            self._latest_frame = EncodedFrame(self._sequence, captured_at, jpeg_payload)
            self._state = "streaming"
            self._error = None
            self._condition.notify_all()
            return self._latest_frame

    async def mark_degraded(self, error: CameraPreviewError) -> None:
        """Expose a recoverable capture failure without discarding the last valid image."""

        async with self._condition_for_current_loop():
            self._state = "degraded"
            self._error = error
            self._condition.notify_all()

    async def mark_stopped(self) -> None:
        """Mark the service stopped and wake pending streams during application shutdown."""

        async with self._condition_for_current_loop():
            self._state = "stopped"
            self._condition.notify_all()

    async def latest_frame(self) -> Optional[EncodedFrame]:
        """Return the bounded in-memory frame cache without initiating camera capture."""

        async with self._condition_for_current_loop():
            return self._latest_frame

    async def wait_for_frame(self, after_sequence: int) -> Optional[EncodedFrame]:
        """Wait until a newer frame exists or the camera preview service stops."""

        async with self._condition_for_current_loop():
            while self._state != "stopped" and (
                self._latest_frame is None or self._latest_frame.sequence <= after_sequence
            ):
                await self._condition.wait()
            return self._latest_frame

    async def status(self) -> CameraPreviewStatus:
        """Return a consistent camera state snapshot for the JSON API."""

        async with self._condition_for_current_loop():
            latest = self._latest_frame
            return CameraPreviewStatus(
                camera_id=self.camera_id,
                state=self._state,
                latest_frame_at=None if latest is None else latest.captured_at,
                frame_sequence=None if latest is None else latest.sequence,
                error=self._error,
            )

    def _condition_for_current_loop(self) -> Any:
        """Create the one shared condition lazily after an ASGI event loop is active."""

        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition


class CameraPreviewService:
    """Own one vision adapter lifecycle and one bounded-rate camera acquisition loop.

    The service deliberately never creates ``Runtime``, execution targets, planner
    plugins, or hardware command clients. It is therefore safe to use with a JSON
    configuration that also declares physical robot targets.
    """

    def __init__(self, camera_id: str, vision: VisionAdapter, settings: WebPreviewSettings) -> None:
        """Bind one configured vision adapter and its read-only preview settings."""

        self.camera_id = camera_id
        self.vision = vision
        self.settings = settings
        self.encoder = JpegFrameEncoder(settings.jpeg_quality)
        self.hub = FrameHub(camera_id)
        self._capture_task = None  # type: Optional[asyncio.Task]

    async def startup(self) -> None:
        """Begin the single shared acquisition task without starting any execution runtime.

        Camera startup takes place in the retrying capture task. This leaves the HTTP
        status endpoints available when a USB camera is temporarily unplugged or its
        SDK cannot open it at process startup.
        """

        if self._capture_task is not None:
            return
        self._capture_task = asyncio.create_task(self._capture_loop())

    async def shutdown(self) -> None:
        """Stop capture before releasing the adapter without retaining image data on disk."""

        task = self._capture_task
        self._capture_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await self.vision.shutdown()
        finally:
            await self.hub.mark_stopped()

    async def _capture_loop(self) -> None:
        """Acquire, encode, and publish frames at the configured bounded rate."""

        interval_seconds = 1.0 / self.settings.stream_fps
        event_loop = asyncio.get_event_loop()
        while True:
            started_at = time.monotonic()
            try:
                await self.vision.startup()
                frame = await self.vision.capture()
                jpeg_payload = await event_loop.run_in_executor(None, self.encoder.encode, frame)
                await self.hub.publish(jpeg_payload, frame.captured_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.hub.mark_degraded(
                    CameraPreviewError(
                        "camera_unavailable",
                        "Camera preview is temporarily unavailable and will retry automatically.",
                    )
                )
                await self._reset_vision_after_failure()
                await asyncio.sleep(self.settings.capture_retry_seconds)
                continue

            remaining_seconds = interval_seconds - (time.monotonic() - started_at)
            if remaining_seconds > 0:
                await asyncio.sleep(remaining_seconds)

    async def _reset_vision_after_failure(self) -> None:
        """Release a failed acquisition resource so the next retry has a clean lifecycle."""

        try:
            await self.vision.shutdown()
        except Exception:
            # The public preview error remains normalized even when SDK cleanup also fails.
            pass
