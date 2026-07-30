"""Bounded latest-frame tracking for passive semantic object detection."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping, Optional, Tuple

from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.object_detection.models import (
    DetectionCandidate,
    DetectionProvider,
    DetectionProviderError,
)


class UnknownDetectionModelError(ValueError):
    """Report a model identifier outside the trusted configured catalog."""


@dataclass(frozen=True)
class ObjectDetectionTrackingSnapshot:
    """Latest cache-only detection result for one logical camera."""

    camera_id: str
    enabled: bool
    selected_model_id: Optional[str]
    provider_id: Optional[str]
    captured_at: Optional[float]
    valid: bool
    reason: str
    inference_latency_ms: Optional[float]
    detections: Tuple[DetectionCandidate, ...]


@dataclass(frozen=True)
class _DetectionWorkerResult:
    """Synchronous provider output before it is published into async state."""

    model_id: str
    provider_id: str
    captured_at: float
    valid: bool
    reason: str
    inference_latency_ms: float
    detections: Tuple[DetectionCandidate, ...]


class ObjectDetectionTrackingService:
    """Run one model worker while replacing queued input with the newest frame.

    The service owns no camera, robot, gripper, safety policy, or command facade.
    Providers receive only an already captured in-memory ``ImageFrame``.
    """

    def __init__(
        self,
        camera_id: str,
        enabled: bool,
        providers: Mapping[str, DetectionProvider],
        selected_model_id: Optional[str],
        max_analysis_fps: int = 2,
        overlay_max_frame_lag_seconds: float = 0.5,
        setup_error: Optional[str] = None,
        monotonic_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Bind trusted providers without loading weights or touching hardware."""

        if not isinstance(camera_id, str) or not camera_id:
            raise ValueError("Object detection camera_id must be a non-empty string.")
        if type(enabled) is not bool:
            raise ValueError("Object detection enabled must be a boolean.")
        if type(max_analysis_fps) is not int or not 1 <= max_analysis_fps <= 30:
            raise ValueError("Object detection max_analysis_fps must be from 1 to 30.")
        if (
            not isinstance(overlay_max_frame_lag_seconds, (int, float))
            or isinstance(overlay_max_frame_lag_seconds, bool)
            or not 0.01 <= float(overlay_max_frame_lag_seconds) <= 10.0
        ):
            raise ValueError(
                "Object detection overlay_max_frame_lag_seconds must be from 0.01 to 10.0."
            )
        normalized_providers = dict(providers)
        if any(
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(provider, DetectionProvider)
            for model_id, provider in normalized_providers.items()
        ):
            raise ValueError("Object detection providers must map model IDs to providers.")
        if selected_model_id is not None and selected_model_id not in normalized_providers:
            raise UnknownDetectionModelError(
                "The selected object detection model is not configured."
            )

        self.camera_id = camera_id
        self.enabled = enabled
        self.providers = normalized_providers
        self.selected_model_id = selected_model_id
        self.max_analysis_fps = max_analysis_fps
        self.overlay_max_frame_lag_seconds = float(overlay_max_frame_lag_seconds)
        self._setup_error = setup_error
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._snapshot = self._empty_snapshot()
        self._analysis_task = None  # type: Optional[asyncio.Task]
        self._pending_frame = None  # type: Optional[ImageFrame]
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="object-detection"
        )
        self._last_analysis_started_at = None  # type: Optional[float]
        self._lock = None  # type: Optional[Any]
        self._reset_lock = None  # type: Optional[Any]
        self._resetting = False
        self._stopped = False

    async def submit_frame(self, frame: ImageFrame) -> bool:
        """Schedule one frame or replace the queued frame with the newest source."""

        if not self._can_analyze(frame):
            return False
        async with self._lock_for_current_loop():
            if not self._can_analyze(frame):
                return False
            if self._analysis_task is not None and not self._analysis_task.done():
                self._pending_frame = frame
                return True
            self._pending_frame = None
            self._analysis_task = asyncio.create_task(self._run_pipeline(frame))
            return True

    async def snapshot(self) -> ObjectDetectionTrackingSnapshot:
        """Return cached metadata only; never start capture, file I/O, or inference."""

        async with self._lock_for_current_loop():
            return self._snapshot

    async def select_model(self, model_id: str) -> ObjectDetectionTrackingSnapshot:
        """Switch to one configured provider after draining old-model work."""

        if model_id not in self.providers:
            raise UnknownDetectionModelError(
                "The requested object detection model is not configured."
            )
        async with self._reset_lock_for_current_loop():
            async with self._lock_for_current_loop():
                if self._stopped:
                    raise RuntimeError("A stopped object detection service cannot select a model.")
                if model_id == self.selected_model_id:
                    return self._snapshot
                self._resetting = True
                self._pending_frame = None
                task = self._analysis_task
            try:
                if task is not None:
                    await task
                async with self._lock_for_current_loop():
                    self.selected_model_id = model_id
                    self._analysis_task = None
                    self._pending_frame = None
                    self._last_analysis_started_at = None
                    self._snapshot = self._empty_snapshot()
                    return self._snapshot
            finally:
                async with self._lock_for_current_loop():
                    self._resetting = False

    async def reset_frame_state(self) -> None:
        """Discard results and queued frames after a physical camera switch."""

        async with self._reset_lock_for_current_loop():
            async with self._lock_for_current_loop():
                self._resetting = True
                self._pending_frame = None
                task = self._analysis_task
            try:
                if task is not None:
                    await task
                async with self._lock_for_current_loop():
                    self._analysis_task = None
                    self._pending_frame = None
                    self._last_analysis_started_at = None
                    self._snapshot = self._empty_snapshot()
            finally:
                async with self._lock_for_current_loop():
                    self._resetting = False

    async def shutdown(self) -> None:
        """Stop future submissions and release the plugin-owned worker."""

        async with self._lock_for_current_loop():
            if self._stopped:
                return
            self._stopped = True
            self._pending_frame = None
            task = self._analysis_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=True)

    def _can_analyze(self, frame: ImageFrame) -> bool:
        """Reject disabled, malformed, reset, stopped, or other-camera submissions."""

        return bool(
            not self._stopped
            and not self._resetting
            and self.enabled
            and self._setup_error is None
            and self.selected_model_id in self.providers
            and isinstance(frame, ImageFrame)
            and frame.camera_id == self.camera_id
        )

    async def _run_pipeline(self, initial_frame: ImageFrame) -> None:
        """Run the active model, then exchange any stale queue for the newest frame."""

        frame = initial_frame
        while True:
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._analysis_task = None
                    return
                last_started_at = self._last_analysis_started_at
            if last_started_at is not None:
                interval = 1.0 / float(self.max_analysis_fps)
                delay = interval - (self._monotonic_clock() - last_started_at)
                if delay > 0.0:
                    await asyncio.sleep(delay)
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._analysis_task = None
                    return
                model_id = self.selected_model_id
                provider = self.providers.get(model_id)
                if model_id is None or provider is None:
                    self._analysis_task = None
                    return
                self._last_analysis_started_at = self._monotonic_clock()
            result = await self._analyze_frame(model_id, provider, frame)
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._pending_frame = None
                    self._analysis_task = None
                    return
                if result.model_id == self.selected_model_id:
                    self._snapshot = ObjectDetectionTrackingSnapshot(
                        self.camera_id,
                        self.enabled,
                        result.model_id,
                        result.provider_id,
                        result.captured_at,
                        result.valid,
                        result.reason,
                        result.inference_latency_ms,
                        result.detections,
                    )
                next_frame = self._pending_frame
                self._pending_frame = None
                if next_frame is None:
                    self._analysis_task = None
                    return
                frame = next_frame

    async def _analyze_frame(
        self,
        model_id: str,
        provider: DetectionProvider,
        frame: ImageFrame,
    ) -> _DetectionWorkerResult:
        """Run synchronous model work outside the event loop and normalize failures."""

        started_at = self._monotonic_clock()
        try:
            detections = await asyncio.get_event_loop().run_in_executor(
                self._executor, provider.infer, frame
            )
            if not isinstance(detections, tuple) or not all(
                isinstance(detection, DetectionCandidate) for detection in detections
            ):
                raise DetectionProviderError(
                    "The object detection provider returned an invalid result."
                )
        except (DetectionProviderError, ValueError, TypeError, OSError):
            detections = ()
            valid = False
            reason = "detection_inference_failed"
        except Exception:
            detections = ()
            valid = False
            reason = "detection_inference_failed"
        else:
            valid = True
            reason = "detection_available" if detections else "no_detections"
        latency_ms = max(0.0, (self._monotonic_clock() - started_at) * 1000.0)
        return _DetectionWorkerResult(
            model_id,
            provider.provider_id,
            frame.captured_at,
            valid,
            reason,
            latency_ms,
            detections,
        )

    def _empty_snapshot(self) -> ObjectDetectionTrackingSnapshot:
        """Build a safe empty state for disabled or not-yet-run configurations."""

        provider = self.providers.get(self.selected_model_id)
        provider_id = None if provider is None else provider.provider_id
        if not self.enabled:
            reason = "object_detection_disabled"
        elif self._setup_error is not None:
            reason = self._setup_error
        elif self.selected_model_id is None or provider is None:
            reason = "object_detection_model_unavailable"
        else:
            reason = "object_detection_waiting_for_frame"
        return ObjectDetectionTrackingSnapshot(
            self.camera_id,
            self.enabled,
            self.selected_model_id,
            provider_id,
            None,
            False,
            reason,
            None,
            (),
        )

    def _lock_for_current_loop(self) -> Any:
        """Create the scheduling lock only after an asyncio loop exists."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _reset_lock_for_current_loop(self) -> Any:
        """Serialize camera resets, model switches, and shutdown-adjacent drains."""

        if self._reset_lock is None:
            self._reset_lock = asyncio.Lock()
        return self._reset_lock
