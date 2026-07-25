"""Composition of bounded frame diagnostics and pose-tracking output."""

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any, Callable, Optional, Tuple

from gripper_ai_controller.configuration import PoseTrackingSettings, VisionAnalysisSettings
from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D
from gripper_ai_controller.pose.tracker import PoseTrackingService
from gripper_ai_controller.vision.models import (
    FrameQualityDiagnostics,
    JointVisibility,
    PersonDetection2D,
    VisionAnalysisSnapshot,
)
from gripper_ai_controller.vision.quality import FrameQualityInspector


class JointVisibilityEvaluator:
    """Map a pose candidate to named visible, low-confidence, or out-of-frame joints."""

    def __init__(self, minimum_confidence: float) -> None:
        """Store the same confidence policy used to approve the browser pose target."""

        self.minimum_confidence = minimum_confidence

    def evaluate(
        self,
        joints: Tuple[PoseJoint2D, ...],
        width: Optional[int],
        height: Optional[int],
    ) -> Tuple[JointVisibility, ...]:
        """Return all COCO joint states, including unavailable entries for malformed candidates."""

        joint_by_name = {joint.name: joint for joint in joints}
        result = []
        for name in COCO_KEYPOINT_NAMES:
            joint = joint_by_name.get(name)
            if joint is None:
                result.append(JointVisibility(name, "unavailable", 0.0, 0.0, 0.0))
                continue
            if self._is_out_of_frame(joint, width, height):
                state = "out_of_frame"
            elif joint.confidence < self.minimum_confidence:
                state = "low_confidence"
            else:
                state = "detected"
            result.append(
                JointVisibility(
                    name,
                    state,
                    joint.confidence,
                    joint.normalized_x,
                    joint.normalized_y,
                )
            )
        return tuple(result)

    @staticmethod
    def _is_out_of_frame(joint: PoseJoint2D, width: Optional[int], height: Optional[int]) -> bool:
        """Use normalized coordinates when sampled frame dimensions do not match inference."""

        if (
            joint.normalized_x < 0.0
            or joint.normalized_y < 0.0
            or joint.normalized_x >= 1.0
            or joint.normalized_y >= 1.0
        ):
            return True
        return (
            width is not None
            and height is not None
            and (
                joint.x_px < 0.0
                or joint.y_px < 0.0
                or joint.x_px >= width
                or joint.y_px >= height
            )
        )


