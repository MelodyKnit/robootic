"""Single-camera acquisition, controlled parameters, and MJPEG fan-out primitives."""

import asyncio
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.domain.models import (
    CameraParameter,
    CameraParameterApplyMode,
    CameraParameterUpdateResult,
    CameraParameterValue,
)
from gripper_ai_controller.domain.ports import (
    CameraParameterAdapter,
    CameraParameterError,
    VisionAdapter,
)
from gripper_ai_controller.web.codec import JpegFrameEncoder
from gripper_ai_controller.web.config_store import (
    CameraParameterConfigStore,
    CameraParameterConfigStoreError,
)
from gripper_ai_controller.web.models import (
    CameraPreviewError,
    CameraPreviewStatus,
    EncodedFrame,
)


class CameraParameterCapabilityError(RuntimeError):
    """Report that the configured vision adapter does not expose controlled parameters."""


class CameraParameterWriteDisabledError(RuntimeError):
    """Report a parameter write rejected by the local preview configuration."""


class CameraParameterOperationError(RuntimeError):
    """Report a non-validation camera parameter failure without exposing vendor details."""


class CameraParameterPersistenceError(RuntimeError):
    """Report a successful device update that could not be saved for a future restart."""


class CameraParameterRestoreError(RuntimeError):
    """Report a configured parameter replay failure before browser frames are acquired."""


_PERSISTED_PARAMETER_DEPENDENCIES = {
    "exposure_time_us": ("exposure_auto", "Off"),
    "gain_db": ("gain_auto", "Off"),
    "frame_rate_fps": ("frame_rate_enabled", True),
}
"""Manual controls whose enabling value must be replayed with the saved setting."""


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

    async def publish(self, jpeg_payload: bytes, captured_at: float) -> EncodedFrame:
        """Replace the cached frame and notify every stream waiting for a newer image."""

        async with self._condition_for_current_loop():
            self._latest_frame = EncodedFrame(captured_at, jpeg_payload)
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

    async def wait_for_frame(self, previous_frame: Optional[EncodedFrame]) -> Optional[EncodedFrame]:
        """Wait until a different cached JPEG exists or the preview service stops.

        Object identity is enough for MJPEG fan-out because publishing always creates a
        new immutable frame object. This intentionally avoids assigning or retaining a
        camera-frame sequence number solely for browser streaming.
        """

        async with self._condition_for_current_loop():
            while self._state != "stopped" and (
                self._latest_frame is None or self._latest_frame is previous_frame
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
                error=self._error,
            )

    def _condition_for_current_loop(self) -> Any:
        """Create the one shared condition lazily after an ASGI event loop is active."""

        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition


class CameraPreviewService:
    """Own one vision lifecycle, controlled parameter boundary, and shared capture loop.

    The service deliberately never creates ``Runtime``, execution targets, planner
    plugins, or hardware command clients. It is therefore safe to use with a JSON
    configuration that also declares physical robot targets.
    """

    def __init__(
        self,
        camera_id: str,
        vision: VisionAdapter,
        settings: WebPreviewSettings,
        camera_parameter_overrides: Optional[Mapping[str, CameraParameterValue]] = None,
        parameter_store: Optional[CameraParameterConfigStore] = None,
    ) -> None:
        """Bind one configured vision adapter, settings, and optional JSON overrides.

        ``parameter_store`` is intentionally optional for direct unit tests and callers
        that use a transient adapter. A normal CLI-created web application always
        supplies the store bound to its explicit ``--config-file`` argument.
        """

        self.camera_id = camera_id
        self.vision = vision
        self.settings = settings
        self.encoder = JpegFrameEncoder(settings.jpeg_quality)
        self.hub = FrameHub(camera_id)
        self._capture_task = None  # type: Optional[asyncio.Task]
        self._operation_lock = None  # type: Optional[Any]
        self._camera_parameter_overrides = dict(camera_parameter_overrides or {})
        self._parameter_store = parameter_store
        self._camera_parameter_overrides_restored = False

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
        """Stop capture before releasing the adapter without retaining image data on disk.

        The capture task is allowed to finish cancellation before shutdown enters the
        shared operation lock. This protects an MVS handle from being closed while an
        executor thread is still inside a native capture call.
        """

        task = self._capture_task
        self._capture_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            async with self._operation_lock_for_current_loop():
                await self.vision.shutdown()
        finally:
            await self.hub.mark_stopped()

    @property
    def camera_controls_enabled(self) -> bool:
        """Return whether this local configuration permits browser parameter writes."""

        return self.settings.camera_controls_enabled

    async def get_camera_parameters(self) -> Tuple[CameraParameter, ...]:
        """Return the adapter's current normalized parameter whitelist.

        The shared operation lock serializes node inspection with capture and restart
        operations. A failed device read is normalized before it reaches the HTTP API.
        """

        async with self._operation_lock_for_current_loop():
            adapter = self._parameter_adapter()
            try:
                return await adapter.get_camera_parameters()
            except CameraParameterError:
                raise
            except Exception as error:
                raise CameraParameterOperationError(
                    "Unable to read the configured camera parameters."
                ) from error

    async def update_camera_parameters(
        self,
        updates: Mapping[str, CameraParameterValue],
        expected_apply_mode: CameraParameterApplyMode,
    ) -> CameraParameterUpdateResult:
        """Apply one browser request after checking its allowed parameter application mode.

        Immediate and restart-required routes both enter this same method. The expected
        mode prevents callers from using the immediate endpoint to bypass the explicit
        save-and-resume confirmation required by acquisition-locked parameters.
        """

        if not self.camera_controls_enabled:
            raise CameraParameterWriteDisabledError(
                "Camera parameter writes are disabled by the local preview configuration."
            )
        if not updates:
            raise CameraParameterError("At least one camera parameter update is required.")

        async with self._operation_lock_for_current_loop():
            adapter = self._parameter_adapter()
            try:
                parameters = await adapter.get_camera_parameters()
                parameter_by_key = {parameter.key: parameter for parameter in parameters}
                for key in updates:
                    parameter = parameter_by_key.get(key)
                    if parameter is None:
                        raise CameraParameterError("The requested camera parameter is unavailable.")
                    if parameter.apply_mode != expected_apply_mode:
                        raise CameraParameterError(
                            "The requested camera parameter must use the {0} apply endpoint.".format(
                                parameter.apply_mode.value
                            )
                        )
                result = await adapter.update_camera_parameters(updates)
                persisted_values = self._values_to_persist(updates, result.parameters)
                await self._persist_camera_parameter_overrides(persisted_values)
                return result
            except CameraParameterError:
                raise
            except CameraParameterPersistenceError:
                raise
            except Exception as error:
                raise CameraParameterOperationError(
                    "Unable to apply the requested camera parameter update."
                ) from error

    async def _capture_loop(self) -> None:
        """Acquire, encode, and publish frames at the configured bounded rate."""

        interval_seconds = 1.0 / self.settings.stream_fps
        event_loop = asyncio.get_event_loop()
        while True:
            started_at = time.monotonic()
            try:
                async with self._operation_lock_for_current_loop():
                    await self.vision.startup()
                    await self._restore_camera_parameter_overrides()
                    frame = await self.vision.capture()
                jpeg_payload = await event_loop.run_in_executor(None, self.encoder.encode, frame)
                await self.hub.publish(jpeg_payload, frame.captured_at)
            except asyncio.CancelledError:
                raise
            except CameraParameterRestoreError:
                await self.hub.mark_degraded(
                    CameraPreviewError(
                        "camera_parameter_restore_failed",
                        "Configured camera parameters could not be restored and will retry automatically.",
                    )
                )
                await self._reset_vision_after_failure()
                await asyncio.sleep(self.settings.capture_retry_seconds)
                continue
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
            async with self._operation_lock_for_current_loop():
                self._camera_parameter_overrides_restored = False
                await self.vision.shutdown()
        except Exception:
            # The public preview error remains normalized even when SDK cleanup also fails.
            pass

    def _values_to_persist(
        self,
        updates: Mapping[str, CameraParameterValue],
        parameters: Tuple[CameraParameter, ...],
    ) -> Dict[str, CameraParameterValue]:
        """Retain observed updated values and the prerequisite controls needed at startup.

        Persisting every readable device node would make a manual exposure value fail
        when automatic exposure is deliberately enabled later. This method keeps the
        stored document to the user's explicit changes, adding only the corresponding
        prerequisite control for settings that require it.
        """

        observed_values = {parameter.key: parameter.value for parameter in parameters}
        missing_values = set(updates).difference(observed_values)
        if missing_values:
            raise CameraParameterPersistenceError(
                "The camera applied the requested parameter, but did not report its resulting value."
            )
        persisted_values = dict(self._camera_parameter_overrides)
        for key in updates:
            persisted_values[key] = observed_values[key]
        for manual_key, dependency in _PERSISTED_PARAMETER_DEPENDENCIES.items():
            control_key, required_value = dependency
            if manual_key in updates:
                if control_key not in observed_values:
                    raise CameraParameterPersistenceError(
                        "The camera applied the requested parameter, but did not report its prerequisite state."
                    )
                persisted_values[control_key] = observed_values[control_key]
            if control_key in updates and observed_values.get(control_key) != required_value:
                persisted_values.pop(manual_key, None)
        return persisted_values

    async def _persist_camera_parameter_overrides(
        self, values: Mapping[str, CameraParameterValue]
    ) -> None:
        """Atomically persist the complete managed override mapping after device success."""

        try:
            if self._parameter_store is None:
                persisted_values = dict(values)
            else:
                event_loop = asyncio.get_event_loop()
                persisted_values = await event_loop.run_in_executor(
                    None, self._parameter_store.replace_values, values
                )
        except (CameraParameterConfigStoreError, OSError, ValueError) as error:
            # The device already accepted the update. Keep that state for this running
            # service so a transient camera reconnect cannot restore an older file value.
            self._camera_parameter_overrides = dict(values)
            raise CameraParameterPersistenceError(
                "The camera applied the requested parameter, but the local configuration could not be saved."
            ) from error
        except Exception as error:
            # The same in-memory behavior applies to unexpected storage failures.
            self._camera_parameter_overrides = dict(values)
            raise CameraParameterPersistenceError(
                "The camera applied the requested parameter, but the local configuration could not be saved."
            ) from error
        self._camera_parameter_overrides = dict(persisted_values)

    async def _restore_camera_parameter_overrides(self) -> None:
        """Replay configured values once after adapter startup and before the first capture.

        This method must run while the shared operation lock is held. Both provided
        adapters require their lifecycle ``startup`` to finish before controlled
        parameter writes are valid, and a capture must not race this replay.
        """

        if self._camera_parameter_overrides_restored:
            return
        if not self._camera_parameter_overrides:
            self._camera_parameter_overrides_restored = True
            return
        if not self.camera_controls_enabled:
            # One local authorization switch protects browser writes and automatic
            # replay. A disabled control surface must never alter the camera during
            # startup or recovery merely because an older JSON file contains values.
            self._camera_parameter_overrides_restored = True
            return
        try:
            adapter = self._parameter_adapter()
            await adapter.update_camera_parameters(self._camera_parameter_overrides)
        except Exception as error:
            raise CameraParameterRestoreError(
                "Configured camera parameters could not be restored."
            ) from error
        self._camera_parameter_overrides_restored = True

    def _parameter_adapter(self) -> CameraParameterAdapter:
        """Return the optional controlled-parameter port without exposing SDK clients."""

        if not isinstance(self.vision, CameraParameterAdapter):
            raise CameraParameterCapabilityError(
                "The configured camera does not support browser parameter controls."
            )
        return self.vision

    def _operation_lock_for_current_loop(self) -> Any:
        """Create the one cross-operation lock only after an asyncio loop is active."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        return self._operation_lock
