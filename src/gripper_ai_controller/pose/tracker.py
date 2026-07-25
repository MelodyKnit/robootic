"""Asynchronous, bounded single-person pose tracking for camera preview."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import math
import time
from typing import Any, Callable, Optional, Tuple

from gripper_ai_controller.configuration import PoseTrackingSettings
from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.pose.estimator import PoseEstimator, PoseEstimatorError
from gripper_ai_controller.pose.models import (
    COCO_KEYPOINT_NAMES,
    HumanPose2D,
    PoseCandidate,
    PoseInferenceSnapshot,
    PoseJoint2D,
    PoseMotion2D,
    PoseTrackingSnapshot,
)


class PoseTargetError(ValueError):
    """Report an invalid target-joint selection before changing in-memory tracking state."""


class PoseTrackingService:
    """Run at most one non-blocking GPU inference task and retain only latest pose metadata.

    Captured pixels are passed to the estimator task and released when it returns. The service
    stores no frame payload or model output on disk, and it never imports a robot or gripper
    adapter. This keeps pose display safe to run beside the read-only camera preview service.
    """

    def __init__(
        self,
        camera_id: str,
        settings: PoseTrackingSettings,
        estimator: PoseEstimator,
        monotonic_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Bind one configured camera, tracking policy, model adapter, and rate-limit clock.

        Production callers leave ``monotonic_clock`` unset and use :func:`time.monotonic`.
        The explicit seam keeps asynchronous rate-limit tests deterministic without changing
        the single-inference scheduling policy used by the web service.
        """

        self.camera_id = camera_id
        self.settings = settings
        self.estimator = estimator
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._snapshot = PoseTrackingSnapshot(
            camera_id,
            None,
            False,
            "Pose tracking is waiting for a camera frame.",
            settings.target_joint,
            None,
            None,
            None,
            0,
        )
        self._inference_task = None  # type: Optional[asyncio.Task]
        self._pending_frame = None  # type: Optional[ImageFrame]
        self._inference_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pose-inference",
        )
        self._last_inference_started_at = None  # type: Optional[float]
        self._lock = None  # type: Optional[Any]
        self._stopped = False
        self._inference_snapshot = PoseInferenceSnapshot(
            camera_id,
            None,
            (),
            "Pose tracking is waiting for a camera frame.",
        )
        self._previous_motion_target = None  # type: Optional[PoseJoint2D]
        self._previous_motion_captured_at = None  # type: Optional[float]
        self._tracked_bounding_box = None  # type: Optional[BoundingBox2D]

    async def submit_frame(self, frame: ImageFrame) -> bool:
        """Schedule one bounded inference and report whether this frame entered the model path."""

        if self._stopped or not self.settings.enabled or frame.camera_id != self.camera_id:
            return False
        async with self._lock_for_current_loop():
            if self._inference_task is not None and not self._inference_task.done():
                # Preserve only the newest source image while GPU inference is active.
                # This bounds memory and prevents a completed result from chasing a FIFO.
                self._pending_frame = frame
                return True
            self._pending_frame = None
            self._inference_task = asyncio.create_task(self._run_inference_pipeline(frame))
            return True

    async def snapshot(self) -> PoseTrackingSnapshot:
        """Return the latest immutable pose state without starting a model inference."""

        async with self._lock_for_current_loop():
            return self._snapshot

    async def inference_snapshot(self) -> PoseInferenceSnapshot:
        """Return latest qualified person detections without scheduling a model inference."""

        async with self._lock_for_current_loop():
            return self._inference_snapshot

    async def tracking_snapshots(self) -> Tuple[PoseTrackingSnapshot, PoseInferenceSnapshot]:
        """Return pose and inference metadata from one atomic tracker state revision.

        The analysis facade uses this method instead of two separate reads so that a
        selected person, its candidate boxes, and their capture time always refer to
        the same completed model inference.
        """

        async with self._lock_for_current_loop():
            return self._snapshot, self._inference_snapshot

    async def set_target_joint(self, target_joint: str) -> PoseTrackingSnapshot:
        """Select one model-supported target joint and invalidate stale lock state safely."""

        if target_joint not in COCO_KEYPOINT_NAMES:
            raise PoseTargetError("The requested pose target joint is not supported.")
        async with self._lock_for_current_loop():
            self.settings = self.settings.with_target_joint(target_joint)
            self._reset_motion()
            person = self._snapshot.person
            target = None if person is None else self._joint_by_name(person.joints, target_joint)
            valid = target is not None and target.confidence >= self.settings.joint_confidence_threshold
            self._snapshot = PoseTrackingSnapshot(
                self.camera_id,
                self._snapshot.captured_at,
                valid,
                "Pose target is available." if valid else "The selected pose target is not currently visible.",
                target_joint,
                target if valid else None,
                person,
                self._snapshot.inference_latency_ms,
                0 if valid else self._snapshot.lost_frames,
                None,
            )
            return self._snapshot

    async def shutdown(self) -> None:
        """Prevent further inference and wait for the one executor-backed task to settle."""

        async with self._lock_for_current_loop():
            self._stopped = True
            self._pending_frame = None
            task = self._inference_task
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._inference_executor.shutdown(wait=True)

    async def _run_inference_pipeline(self, frame: ImageFrame) -> None:
        """Run one inference at a time and replace any queued source with the latest frame."""

        current_frame = frame
        while True:
            async with self._lock_for_current_loop():
                if self._stopped:
                    self._inference_task = None
                    return
                last_started_at = self._last_inference_started_at
            if last_started_at is not None:
                minimum_interval = 1.0 / self.settings.max_inference_fps
                delay = minimum_interval - (self._monotonic_clock() - last_started_at)
                if delay > 0.0:
                    await asyncio.sleep(delay)
            async with self._lock_for_current_loop():
                if self._stopped:
                    self._inference_task = None
                    return
                self._last_inference_started_at = self._monotonic_clock()
            await self._infer_frame(current_frame)
            async with self._lock_for_current_loop():
                if self._stopped:
                    self._pending_frame = None
                    self._inference_task = None
                    return
                next_frame = self._pending_frame
                self._pending_frame = None
                if next_frame is None:
                    self._inference_task = None
                    return
                current_frame = next_frame

    async def _infer_frame(self, frame: ImageFrame) -> None:
        """Run the synchronous estimator outside the event loop then atomically publish its result."""

        started_at = self._monotonic_clock()
        try:
            candidates = await asyncio.get_event_loop().run_in_executor(
                self._inference_executor,
                self.estimator.infer,
                frame,
            )
        except PoseEstimatorError as error:
            await self._record_loss(frame, str(error), self._monotonic_clock() - started_at)
            return
        except Exception:
            await self._record_loss(
                frame,
                "Pose inference is temporarily unavailable.",
                self._monotonic_clock() - started_at,
            )
            return
        await self._record_candidates(frame, candidates, self._monotonic_clock() - started_at)

    async def _record_candidates(
        self, frame: ImageFrame, candidates: Tuple[PoseCandidate, ...], elapsed_seconds: float
    ) -> None:
        """Associate the configured single person before publishing its target joint.

        A detector score alone cannot identify the same person in successive frames.
        Once a person is selected, the next frame must overlap its prior box or remain
        close to its prior box center. An uncertain association is reported as a loss
        and resets the motion baseline instead of manufacturing a cross-person speed.
        """

        qualified_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.confidence >= self.settings.person_confidence_threshold
        )
        async with self._lock_for_current_loop():
            selected, selection_reason, reset_tracking = self._select_candidate(qualified_candidates)
        if selected is None:
            await self._record_loss(
                frame,
                selection_reason,
                elapsed_seconds,
                candidates=(),
                reset_tracking=reset_tracking,
            )
            return
        person = HumanPose2D(
            frame.camera_id,
            frame.captured_at,
            selected.bounding_box,
            selected.confidence,
            selected.joints,
        )
        target = self._joint_by_name(person.joints, self.settings.target_joint)
        if target is None or target.confidence < self.settings.joint_confidence_threshold:
            async with self._lock_for_current_loop():
                self._tracked_bounding_box = selected.bounding_box
            await self._record_loss(
                frame,
                "The selected pose target has insufficient confidence.",
                elapsed_seconds,
                person,
                qualified_candidates,
                False,
            )
            return
        async with self._lock_for_current_loop():
            self._tracked_bounding_box = selected.bounding_box
            motion = self._record_motion(frame.captured_at, target)
            self._inference_snapshot = PoseInferenceSnapshot(
                frame.camera_id,
                frame.captured_at,
                qualified_candidates,
                "Qualified person detections are available.",
            )
            self._snapshot = PoseTrackingSnapshot(
                frame.camera_id,
                frame.captured_at,
                True,
                "Pose target is available.",
                self.settings.target_joint,
                target,
                person,
                elapsed_seconds * 1000.0,
                0,
                motion,
            )

    async def _record_loss(
        self,
        frame: ImageFrame,
        reason: str,
        elapsed_seconds: float,
        person: Optional[HumanPose2D] = None,
        candidates: Tuple[PoseCandidate, ...] = (),
        reset_tracking: bool = False,
    ) -> None:
        """Publish an invalid target while retaining a bounded association grace period."""

        async with self._lock_for_current_loop():
            self._reset_motion()
            lost_frames = self._snapshot.lost_frames + 1
            if reset_tracking or lost_frames >= self.settings.lost_after_frames:
                self._reset_tracking()
            self._inference_snapshot = PoseInferenceSnapshot(
                frame.camera_id,
                frame.captured_at,
                candidates,
                reason,
            )
            self._snapshot = PoseTrackingSnapshot(
                frame.camera_id,
                frame.captured_at,
                False,
                reason,
                self.settings.target_joint,
                None,
                person,
                elapsed_seconds * 1000.0,
                min(lost_frames, self.settings.lost_after_frames),
                None,
            )

    def _record_motion(self, captured_at: float, target: PoseJoint2D) -> Optional[PoseMotion2D]:
        """Compare valid associated joints while rejecting unsafe time discontinuities."""

        previous_target = self._previous_motion_target
        previous_captured_at = self._previous_motion_captured_at
        if previous_target is None or previous_captured_at is None:
            self._previous_motion_target = target
            self._previous_motion_captured_at = captured_at
            return None
        elapsed_seconds = captured_at - previous_captured_at
        if elapsed_seconds <= 0.0:
            # Keep the older valid baseline. A wall-clock correction or duplicate SDK
            # timestamp must not poison a later correctly ordered motion observation.
            return None
        if elapsed_seconds > self.settings.motion_max_interval_seconds:
            # A long acquisition gap has no continuous motion semantics. Establish a
            # fresh baseline and wait for another associated inference result.
            self._previous_motion_target = target
            self._previous_motion_captured_at = captured_at
            return None
        delta_x = target.normalized_x - previous_target.normalized_x
        delta_y = target.normalized_y - previous_target.normalized_y
        displacement = math.hypot(delta_x, delta_y)
        velocity_x = delta_x / elapsed_seconds
        velocity_y = delta_y / elapsed_seconds
        speed = displacement / elapsed_seconds
        self._previous_motion_target = target
        self._previous_motion_captured_at = captured_at
        return PoseMotion2D(
            previous_captured_at,
            captured_at,
            self.settings.target_joint,
            delta_x,
            delta_y,
            displacement,
            velocity_x,
            velocity_y,
            speed,
            speed >= self.settings.motion_speed_threshold,
        )

    def _reset_motion(self) -> None:
        """Discard temporal state after a loss or target selection change.

        A new visible target must provide a second consecutive inference result before
        movement is reported, preventing stale coordinates from being misread as motion.
        """

        self._previous_motion_target = None
        self._previous_motion_captured_at = None

    def _reset_tracking(self) -> None:
        """Forget the selected person after a hard association failure or sustained loss."""

        self._tracked_bounding_box = None
        self._reset_motion()

    def _select_candidate(
        self, candidates: Tuple[PoseCandidate, ...]
    ) -> Tuple[Optional[PoseCandidate], str, bool]:
        """Choose the initial best person or the best conservative continuation match."""

        if not candidates:
            return None, "No sufficiently confident person was detected.", False
        tracked_box = self._tracked_bounding_box
        if tracked_box is None:
            selected = max(candidates, key=lambda candidate: candidate.confidence)
            return selected, "", False

        associated = []
        for candidate in candidates:
            iou = self._bounding_box_iou(tracked_box, candidate.bounding_box)
            center_distance = self._bounding_box_center_distance(tracked_box, candidate.bounding_box)
            if (
                iou >= self.settings.tracking_min_iou
                or center_distance <= self.settings.tracking_max_center_distance
            ):
                associated.append((iou, center_distance, candidate))
        if not associated:
            return (
                None,
                "The previously selected person could not be associated with this frame.",
                True,
            )
        _, _, selected = max(
            associated,
            key=lambda item: (item[0], -item[1], item[2].confidence),
        )
        return selected, "", False

    @staticmethod
    def _bounding_box_iou(first: BoundingBox2D, second: BoundingBox2D) -> float:
        """Compute normalized box overlap without depending on an image-processing library."""

        intersection_left = max(first.x, second.x)
        intersection_top = max(first.y, second.y)
        intersection_right = min(first.x + first.width, second.x + second.width)
        intersection_bottom = min(first.y + first.height, second.y + second.height)
        intersection_width = max(0.0, intersection_right - intersection_left)
        intersection_height = max(0.0, intersection_bottom - intersection_top)
        intersection = intersection_width * intersection_height
        union = first.width * first.height + second.width * second.height - intersection
        return 0.0 if union <= 0.0 else intersection / union

    @staticmethod
    def _bounding_box_center_distance(first: BoundingBox2D, second: BoundingBox2D) -> float:
        """Measure normalized image-space distance between two box centers."""

        first_center_x = first.x + first.width / 2.0
        first_center_y = first.y + first.height / 2.0
        second_center_x = second.x + second.width / 2.0
        second_center_y = second.y + second.height / 2.0
        return math.hypot(second_center_x - first_center_x, second_center_y - first_center_y)

    def _lock_for_current_loop(self) -> Any:
        """Create mutable state protection only after an asyncio loop exists."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _joint_by_name(joints: Tuple[PoseJoint2D, ...], name: str) -> Optional[PoseJoint2D]:
        """Return one named joint from a typed candidate without exposing index-based lookup."""

        for joint in joints:
            if joint.name == name:
                return joint
        return None