class VisionAnalysisService:
    """Retain bounded metadata from camera frames and the existing pose inference service.

    This facade does not own a camera lifecycle or a model task. The preview capture
    loop submits frame health work without waiting for it, while
    ``PoseTrackingService`` remains the single bounded GPU inference scheduler. One
    native worker measures at most one frame at a time; while it is busy, only the
    newest queued frame is retained. This prevents quality analysis from adding a
    camera-frame backlog or blocking acquisition.
    """

    def __init__(
        self,
        camera_id: str,
        settings: VisionAnalysisSettings,
        pose_settings: PoseTrackingSettings,
        pose_tracking_service: Optional[PoseTrackingService],
        monotonic_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Bind one camera's passive diagnostics to its optional pose tracker."""

        self.camera_id = camera_id
        self.pose_tracking_service = pose_tracking_service
        self._inspector = FrameQualityInspector(settings)
        self._visibility = JointVisibilityEvaluator(pose_settings.joint_confidence_threshold)
        self._qualities = OrderedDict()
        self._lock = None  # type: Optional[Any]
        self._monotonic_clock = monotonic_clock or time.monotonic
        maximum_fps = float(getattr(settings, "max_analysis_fps", 1))
        self._analysis_interval_seconds = 1.0 / max(1.0, maximum_fps)
        self._analysis_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vision-quality",
        )
        self._analysis_task = None  # type: Optional[asyncio.Task]
        self._pending_frame = None  # type: Optional[ImageFrame]
        self._last_analysis_started_at = None  # type: Optional[float]
        self._stopped = False
        self._executor_shutdown = False

    async def record_frame(self, frame: ImageFrame) -> None:
        """Queue one frame for bounded asynchronous quality diagnostics.

        This method only replaces the one waiting frame and schedules the worker. It
        never measures pixels on the capture event loop, so callers can safely await
        it for coordination without making image analysis part of acquisition latency.
        """

        if frame.camera_id != self.camera_id or self._stopped:
            return
        async with self._lock_for_current_loop():
            if self._stopped:
                return
            # A new capture supersedes an unprocessed older diagnostic frame. The
            # active worker retains its own frame, so this is one active plus one
            # replaceable pending item rather than an unbounded queue.
            self._pending_frame = frame
            if self._analysis_task is None or self._analysis_task.done():
                self._analysis_task = asyncio.create_task(self._process_pending_frames())

    async def shutdown(self) -> None:
        """Stop accepting frames and release the dedicated quality worker cleanly."""

        async with self._lock_for_current_loop():
            self._stopped = True
            self._pending_frame = None
            task = self._analysis_task
        if task is not None:
            # Do not cancel an executor-backed inspection: cancellation cannot stop
            # the native work and would make deterministic executor shutdown harder.
            await task
        if not self._executor_shutdown:
            self._analysis_executor.shutdown(wait=True)
            self._executor_shutdown = True

    async def snapshot(self) -> VisionAnalysisSnapshot:
        """Combine the latest frame health with cached pose candidates without new inference."""

        async with self._lock_for_current_loop():
            latest_quality = None
            if self._qualities:
                latest_key = next(reversed(self._qualities))
                latest_quality = self._qualities[latest_key]

        if self.pose_tracking_service is None:
            return VisionAnalysisSnapshot(
                self.camera_id,
                None if latest_quality is None else latest_quality.captured_at,
                None,
                False,
                False,
                "Pose tracking is disabled by the current preview configuration.",
                latest_quality,
                (),
                None,
                (),
                (),
            )

        pose_snapshot, inference_snapshot = await self.pose_tracking_service.tracking_snapshots()
        async with self._lock_for_current_loop():
            inference_quality = self._qualities.get(inference_snapshot.captured_at)
        quality = inference_quality if inference_quality is not None else latest_quality
        selected_person = pose_snapshot.person
        persons = self._persons(inference_snapshot.candidates, selected_person)
        visibility = ()
        if selected_person is not None:
            visibility = self._visibility.evaluate(
                selected_person.joints,
                None if inference_quality is None else inference_quality.width,
                None if inference_quality is None else inference_quality.height,
            )
        return VisionAnalysisSnapshot(
            self.camera_id,
            None if latest_quality is None else latest_quality.captured_at,
            inference_snapshot.captured_at,
            True,
            pose_snapshot.valid,
            pose_snapshot.reason,
            quality,
            persons,
            selected_person,
            visibility,
            tuple(joint.name for joint in visibility if joint.state == "detected"),
        )

    async def _process_pending_frames(self) -> None:
        """Consume the newest pending frame at a bounded rate on one native worker."""

        event_loop = asyncio.get_event_loop()
        while True:
            async with self._lock_for_current_loop():
                if self._stopped:
                    self._analysis_task = None
                    return
                last_started_at = self._last_analysis_started_at
                if last_started_at is None:
                    delay_seconds = 0.0
                else:
                    delay_seconds = max(
                        0.0,
                        self._analysis_interval_seconds
                        - (self._monotonic_clock() - last_started_at),
                    )
            if delay_seconds > 0.0:
                await asyncio.sleep(delay_seconds)

            async with self._lock_for_current_loop():
                if self._stopped:
                    self._analysis_task = None
                    return
                frame = self._pending_frame
                self._pending_frame = None
                if frame is None:
                    self._analysis_task = None
                    return
                self._last_analysis_started_at = self._monotonic_clock()

            try:
                diagnostics = await event_loop.run_in_executor(
                    self._analysis_executor,
                    self._inspector.inspect,
                    frame,
                )
            except Exception:
                # The inspector already turns malformed vendor payloads into stable
                # diagnostics. An unexpected failure must still leave acquisition
                # healthy and permit a later latest frame to be analyzed.
                diagnostics = None

            async with self._lock_for_current_loop():
                if diagnostics is not None:
                    self._qualities[frame.captured_at] = diagnostics
                    while len(self._qualities) > 8:
                        self._qualities.popitem(last=False)
                if self._stopped:
                    self._analysis_task = None
                    return
                if self._pending_frame is None:
                    self._analysis_task = None
                    return

    @staticmethod
    def _persons(
        candidates: Tuple[PoseCandidate, ...], selected_person
    ) -> Tuple[PersonDetection2D, ...]:
        """Expose qualified person boxes while marking the same candidate chosen for pose tracking."""

        result = []
        for candidate in candidates:
            selected = (
                selected_person is not None
                and candidate.bounding_box == selected_person.bounding_box
                and candidate.confidence == selected_person.confidence
            )
            result.append(PersonDetection2D(candidate.bounding_box, candidate.confidence, selected))
        return tuple(result)

    def _lock_for_current_loop(self) -> Any:
        """Lazily bind mutable diagnostic state to the ASGI event loop."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock
