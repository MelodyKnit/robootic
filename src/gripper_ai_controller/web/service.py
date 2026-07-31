"""Single-camera acquisition, controlled parameters, and MJPEG fan-out primitives."""

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from gripper_ai_controller.configuration import (
    CameraSelectionSettings,
    PoseTrackingSettings,
    VisionAnalysisSettings,
    WebPreviewSettings,
)
from gripper_ai_controller.core.plugin_host import PluginHost, PluginHostError
from gripper_ai_controller.domain.models import (
    CameraDeviceDescriptor,
    CameraParameter,
    CameraParameterApplyMode,
    CameraParameterUpdateResult,
    CameraParameterValue,
    ImageFrame,
)
from gripper_ai_controller.domain.ports import (
    CameraParameterAdapter,
    CameraParameterError,
    SelectableVisionAdapter,
    VisionAdapter,
)
from gripper_ai_controller.pose.config_store import PoseTargetConfigStore, PoseTargetConfigStoreError
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseTrackingSnapshot
from gripper_ai_controller.pose.tracker import PoseTargetError, PoseTrackingService
from gripper_ai_controller.object_pose.tracker import ObjectPoseTrackingSnapshot
from gripper_ai_controller.object_detection.tracker import (
    ObjectDetectionTrackingSnapshot,
    UnknownDetectionModelError,
)
from gripper_ai_controller.vision.analysis import VisionAnalysisService
from gripper_ai_controller.vision.models import VisionAnalysisSnapshot
from gripper_ai_controller.web.codec import FrameEncodingError, JpegFrameEncoder
from gripper_ai_controller.web.config_store import (
    CameraParameterConfigStore,
    CameraParameterConfigStoreError,
    CameraSelectionConfigStore,
    CameraSelectionConfigStoreError,
    PluginLifecycleConfigStore,
    PluginLifecycleConfigStoreError,
)
from gripper_ai_controller.web.models import (
    CameraCatalogSnapshot,
    CameraPreviewError,
    CameraPreviewStatus,
    EncodedFrame,
    ObjectPosePreviewSnapshot,
    ObjectDetectionPreviewSnapshot,
    PosePreviewSnapshot,
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


class PoseTrackingCapabilityError(RuntimeError):
    """Report that the configured preview graph does not include pose tracking."""


class PoseTargetPersistenceError(RuntimeError):
    """Report that a valid in-memory target could not be saved to local configuration."""


class CameraSelectionDisabledError(RuntimeError):
    """Report a physical-camera selection blocked by local Web policy."""


class CameraSelectionCapabilityError(RuntimeError):
    """Report that the configured vision adapter cannot enumerate physical devices."""


class CameraDeviceNotFoundError(RuntimeError):
    """Report an opaque camera device identifier absent from a fresh discovery result."""


class CameraSelectionConflictError(RuntimeError):
    """Report a second selection request while another switch is still active."""


class CameraSelectionOperationError(RuntimeError):
    """Report a failed device switch after attempting to restore the previous camera."""


class CameraSelectionPersistenceError(RuntimeError):
    """Report a selected device that could not be saved to the explicit local config."""


class DetectionModelUnavailableError(RuntimeError):
    """Report a configured model whose local asset is not available for selection."""


class PluginLifecycleControlsDisabledError(RuntimeError):
    """Report a browser lifecycle request blocked by the local preview policy."""


class PluginLifecyclePersistenceError(RuntimeError):
    """Report a lifecycle transition whose local configuration state could not be saved."""


_VISUAL_POSE_PLUGIN_ID = "visual-pose-analysis"
_OBJECT_POSE_PLUGIN_ID = "object-pose-analysis"
_OBJECT_DETECTION_PLUGIN_ID = "object-detection-analysis"


_PERSISTED_PARAMETER_DEPENDENCIES = {
    "exposure_time_us": ("exposure_auto", "Off"),
    "gain_db": ("gain_auto", "Off"),
    "frame_rate_fps": ("frame_rate_enabled", True),
}
"""Manual controls whose enabling value must be replayed with the saved setting."""


class FrameHub:
    """Fan out the latest JPEG and retain a tiny cache for in-flight pose inference frames."""

    def __init__(self, camera_id: str) -> None:
        """Create an empty frame cache for one configured camera identifier."""

        self.camera_id = camera_id
        # Python 3.7 requires an active event loop when an asyncio primitive is created.
        # FastAPI constructs the application before it enters its ASGI lifecycle, so this
        # condition is created lazily by the first asynchronous operation instead.
        self._condition = None  # type: Optional[Any]
        self._latest_frame = None  # type: Optional[EncodedFrame]
        self._pose_frames = OrderedDict()
        self._state = "starting"
        self._error = None  # type: Optional[CameraPreviewError]

    async def publish(
        self, jpeg_payload: bytes, captured_at: float, retain_for_pose: bool = False
    ) -> EncodedFrame:
        """Replace the live JPEG and retain only model-dispatched frames for pose rendering.

        At most one Keypoint R-CNN task is in flight. Keeping three JPEGs from accepted
        tasks therefore covers the latest completed result, its replacement candidate,
        and a brief browser poll delay without turning the preview into a frame archive.
        """

        async with self._condition_for_current_loop():
            frame = EncodedFrame(captured_at, jpeg_payload)
            self._latest_frame = frame
            if retain_for_pose:
                self._pose_frames[captured_at] = frame
                self._pose_frames.move_to_end(captured_at)
                while len(self._pose_frames) > 3:
                    self._pose_frames.popitem(last=False)
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

    async def reset_for_camera_switch(self) -> None:
        """Discard every old-device frame before accepting the new camera source.

        Existing MJPEG clients remain connected to the same hub and wait for the
        first frame from the replacement device. Clearing the pose cache here also
        prevents a diagnostic request from retrieving pixels captured by the former
        physical camera after the switch has committed.
        """

        async with self._condition_for_current_loop():
            self._latest_frame = None
            self._pose_frames.clear()
            self._state = "starting"
            self._error = None
            self._condition.notify_all()

    async def latest_frame(self) -> Optional[EncodedFrame]:
        """Return the bounded in-memory frame cache without initiating camera capture."""

        async with self._condition_for_current_loop():
            return self._latest_frame

    async def pose_frame_at(self, captured_at: float) -> Optional[EncodedFrame]:
        """Return a bounded JPEG captured for one accepted pose inference task only."""

        async with self._condition_for_current_loop():
            return self._pose_frames.get(captured_at)

    async def retain_pose_frame(self, frame: EncodedFrame) -> None:
        """Retain one model-source JPEG after the pose worker actually begins inference."""

        async with self._condition_for_current_loop():
            self._pose_frames[frame.captured_at] = frame
            self._pose_frames.move_to_end(frame.captured_at)
            while len(self._pose_frames) > 3:
                self._pose_frames.popitem(last=False)

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


class LatestJpegPublisher:
    """Encode and publish at most one active and one newest pending browser frame."""

    def __init__(
        self,
        encoder: JpegFrameEncoder,
        hub: FrameHub,
        on_frame_published: Optional[Callable[[ImageFrame, EncodedFrame], None]] = None,
    ) -> None:
        """Bind a single-worker encoder and an optional post-publication observer hook.

        The hook runs only after the JPEG replaces the browser-visible latest frame.
        It must be non-blocking; passive plugin work therefore cannot hold the image
        encoder or slow down the next preview frame.
        """

        self._encoder = encoder
        self._hub = hub
        self._on_frame_published = on_frame_published
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jpeg-encoder")
        self._task = None  # type: Optional[asyncio.Task]
        self._pending = None  # type: Optional[Tuple[ImageFrame, bool]]
        self._lock = None  # type: Optional[Any]

    async def submit(self, frame: ImageFrame, retain_for_pose: bool) -> None:
        """Queue the frame only when it is the most recent unavailable-to-encode source."""

        async with self._lock_for_current_loop():
            if self._task is not None and not self._task.done():
                self._pending = (frame, retain_for_pose)
                return
            self._pending = None
            self._task = asyncio.create_task(self._encode_and_publish(frame, retain_for_pose))

    async def shutdown(self) -> None:
        """Drain at most the active encode before releasing the dedicated executor."""

        async with self._lock_for_current_loop():
            self._pending = None
            task = self._task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=True)

    async def reset_frame_state(self) -> None:
        """Drain the active old-device encode and discard its queued replacement.

        The executor remains available for the next camera. A completed old frame may
        briefly enter the hub while this method waits, but the caller resets the hub
        and passive analysis only after this drain has completed.
        """

        async with self._lock_for_current_loop():
            self._pending = None
            task = self._task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _encode_and_publish(self, frame: ImageFrame, retain_for_pose: bool) -> None:
        """Run bounded native JPEG work and replace queued sources after every completion."""

        current_frame = frame
        current_retain_for_pose = retain_for_pose
        while True:
            try:
                jpeg_payload = await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._encoder.encode,
                    current_frame,
                )
            except (FrameEncodingError, ValueError, OSError):
                await self._hub.mark_degraded(
                    CameraPreviewError(
                        "camera_unavailable",
                        "Camera preview is temporarily unavailable and will retry automatically.",
                    )
                )
            except Exception:
                await self._hub.mark_degraded(
                    CameraPreviewError(
                        "camera_unavailable",
                        "Camera preview is temporarily unavailable and will retry automatically.",
                    )
                )
            else:
                encoded_frame = await self._hub.publish(
                    jpeg_payload,
                    current_frame.captured_at,
                    retain_for_pose=current_retain_for_pose,
                )
                if self._on_frame_published is not None:
                    try:
                        self._on_frame_published(current_frame, encoded_frame)
                    except Exception:
                        # A passive observer must never turn an encoded camera frame
                        # into an acquisition failure. PluginHost records its own state.
                        pass

            async with self._lock_for_current_loop():
                if self._pending is None:
                    self._task = None
                    return
                current_frame, current_retain_for_pose = self._pending
                self._pending = None

    def _lock_for_current_loop(self) -> Any:
        """Create the internal scheduling lock only after an asyncio loop exists."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock


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
        pose_tracking_service: Optional[PoseTrackingService] = None,
        pose_target_store: Optional[PoseTargetConfigStore] = None,
        vision_analysis_service: Optional[VisionAnalysisService] = None,
        plugin_host: Optional[PluginHost] = None,
        plugin_lifecycle_store: Optional[PluginLifecycleConfigStore] = None,
        camera_selection_settings: Optional[CameraSelectionSettings] = None,
        camera_selection_store: Optional[CameraSelectionConfigStore] = None,
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
        self._jpeg_publisher = LatestJpegPublisher(
            self.encoder,
            self.hub,
            self._on_jpeg_frame_published,
        )
        self._capture_task = None  # type: Optional[asyncio.Task]
        self._operation_lock = None  # type: Optional[Any]
        self._plugin_operation_lock = None  # type: Optional[Any]
        self._camera_selection_in_progress = False
        self._camera_parameter_overrides = dict(camera_parameter_overrides or {})
        self._parameter_store = parameter_store
        self._camera_parameter_overrides_restored = False
        self.plugin_host = plugin_host
        self._plugin_lifecycle_store = plugin_lifecycle_store
        self.camera_selection_settings = camera_selection_settings or CameraSelectionSettings()
        self._camera_selection_store = camera_selection_store
        self.pose_tracking_service = pose_tracking_service
        self._pose_target_store = pose_target_store
        self.vision_analysis_service = vision_analysis_service
        if self.plugin_host is None and self.vision_analysis_service is None:
            self.vision_analysis_service = VisionAnalysisService(
                camera_id,
                VisionAnalysisSettings(),
                PoseTrackingSettings(),
                pose_tracking_service,
            )
        self._recorder = None  # type: Optional[Any]
        if settings.recording_enabled:
            from gripper_ai_controller.web.recorder import CameraRecorder
            self._recorder = CameraRecorder(Path(settings.recording_output_dir))
        self._latest_captured_frame = None  # type: Optional[ImageFrame]

    async def startup(self) -> None:
        """Begin the single shared acquisition task without starting any execution runtime.

        Camera startup takes place in the retrying capture task. This leaves the HTTP
        status endpoints available when a USB camera is temporarily unplugged or its
        SDK cannot open it at process startup.
        """

        if self._capture_task is not None:
            return
        if self.plugin_host is not None:
            await self.plugin_host.startup()
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
            if self.plugin_host is not None:
                await self.plugin_host.shutdown()
            else:
                if self.pose_tracking_service is not None:
                    await self.pose_tracking_service.shutdown()
                if self.vision_analysis_service is not None:
                    await self.vision_analysis_service.shutdown()
            await self._jpeg_publisher.shutdown()
            async with self._operation_lock_for_current_loop():
                await self.vision.shutdown()
        finally:
            await self.hub.mark_stopped()

    @property
    def camera_controls_enabled(self) -> bool:
        """Return whether the local loopback preview permits browser parameter writes.

        Parameter updates alter live camera state and are therefore subject to the
        same loopback boundary as the other browser-controlled hardware surfaces.
        The application factory rejects an invalid LAN configuration at startup;
        retaining this check here also keeps direct service users fail-closed.
        """

        return bool(
            self.settings.camera_controls_enabled
            and self.settings.bind_host == "127.0.0.1"
        )

    @property
    def camera_selection_enabled(self) -> bool:
        """Return whether local policy and adapter capability permit a device switch."""

        return bool(
            self.camera_selection_settings.enabled
            and self._camera_selection_store is not None
            and isinstance(self.vision, SelectableVisionAdapter)
        )

    @property
    def plugin_lifecycle_controls_enabled(self) -> bool:
        """Return whether this local preview may persist real passive-plugin state changes."""

        return bool(
            self.settings.plugin_lifecycle_controls_enabled
            and self.plugin_host is not None
            and self._plugin_lifecycle_store is not None
        )

    async def get_camera_catalog(self) -> CameraCatalogSnapshot:
        """Discover selectable devices without opening another capture pipeline.

        Discovery failures remain attached to the catalog so the existing active
        preview can continue serving frames. A non-selectable adapter is represented
        by one read-only logical device for backward-compatible browser behavior.
        """

        status = await self.hub.status()
        adapter = self.vision
        if not isinstance(adapter, SelectableVisionAdapter):
            descriptor = CameraDeviceDescriptor(
                device_id=self.camera_id,
                display_name=self.camera_id,
                model_name="Configured camera",
                transport="configured",
                selected=True,
                calibrated=False,
            )
            return CameraCatalogSnapshot(
                status,
                (descriptor,),
                descriptor.device_id,
                False,
                None,
            )

        try:
            async with self._operation_lock_for_current_loop():
                devices = tuple(await adapter.list_camera_devices())
        except asyncio.CancelledError:
            raise
        except Exception:
            return CameraCatalogSnapshot(
                status,
                (),
                adapter.selected_camera_device_id,
                self.camera_selection_enabled,
                CameraPreviewError(
                    "camera_discovery_failed",
                    "Available cameras could not be enumerated. The active preview remains unchanged.",
                ),
            )
        return CameraCatalogSnapshot(
            status,
            devices,
            adapter.selected_camera_device_id,
            self.camera_selection_enabled,
            None,
        )

    async def select_camera_device(self, device_id: str) -> CameraCatalogSnapshot:
        """Persist and activate one freshly discovered physical camera device.

        Selection is serialized with capture, parameter writes, pose-target writes,
        and plugin reload. The old device remains authoritative until the replacement
        opens and its configured parameters validate. Any failure attempts to restore
        the former adapter selection before returning an error to the browser.
        """

        if not self.camera_selection_settings.enabled:
            raise CameraSelectionDisabledError(
                "Camera selection is disabled by the local preview configuration."
            )
        if self._camera_selection_store is None:
            raise CameraSelectionDisabledError(
                "Camera selection requires an explicit local configuration store."
            )
        if not isinstance(device_id, str) or not device_id.strip():
            raise CameraDeviceNotFoundError("The requested camera device is unavailable.")
        adapter = self.vision
        if not isinstance(adapter, SelectableVisionAdapter):
            raise CameraSelectionCapabilityError(
                "The configured camera adapter does not support device selection."
            )
        if self._camera_selection_in_progress:
            raise CameraSelectionConflictError("Another camera selection is already in progress.")

        self._camera_selection_in_progress = True
        try:
            async with self._operation_lock_for_current_loop():
                async with self._plugin_operation_lock_for_current_loop():
                    try:
                        devices = tuple(await adapter.list_camera_devices())
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        raise CameraSelectionOperationError(
                            "Available cameras could not be enumerated."
                        ) from error
                    target = next(
                        (device for device in devices if device.device_id == device_id),
                        None,
                    )
                    if target is None:
                        raise CameraDeviceNotFoundError(
                            "The requested camera device is no longer available."
                        )
                    previous_device_id = adapter.selected_camera_device_id
                    if previous_device_id == device_id:
                        await self._persist_camera_device_selection(device_id)
                    else:
                        await self._switch_camera_device(
                            adapter,
                            previous_device_id,
                            device_id,
                        )
            return await self.get_camera_catalog()
        finally:
            self._camera_selection_in_progress = False

    async def _switch_camera_device(
        self,
        adapter: SelectableVisionAdapter,
        previous_device_id: Optional[str],
        next_device_id: str,
    ) -> None:
        """Replace the selected adapter source and roll back on any commit failure."""

        try:
            await self.vision.shutdown()
            self._camera_parameter_overrides_restored = False
            await adapter.configure_camera_device(next_device_id)
            await self.vision.startup()
            await self._restore_camera_parameter_overrides()
            await self._jpeg_publisher.reset_frame_state()
            await self._reset_passive_frame_state()
            await self.hub.reset_for_camera_switch()
        except asyncio.CancelledError:
            await self._rollback_camera_device(adapter, previous_device_id)
            raise
        except Exception as error:
            await self._rollback_camera_device(adapter, previous_device_id)
            raise CameraSelectionOperationError(
                "The selected camera could not be activated; the previous camera was restored."
            ) from error

        try:
            await self._persist_camera_device_selection(next_device_id)
        except CameraSelectionPersistenceError:
            await self._rollback_camera_device(adapter, previous_device_id)
            raise

    async def _rollback_camera_device(
        self,
        adapter: SelectableVisionAdapter,
        previous_device_id: Optional[str],
    ) -> None:
        """Restore the former adapter source without altering the persisted selection."""

        try:
            await self.vision.shutdown()
            self._camera_parameter_overrides_restored = False
            await adapter.configure_camera_device(previous_device_id)
            await self.vision.startup()
            await self._restore_camera_parameter_overrides()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.hub.mark_degraded(
                CameraPreviewError(
                    "camera_selection_failed",
                    "Camera switching failed and the previous camera could not be restored.",
                )
            )
            raise CameraSelectionOperationError(
                "Camera switching failed and the previous camera could not be restored."
            ) from error

    async def _persist_camera_device_selection(self, device_id: str) -> None:
        """Save one adapter-issued opaque ID with cancellation-safe commit semantics.

        A running executor write cannot be cancelled safely. If the HTTP task is
        cancelled, this method waits for the atomic write to finish. A successful
        write re-raises cancellation and leaves the new camera committed; a failed
        write becomes a persistence error so the caller restores the old camera.
        """

        event_loop = asyncio.get_event_loop()
        write_future = event_loop.run_in_executor(
            None,
            self._camera_selection_store.persist_selected_device_id,
            device_id,
        )
        try:
            await asyncio.shield(write_future)
        except asyncio.CancelledError:
            while not write_future.done():
                try:
                    await asyncio.shield(write_future)
                except asyncio.CancelledError:
                    continue
            try:
                write_future.result()
            except asyncio.CancelledError as error:
                raise CameraSelectionPersistenceError(
                    "The selected camera could not be saved to the local configuration."
                ) from error
            except (CameraSelectionConfigStoreError, OSError, ValueError) as error:
                raise CameraSelectionPersistenceError(
                    "The selected camera could not be saved to the local configuration."
                ) from error
            except Exception as error:
                raise CameraSelectionPersistenceError(
                    "The selected camera could not be saved to the local configuration."
                ) from error
            raise
        except (CameraSelectionConfigStoreError, OSError, ValueError) as error:
            raise CameraSelectionPersistenceError(
                "The selected camera could not be saved to the local configuration."
            ) from error
        except Exception as error:
            raise CameraSelectionPersistenceError(
                "The selected camera could not be saved to the local configuration."
            ) from error

    async def _reset_passive_frame_state(self) -> None:
        """Clear old-device pose and image-quality state without reloading plugins."""

        if self.plugin_host is not None:
            await self.plugin_host.reset_frame_state()
            return
        if self.pose_tracking_service is not None:
            await self.pose_tracking_service.reset_frame_state()
        if self.vision_analysis_service is not None:
            await self.vision_analysis_service.reset_frame_state()

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

    async def get_pose_snapshot(self) -> PoseTrackingSnapshot:
        """Return latest pose metadata without starting an inference or camera operation."""

        plugin = await self._visual_pose_plugin()
        if plugin is not None:
            return await plugin.pose_snapshot()
        if self.pose_tracking_service is None:
            return self._disabled_pose_snapshot()
        return await self.pose_tracking_service.snapshot()

    async def get_pose_preview_snapshot(self) -> PosePreviewSnapshot:
        """Pair current pose output with the latest browser frame for bounded overlay use."""

        snapshot = await self.get_pose_snapshot()
        latest_frame = await self.hub.latest_frame()
        latest_frame_at = None if latest_frame is None else latest_frame.captured_at
        maximum_lag = self._overlay_max_frame_lag_seconds()
        frame_lag = None
        if snapshot.captured_at is not None and latest_frame_at is not None:
            frame_lag = latest_frame_at - snapshot.captured_at
        # A selected joint can be unavailable while the remaining detected skeleton is
        # still valuable for visual diagnostics. Lock validity remains a separate gate.
        overlay_fresh = bool(
            snapshot.person is not None
            and frame_lag is not None
            and 0.0 <= frame_lag <= maximum_lag
        )
        return PosePreviewSnapshot(snapshot, latest_frame_at, overlay_fresh)

    async def get_pose_frame(self, captured_at: float) -> Optional[EncodedFrame]:
        """Return a cache-only JPEG for the pose result's exact source-frame timestamp."""

        if self.plugin_host is None and self.pose_tracking_service is None:
            return None
        return await self.hub.pose_frame_at(captured_at)

    async def get_vision_analysis_snapshot(self) -> VisionAnalysisSnapshot:
        """Return cached frame health and pose-derived recognition metadata without new work."""

        plugin = await self._visual_pose_plugin()
        if plugin is not None:
            return await plugin.analysis_snapshot()
        if self.vision_analysis_service is not None:
            return await self.vision_analysis_service.snapshot()
        return self._disabled_vision_analysis_snapshot()

    async def get_object_pose_snapshot(self) -> ObjectPoseTrackingSnapshot:
        """Return cached known-workpiece metadata without starting capture or inference."""

        plugin = await self._object_pose_plugin()
        if plugin is None:
            return self._disabled_object_pose_snapshot()
        return await plugin.object_snapshot()

    async def get_object_pose_preview_snapshot(self) -> ObjectPosePreviewSnapshot:
        """Pair one object-pose cache result with MJPEG freshness without altering the stream."""

        snapshot = await self.get_object_pose_snapshot()
        latest_frame = await self.hub.latest_frame()
        latest_frame_at = None if latest_frame is None else latest_frame.captured_at
        frame_lag = None
        if snapshot.captured_at is not None and latest_frame_at is not None:
            frame_lag = latest_frame_at - snapshot.captured_at
        overlay_fresh = bool(
            snapshot.valid
            and snapshot.objects
            and frame_lag is not None
            and 0.0 <= frame_lag <= self._object_overlay_max_frame_lag_seconds()
        )
        return ObjectPosePreviewSnapshot(snapshot, latest_frame_at, overlay_fresh)

    async def get_object_detection_snapshot(self) -> ObjectDetectionTrackingSnapshot:
        """Return cached semantic detections without starting capture or inference."""

        plugin = await self._object_detection_plugin()
        if plugin is None:
            return self._disabled_object_detection_snapshot()
        return await plugin.detection_snapshot()

    async def get_object_detection_model_profiles(self):
        """Return configured model metadata without exposing file paths to callers."""

        plugin = await self._object_detection_plugin()
        if plugin is None:
            return ()
        return plugin.model_profiles()

    async def get_object_detection_preview_snapshot(self) -> ObjectDetectionPreviewSnapshot:
        """Pair cached bounding boxes with the latest browser frame timestamp."""

        snapshot = await self.get_object_detection_snapshot()
        latest_frame = await self.hub.latest_frame()
        latest_frame_at = None if latest_frame is None else latest_frame.captured_at
        frame_lag = None
        if snapshot.captured_at is not None and latest_frame_at is not None:
            frame_lag = latest_frame_at - snapshot.captured_at
        overlay_fresh = bool(
            snapshot.valid
            and snapshot.detections
            and frame_lag is not None
            and 0.0 <= frame_lag <= self._detection_overlay_max_frame_lag_seconds()
        )
        return ObjectDetectionPreviewSnapshot(snapshot, latest_frame_at, overlay_fresh)

    async def select_object_detection_model(
        self, model_id: str
    ) -> ObjectDetectionTrackingSnapshot:
        """Select one configured passive provider for the current process session."""

        async with self._plugin_operation_lock_for_current_loop():
            plugin = await self._object_detection_plugin()
            if plugin is None:
                raise DetectionModelUnavailableError(
                    "Object detection is disabled by the current preview configuration."
                )
            snapshot = await plugin.detection_snapshot()
            if not snapshot.enabled:
                raise DetectionModelUnavailableError(
                    "Object detection is disabled by the current preview configuration."
                )
            profile = next(
                (candidate for candidate in plugin.model_profiles() if candidate.model_id == model_id),
                None,
            )
            if profile is None:
                raise UnknownDetectionModelError(
                    "The requested object detection model is not configured."
                )
            if not Path(profile.model_path).is_file():
                raise DetectionModelUnavailableError(
                    "The requested object detection model file is unavailable."
                )
            return await plugin.select_model(model_id)

    async def reload_plugins(self, plugin_ids=()):
        """Serialize preview-plugin reloads with persisted visual target updates.

        This service-level lock is intentionally separate from the camera operation
        lock: a development reload may wait for passive inference cleanup, but it
        must not block capture, JPEG encoding, or adapter lifecycle operations.
        """

        if self.plugin_host is None:
            raise PluginHostError("No preview plugin host is configured.")
        async with self._plugin_operation_lock_for_current_loop():
            return await self.plugin_host.reload(plugin_ids)

    async def set_plugin_enabled(self, plugin_id: str, enabled: bool):
        """Persist and apply one passive plugin lifecycle choice as one service operation.

        Lifecycle transitions run before persistence so a broken plugin never leaves a
        future restart configured to start work that could not begin now. If the local
        JSON write fails, the service restores the previous in-process state before it
        reports the failure. Camera acquisition and MJPEG fan-out are not part of this
        operation and continue independently throughout the transition.
        """

        if not self.plugin_lifecycle_controls_enabled:
            raise PluginLifecycleControlsDisabledError(
                "Plugin lifecycle controls are disabled by the local preview configuration."
            )
        if type(enabled) is not bool:
            raise ValueError("Plugin enabled state must be a boolean.")
        host = self.plugin_host
        store = self._plugin_lifecycle_store
        if host is None or store is None:
            raise PluginLifecycleControlsDisabledError(
                "Plugin lifecycle controls are unavailable for this preview."
            )
        async with self._plugin_operation_lock_for_current_loop():
            current_status = await host.status(plugin_id)
            if current_status.enabled == enabled:
                return current_status

            # Shield the host transition so a disconnected browser cannot cancel it
            # after the passive plugin has changed state but before persistence starts.
            # The same complete-then-propagate-cancellation rule is used by camera
            # selection because both operations have an in-process and a JSON commit.
            transition_task = asyncio.ensure_future(host.set_enabled(plugin_id, enabled))
            next_status, cancellation_requested = await self._complete_cancellation_safe_task(
                transition_task
            )
            try:
                event_loop = asyncio.get_event_loop()
                persistence_task = event_loop.run_in_executor(
                    None, store.persist_enabled, plugin_id, enabled
                )
                _, persistence_cancelled = await self._complete_cancellation_safe_task(
                    persistence_task
                )
                cancellation_requested = cancellation_requested or persistence_cancelled
            except (PluginLifecycleConfigStoreError, OSError, ValueError) as error:
                await self._restore_plugin_enabled_state(host, plugin_id, current_status.enabled)
                raise PluginLifecyclePersistenceError(
                    "The selected plugin state could not be saved to the local configuration."
                ) from error
            except asyncio.CancelledError:
                # Python 3.7 still derives CancelledError from Exception. Do not
                # reinterpret a completed-and-safe caller cancellation as a failed
                # persistence commit that needs a compensating lifecycle transition.
                raise
            except Exception as error:
                await self._restore_plugin_enabled_state(host, plugin_id, current_status.enabled)
                raise PluginLifecyclePersistenceError(
                    "The selected plugin state could not be saved to the local configuration."
                ) from error
            if cancellation_requested:
                raise asyncio.CancelledError()
            return next_status

    async def _restore_plugin_enabled_state(
        self, host: PluginHost, plugin_id: str, enabled: bool
    ) -> None:
        """Restore the prior in-process lifecycle when the local commit step fails."""

        try:
            restore_task = asyncio.ensure_future(host.set_enabled(plugin_id, enabled))
            _, cancellation_requested = await self._complete_cancellation_safe_task(restore_task)
        except PluginHostError as error:
            raise PluginLifecyclePersistenceError(
                "The selected plugin state could not be saved and the previous state could not be restored."
            ) from error
        if cancellation_requested:
            raise asyncio.CancelledError()

    @staticmethod
    async def _complete_cancellation_safe_task(task):
        """Wait for an irreversible task before preserving a caller cancellation.

        ``run_in_executor`` work continues after the awaiting HTTP task is cancelled.
        Plugin lifecycle transitions have the same property once startup or shutdown
        has begun. Shielding the task and collecting cancellation requests ensures the
        caller observes cancellation only after the operation has reached one coherent
        state that can be persisted or compensated.
        """

        cancellation_requested = False
        while True:
            try:
                return await asyncio.shield(task), cancellation_requested
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                if task.done():
                    return task.result(), True
                cancellation_requested = True

    async def update_pose_target(self, target_joint: str) -> PoseTrackingSnapshot:
        """Persist a browser target selection before applying it to the live pose tracker."""

        async with self._plugin_operation_lock_for_current_loop():
            plugin = await self._visual_pose_plugin()
            if (plugin is not None and getattr(plugin, "pose_tracking_service", None) is None) or (
                plugin is None and self.pose_tracking_service is None
            ):
                raise PoseTrackingCapabilityError(
                    "Pose tracking is disabled by the current preview configuration."
                )
            if target_joint not in COCO_KEYPOINT_NAMES:
                # Reject invalid input before any local JSON write so the API can return 422.
                raise PoseTargetError("The requested pose target joint is not supported.")
            if self._pose_target_store is None:
                raise PoseTargetPersistenceError(
                    "Pose target updates require an explicit local configuration store."
                )
            try:
                event_loop = asyncio.get_event_loop()
                await event_loop.run_in_executor(
                    None, self._pose_target_store.persist_target_joint, target_joint
                )
                if plugin is not None:
                    return await plugin.set_target_joint(target_joint)
                return await self.pose_tracking_service.set_target_joint(target_joint)
            except PoseTargetError:
                raise
            except (PoseTargetConfigStoreError, OSError, ValueError) as error:
                raise PoseTargetPersistenceError(
                    "The selected pose target could not be saved locally."
                ) from error
            except Exception as error:
                raise PoseTargetPersistenceError(
                    "The selected pose target could not be saved locally."
                ) from error

    async def _visual_pose_plugin(self):
        """Return the configured passive visual plugin without coupling to its reloaded class object.

        ``importlib.reload`` creates a new plugin class identity, so this boundary
        intentionally checks the narrow snapshot methods rather than using
        ``isinstance`` against an older imported class. The ID remains trusted
        application configuration and never originates in a browser request.
        """

        if self.plugin_host is None:
            return None
        try:
            plugin = await self.plugin_host.get_plugin(_VISUAL_POSE_PLUGIN_ID)
        except PluginHostError:
            return None
        if not all(
            callable(getattr(plugin, method_name, None))
            for method_name in ("pose_snapshot", "analysis_snapshot", "set_target_joint")
        ):
            return None
        return plugin

    async def _object_pose_plugin(self):
        """Return only the trusted passive object plugin's narrow cache interface."""

        if self.plugin_host is None:
            return None
        try:
            plugin = await self.plugin_host.get_plugin(_OBJECT_POSE_PLUGIN_ID)
        except PluginHostError:
            return None
        if not callable(getattr(plugin, "object_snapshot", None)):
            return None
        return plugin

    async def _object_detection_plugin(self):
        """Return only the trusted semantic detector's cache and model-selection surface."""

        if self.plugin_host is None:
            return None
        try:
            plugin = await self.plugin_host.get_plugin(_OBJECT_DETECTION_PLUGIN_ID)
        except PluginHostError:
            return None
        required_methods = (
            "detection_snapshot",
            "model_profiles",
            "select_model",
        )
        if not all(callable(getattr(plugin, method_name, None)) for method_name in required_methods):
            return None
        return plugin

    def _disabled_pose_snapshot(self) -> PoseTrackingSnapshot:
        """Build the legacy disabled response when no visual pose plugin is configured."""

        return PoseTrackingSnapshot(
            self.camera_id,
            None,
            False,
            "Pose tracking is disabled by the current preview configuration.",
            "right_wrist",
            None,
            None,
            None,
            0,
        )

    def _disabled_object_pose_snapshot(self) -> ObjectPoseTrackingSnapshot:
        """Build the safe empty response when object analysis is not configured."""

        return ObjectPoseTrackingSnapshot(
            self.camera_id,
            False,
            None,
            False,
            "object_pose_disabled",
            None,
            (),
        )

    def _disabled_object_detection_snapshot(self) -> ObjectDetectionTrackingSnapshot:
        """Build a safe empty response when semantic detection is not configured."""

        return ObjectDetectionTrackingSnapshot(
            self.camera_id,
            False,
            None,
            None,
            None,
            False,
            "object_detection_disabled",
            None,
            (),
        )

    def _overlay_max_frame_lag_seconds(self) -> float:
        """Read the active tracker's bounded overlay lag without starting work."""

        if self.pose_tracking_service is not None:
            return self.pose_tracking_service.settings.overlay_max_frame_lag_seconds
        if self.plugin_host is not None:
            plugin = self.plugin_host.registry.components.get(_VISUAL_POSE_PLUGIN_ID)
            tracker = getattr(plugin, "pose_tracking_service", None)
            settings = getattr(tracker, "settings", None)
            value = getattr(settings, "overlay_max_frame_lag_seconds", None)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.35

    def _object_overlay_max_frame_lag_seconds(self) -> float:
        """Read only the configured object overlay lag from its dedicated plugin."""

        if self.plugin_host is not None:
            plugin = self.plugin_host.registry.components.get(_OBJECT_POSE_PLUGIN_ID)
            tracker = getattr(plugin, "tracking_service", None)
            settings = getattr(tracker, "settings", None)
            value = getattr(settings, "overlay_max_frame_lag_seconds", None)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.35

    def _detection_overlay_max_frame_lag_seconds(self) -> float:
        """Read only the configured semantic detection overlay lag."""

        if self.plugin_host is not None:
            plugin = self.plugin_host.registry.components.get(_OBJECT_DETECTION_PLUGIN_ID)
            tracker = getattr(plugin, "tracking_service", None)
            value = getattr(tracker, "overlay_max_frame_lag_seconds", None)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.5

    def _disabled_vision_analysis_snapshot(self) -> VisionAnalysisSnapshot:
        """Build a read-only unavailable result when no visual analysis plugin is configured."""

        return VisionAnalysisSnapshot(
            self.camera_id,
            None,
            None,
            False,
            False,
            "Visual analysis is unavailable because no visual analysis plugin is configured.",
            None,
            (),
            None,
            (),
            (),
        )

    async def capture_screenshot(self) -> Mapping[str, Any]:
        """Capture one frame screenshot using the active recorder."""

        if self._recorder is None:
            raise RuntimeError("Recording capability is disabled.")
        frame = self._latest_captured_frame
        if frame is None:
            raise RuntimeError("No camera frame captured yet for screenshot.")
        filepath = self._recorder.capture_screenshot(frame)
        return {
            "success": True,
            "filepath": str(filepath),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def start_recording(self, fps: Optional[int] = None) -> Mapping[str, Any]:
        """Start recording camera frames to video."""

        if self._recorder is None:
            raise RuntimeError("Recording capability is disabled.")
        frame = self._latest_captured_frame
        if frame is None:
            raise RuntimeError("No camera frame captured yet to start recording.")
        actual_fps = fps or self.settings.recording_default_fps
        filepath = self._recorder.start_recording(frame, fps=actual_fps)
        return {
            "success": True,
            "filepath": str(filepath),
            "fps": actual_fps,
        }

    async def stop_recording(self) -> Mapping[str, Any]:
        """Stop current video recording."""

        if self._recorder is None:
            raise RuntimeError("Recording capability is disabled.")
        result = self._recorder.stop_recording()
        if result is None:
            raise RuntimeError("No active recording to stop.")
        filepath, frame_count = result
        return {
            "success": True,
            "filepath": str(filepath),
            "frame_count": frame_count,
            "duration_seconds": round(frame_count / max(1, self.settings.recording_default_fps), 2),
        }

    def get_recording_status(self) -> Mapping[str, Any]:
        """Return live recording status."""

        if self._recorder is None:
            return {
                "enabled": False,
                "is_recording": False,
                "frame_count": 0,
                "output_dir": "",
            }
        return {
            "enabled": True,
            "is_recording": self._recorder.is_recording,
            "frame_count": self._recorder.frame_count,
            "output_dir": str(self._recorder.output_dir),
        }

    async def _capture_loop(self) -> None:
        """Acquire, encode, and publish frames at the configured bounded rate."""

        interval_seconds = 1.0 / self.settings.stream_fps
        while True:
            started_at = time.monotonic()
            try:
                async with self._operation_lock_for_current_loop():
                    await self.vision.startup()
                    await self._restore_camera_parameter_overrides()
                    frame = await self.vision.capture()
                if self.plugin_host is not None:
                    # The preview owns the sole video stream. The publisher offers a
                    # frame to passive plugins only after that JPEG is browser-visible.
                    # A pose source enters the dedicated diagnostic cache later, when
                    # the bounded tracker actually starts inference for this frame.
                    await self._jpeg_publisher.submit(frame, False)
                else:
                    if self.vision_analysis_service is not None:
                        await self.vision_analysis_service.record_frame(frame)
                    pose_frame_scheduled = False
                    if self.pose_tracking_service is not None:
                        pose_frame_scheduled = await self.pose_tracking_service.submit_frame(frame)
                    await self._jpeg_publisher.submit(frame, pose_frame_scheduled)
                    self._latest_captured_frame = frame
                    if self._recorder is not None and self._recorder.is_recording:
                        self._recorder.write_frame(frame)
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

    def _on_jpeg_frame_published(self, frame: ImageFrame, encoded_frame: EncodedFrame) -> None:
        """Offer a published frame to passive plugins and optional recorder."""

        self._latest_captured_frame = frame
        if self._recorder is not None and self._recorder.is_recording:
            self._recorder.write_frame(frame)

        if self.plugin_host is not None:
            async def retain_pose_source() -> None:
                """Keep the source JPEG only after the pose worker selects this frame."""

                await self.hub.retain_pose_frame(encoded_frame)

            self.plugin_host.offer_frame(frame, retain_pose_source)

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

    def _plugin_operation_lock_for_current_loop(self) -> Any:
        """Create the one lock shared by visual target persistence and plugin reload."""

        if self._plugin_operation_lock is None:
            self._plugin_operation_lock = asyncio.Lock()
        return self._plugin_operation_lock
