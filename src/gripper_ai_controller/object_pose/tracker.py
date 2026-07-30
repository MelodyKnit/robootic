"""Bounded passive known-workpiece tracking and calibrated plane projection."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import math
import time
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple

from gripper_ai_controller.calibration.errors import CalibrationError
from gripper_ai_controller.calibration.models import PixelPoint, Point3D
from gripper_ai_controller.calibration.projection import PixelPlaneProjector
from gripper_ai_controller.calibration.workcell import WorkcellCalibration
from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.object_pose.models import (
    NormalizedPoint2D,
    ObjectPoseAnalysis,
    ObjectPoseCandidate,
    ObjectPoseEstimator,
    PixelPoint2D,
)

if TYPE_CHECKING:
    from gripper_ai_controller.configuration import WorkpiecePoseSettings


class _PlanaritySuspectedError(ValueError):
    """Reject a candidate whose calibrated in-plane dimensions contradict its profile."""


@dataclass(frozen=True)
class OrientationRpy:
    """A derived fixed-plane orientation in the JAKA base coordinate frame."""

    roll_rad: float
    pitch_rad: float
    yaw_rad: float


@dataclass(frozen=True)
class WorkpiecePose:
    """Known-workpiece metadata with explicit observed and derived freedoms.

    The class has no grasp candidate or command. ``translation_mm`` is available
    only after a pixel is projected through the calibrated board plane.
    """

    profile_id: str
    confidence: float
    contour: Tuple[NormalizedPoint2D, ...]
    bounding_box: BoundingBox2D
    pixel_center: PixelPoint2D
    normalized_center: NormalizedPoint2D
    coordinate_frame: str
    translation_mm: Point3D
    orientation_rpy_rad: OrientationRpy
    observed_dof: Tuple[str, ...]
    derived_dof: Tuple[str, ...]
    yaw_period_rad: Optional[float]
    orientation_defined: bool
    warning: Optional[str]


@dataclass(frozen=True)
class ObjectPoseTrackingSnapshot:
    """Latest cache-only object-pose state, independent from the MJPEG stream."""

    camera_id: str
    enabled: bool
    captured_at: Optional[float]
    valid: bool
    reason: str
    inference_latency_ms: Optional[float]
    objects: Tuple[WorkpiecePose, ...]


@dataclass(frozen=True)
class _FrameAnalysisResult:
    """Worker result before it changes shared asynchronous state."""

    captured_at: float
    valid: bool
    reason: str
    object_pose: Optional[WorkpiecePose]
    inference_latency_ms: float


class ObjectPoseTrackingService:
    """Run one passive detector worker while retaining only the newest pending frame.

    The service observes already captured images only. It owns immutable metadata and
    one CPU worker; it does not retain JPEGs, import a camera adapter, open a robot
    connection, or expose a command method.
    """

    def __init__(
        self,
        camera_id: str,
        settings: "WorkpiecePoseSettings",
        estimator: Optional[ObjectPoseEstimator],
        workcell_calibration: Optional[WorkcellCalibration],
        setup_error: Optional[str] = None,
        monotonic_clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Bind trusted static dependencies without starting a device or writing a file."""

        self.camera_id = camera_id
        self.settings = settings
        self.estimator = estimator
        self.workcell_calibration = workcell_calibration
        self._setup_error = setup_error
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._snapshot = ObjectPoseTrackingSnapshot(
            camera_id, settings.enabled, None, False, self._initial_reason(), None, ()
        )
        self._analysis_task = None  # type: Optional[asyncio.Task]
        self._pending_frame = None  # type: Optional[ImageFrame]
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="object-pose")
        self._last_analysis_started_at = None  # type: Optional[float]
        self._lock = None  # type: Optional[Any]
        self._reset_lock = None  # type: Optional[Any]
        self._resetting = False
        self._stopped = False
        self._stable_samples = []  # type: list

    async def submit_frame(self, frame: ImageFrame) -> bool:
        """Schedule one frame or replace the queued source with the latest one."""

        if not self._can_analyze(frame):
            return False
        async with self._lock_for_current_loop():
            if not self._can_analyze(frame):
                return False
            if self._analysis_task is not None and not self._analysis_task.done():
                self._pending_frame = frame
                return True
            self._pending_frame = None
            self._analysis_task = asyncio.create_task(self._run_analysis_pipeline(frame))
            return True

    async def snapshot(self) -> ObjectPoseTrackingSnapshot:
        """Return cached metadata only; never trigger capture or detector work."""

        async with self._lock_for_current_loop():
            return self._snapshot

    async def reset_frame_state(self) -> None:
        """Drain the worker and invalidate results from a prior camera selection."""

        async with self._reset_lock_for_current_loop():
            async with self._lock_for_current_loop():
                self._resetting = True
                self._pending_frame = None
                task = self._analysis_task
            try:
                if task is not None:
                    await task
                async with self._lock_for_current_loop():
                    self._pending_frame = None
                    self._analysis_task = None
                    self._last_analysis_started_at = None
                    self._stable_samples = []
                    self._snapshot = ObjectPoseTrackingSnapshot(
                        self.camera_id,
                        self.settings.enabled,
                        None,
                        False,
                        self._initial_reason(),
                        None,
                        (),
                    )
            finally:
                async with self._lock_for_current_loop():
                    self._resetting = False

    async def shutdown(self) -> None:
        """Stop future submissions and release only the plugin-owned worker."""

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
        """Reject disabled, reset, malformed graph, and other-camera submissions early."""

        return bool(
            not self._stopped
            and not self._resetting
            and self.settings.enabled
            and self.estimator is not None
            and self.workcell_calibration is not None
            and self._setup_error is None
            and frame.camera_id == self.camera_id
        )

    def _initial_reason(self) -> str:
        """Return an explicit safe state reason without touching a hardware dependency."""

        if not self.settings.enabled:
            return "object_pose_disabled"
        if self._setup_error is not None:
            return self._setup_error
        if self.estimator is None:
            return "background_unavailable"
        if self.workcell_calibration is None:
            return "calibration_unavailable"
        return "object_pose_waiting_for_frame"

    async def _run_analysis_pipeline(self, initial_frame: ImageFrame) -> None:
        """Process active input then exchange any stale queued source for the newest frame."""

        frame = initial_frame
        while True:
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._analysis_task = None
                    return
                last_started_at = self._last_analysis_started_at
            if last_started_at is not None:
                interval = 1.0 / float(self.settings.max_analysis_fps)
                delay = interval - (self._monotonic_clock() - last_started_at)
                if delay > 0.0:
                    await asyncio.sleep(delay)
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._analysis_task = None
                    return
                self._last_analysis_started_at = self._monotonic_clock()
            result = await self._analyze_frame(frame)
            async with self._lock_for_current_loop():
                if self._stopped or self._resetting:
                    self._pending_frame = None
                    self._analysis_task = None
                    return
                self._publish_result(result)
                next_frame = self._pending_frame
                self._pending_frame = None
                if next_frame is None:
                    self._analysis_task = None
                    return
                frame = next_frame

    async def _analyze_frame(self, frame: ImageFrame) -> _FrameAnalysisResult:
        """Run segmentation plus calibrated projection outside the ASGI event loop."""

        started_at = self._monotonic_clock()
        try:
            return await asyncio.get_event_loop().run_in_executor(
                self._executor, self._analyze_frame_synchronously, frame, started_at
            )
        except Exception:
            return _FrameAnalysisResult(
                frame.captured_at,
                False,
                "object_pose_analysis_failed",
                None,
                (self._monotonic_clock() - started_at) * 1000.0,
            )

    def _analyze_frame_synchronously(
        self, frame: ImageFrame, started_at: float
    ) -> _FrameAnalysisResult:
        """Run all pixel and plane calculations with no shared-state side effects."""

        try:
            analysis = self.estimator.analyze(frame)  # type: ignore[union-attr]
            if not analysis.valid:
                return self._invalid_analysis_result(analysis, started_at)
            if len(analysis.candidates) != 1:
                return self._failed_result(frame, "multiple_candidates", started_at)
            if frame.calibration_id != self.settings.expected_calibration_id:
                return self._failed_result(frame, "calibration_id_mismatch", started_at)
            calibration = self.workcell_calibration
            if calibration is None:
                return self._failed_result(frame, "calibration_unavailable", started_at)
            if calibration.calibration_id != self.settings.expected_calibration_id:
                return self._failed_result(frame, "calibration_id_mismatch", started_at)
            if calibration.camera_calibration.reprojection_error_px > self.settings.maximum_reprojection_error_px:
                return self._failed_result(frame, "calibration_reprojection_error_excessive", started_at)
            if frame.width is None or frame.height is None:
                return self._failed_result(frame, "frame_dimensions_invalid", started_at)
            projector = calibration.plane_projector_for_frame(
                frame.camera_id,
                frame.calibration_id,
                frame.width,
                frame.height,
                maximum_rms_error_mm=self.settings.maximum_base_fit_rms_error_mm,
            )
            object_pose = self._workpiece_pose_from_candidate(
                analysis.candidates[0], frame, projector, calibration
            )
            return _FrameAnalysisResult(
                frame.captured_at,
                True,
                "object_pose_available",
                object_pose,
                (self._monotonic_clock() - started_at) * 1000.0,
            )
        except _PlanaritySuspectedError:
            return self._failed_result(frame, "planarity_suspected", started_at)
        except CalibrationError:
            return self._failed_result(frame, "calibration_invalid", started_at)
        except (ArithmeticError, TypeError, ValueError):
            return self._failed_result(frame, "object_pose_projection_failed", started_at)

    def _invalid_analysis_result(
        self, analysis: ObjectPoseAnalysis, started_at: float
    ) -> _FrameAnalysisResult:
        """Keep detector rejections distinct from calibration and scheduling failures."""

        return _FrameAnalysisResult(
            analysis.captured_at,
            False,
            analysis.reason,
            None,
            (self._monotonic_clock() - started_at) * 1000.0,
        )

    def _failed_result(
        self, frame: ImageFrame, reason: str, started_at: float
    ) -> _FrameAnalysisResult:
        """Build an empty safe result for geometry/configuration failures."""

        return _FrameAnalysisResult(
            frame.captured_at,
            False,
            reason,
            None,
            (self._monotonic_clock() - started_at) * 1000.0,
        )

    def _workpiece_pose_from_candidate(
        self,
        candidate: ObjectPoseCandidate,
        frame: ImageFrame,
        projector: PixelPlaneProjector,
        calibration: WorkcellCalibration,
    ) -> WorkpiecePose:
        """Project one accepted 2D measurement and its declared grasp origin through the board."""

        if not candidate.orientation_defined or candidate.yaw_rad is None:
            raise ValueError("candidate orientation is unavailable")
        projected_center = projector.project(PixelPoint(candidate.pixel_center.x, candidate.pixel_center.y))
        projected_axis = projector.project(self._axis_endpoint(candidate, frame))
        board_dx = projected_axis.board_point_mm.x_mm - projected_center.board_point_mm.x_mm
        board_dy = projected_axis.board_point_mm.y_mm - projected_center.board_point_mm.y_mm
        if math.hypot(board_dx, board_dy) <= 1e-6:
            raise ValueError("projected main axis has no in-plane length")
        board_yaw = math.atan2(board_dy, board_dx)
        board_axis_x = math.cos(board_yaw)
        board_axis_y = math.sin(board_yaw)
        self._validate_planar_dimensions(
            candidate,
            frame,
            projector,
            projected_center.board_point_mm,
            board_axis_x,
            board_axis_y,
        )
        orientation = _orientation_from_board_yaw(
            calibration.board_to_jaka_base.rotation_matrix, board_yaw
        )
        profile = self.settings.detector_settings.profile
        grasp_board_x = (
            projected_center.board_point_mm.x_mm
            + profile.grasp_origin_offset_x_mm * board_axis_x
            - profile.grasp_origin_offset_y_mm * board_axis_y
        )
        grasp_board_y = (
            projected_center.board_point_mm.y_mm
            + profile.grasp_origin_offset_x_mm * board_axis_y
            + profile.grasp_origin_offset_y_mm * board_axis_x
        )
        translation = calibration.board_to_jaka_base.apply(
            Point3D(
                grasp_board_x,
                grasp_board_y,
                self.settings.grasp_height_mm,
            )
        )
        return WorkpiecePose(
            candidate.profile_id,
            candidate.confidence,
            candidate.contour,
            candidate.bounding_box,
            candidate.pixel_center,
            candidate.normalized_center,
            "jaka_base",
            translation,
            orientation,
            ("x", "y", "yaw"),
            ("z", "roll", "pitch"),
            candidate.yaw_period_rad,
            candidate.orientation_defined,
            candidate.warning,
        )

    @staticmethod
    def _axis_endpoint(candidate: ObjectPoseCandidate, frame: ImageFrame) -> PixelPoint:
        """Choose an in-bounds pixel along the observed axial main axis."""

        if frame.width is None or frame.height is None or candidate.yaw_rad is None:
            raise ValueError("frame dimensions and yaw are required")
        box_width = candidate.bounding_box.width * float(frame.width)
        box_height = candidate.bounding_box.height * float(frame.height)
        distance = max(8.0, min(48.0, math.hypot(box_width, box_height) * 0.25))
        for sign in (1.0, -1.0):
            x = candidate.pixel_center.x + sign * math.cos(candidate.yaw_rad) * distance
            y = candidate.pixel_center.y + sign * math.sin(candidate.yaw_rad) * distance
            if 0.0 <= x < frame.width and 0.0 <= y < frame.height:
                return PixelPoint(x, y)
        raise ValueError("main axis leaves the calibrated image")

    def _validate_planar_dimensions(
        self,
        candidate: ObjectPoseCandidate,
        frame: ImageFrame,
        projector: PixelPlaneProjector,
        projected_center: Point3D,
        board_axis_x: float,
        board_axis_y: float,
    ) -> None:
        """Fail closed when calibrated contour dimensions contradict a measured flat profile.

        A single RGB camera cannot prove that a workpiece is flat. When the local
        profile provides nominal length and width, this check can nevertheless flag
        a calibrated shape inconsistency caused by tilt, occlusion, attachment, or
        a wrong object. The caller publishes no base-frame pose for that ambiguity.
        """

        profile = self.settings.detector_settings.profile
        if profile.nominal_length_mm is None or profile.nominal_width_mm is None:
            return
        if frame.width is None or frame.height is None:
            raise ValueError("frame dimensions are required for contour projection")
        normal_x = -board_axis_y
        normal_y = board_axis_x
        longitudinal = []
        lateral = []
        for point in candidate.contour:
            projected = projector.project(
                PixelPoint(point.x * float(frame.width), point.y * float(frame.height))
            ).board_point_mm
            delta_x = projected.x_mm - projected_center.x_mm
            delta_y = projected.y_mm - projected_center.y_mm
            longitudinal.append(delta_x * board_axis_x + delta_y * board_axis_y)
            lateral.append(delta_x * normal_x + delta_y * normal_y)
        if len(longitudinal) < 3:
            raise ValueError("candidate contour cannot establish planar dimensions")
        measured_length = max(longitudinal) - min(longitudinal)
        measured_width = max(lateral) - min(lateral)
        if measured_length <= 1e-6 or measured_width <= 1e-6:
            raise _PlanaritySuspectedError("degenerate projected dimensions")
        length_error = abs(measured_length - profile.nominal_length_mm) / profile.nominal_length_mm
        width_error = abs(measured_width - profile.nominal_width_mm) / profile.nominal_width_mm
        if max(length_error, width_error) > profile.maximum_planar_dimension_error_ratio:
            raise _PlanaritySuspectedError("projected dimensions do not match the known flat profile")

    def _publish_result(self, result: _FrameAnalysisResult) -> None:
        """Publish only a stable single result; rejected input clears old geometry."""

        if not result.valid or result.object_pose is None:
            self._stable_samples = []
            self._snapshot = ObjectPoseTrackingSnapshot(
                self.camera_id,
                self.settings.enabled,
                result.captured_at,
                False,
                result.reason,
                result.inference_latency_ms,
                (),
            )
            return
        if not self._is_consistent_with_recent_samples(result.object_pose):
            self._stable_samples = []
        self._stable_samples.append(result.object_pose)
        if len(self._stable_samples) > self.settings.stable_required_frames:
            self._stable_samples.pop(0)
        if len(self._stable_samples) < self.settings.stable_required_frames:
            self._snapshot = ObjectPoseTrackingSnapshot(
                self.camera_id,
                self.settings.enabled,
                result.captured_at,
                False,
                "object_pose_unstable",
                result.inference_latency_ms,
                (),
            )
            return
        self._snapshot = ObjectPoseTrackingSnapshot(
            self.camera_id,
            self.settings.enabled,
            result.captured_at,
            True,
            "object_pose_available",
            result.inference_latency_ms,
            (result.object_pose,),
        )

    def _is_consistent_with_recent_samples(self, candidate: WorkpiecePose) -> bool:
        """Require profile, center, and periodic yaw agreement before publication."""

        if not self._stable_samples:
            return True
        previous = self._stable_samples[-1]
        if previous.profile_id != candidate.profile_id:
            return False
        center_distance = math.hypot(
            previous.pixel_center.x - candidate.pixel_center.x,
            previous.pixel_center.y - candidate.pixel_center.y,
        )
        if center_distance > self.settings.maximum_center_jitter_px:
            return False
        period = candidate.yaw_period_rad or (2.0 * math.pi)
        yaw_delta = _periodic_angular_difference(
            previous.orientation_rpy_rad.yaw_rad,
            candidate.orientation_rpy_rad.yaw_rad,
            period,
        )
        return yaw_delta <= self.settings.maximum_yaw_jitter_rad

    def _lock_for_current_loop(self) -> Any:
        """Create state synchronization only after an asyncio loop exists."""

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _reset_lock_for_current_loop(self) -> Any:
        """Serialize reset transitions without binding a loop at application construction."""

        if self._reset_lock is None:
            self._reset_lock = asyncio.Lock()
        return self._reset_lock


def _orientation_from_board_yaw(rotation_matrix, board_yaw: float) -> OrientationRpy:
    """Compose board-frame axial yaw with the taught board-to-base orientation."""

    cosine = math.cos(board_yaw)
    sine = math.sin(board_yaw)
    local = ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))
    rotation = _matrix_multiply(rotation_matrix, local)
    pitch = math.asin(max(-1.0, min(1.0, -rotation[2][0])))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2][1], rotation[2][2])
        yaw = math.atan2(rotation[1][0], rotation[0][0])
    else:
        roll = 0.0
        yaw = math.atan2(-rotation[0][1], rotation[1][1])
    return OrientationRpy(roll, pitch, yaw)


def _matrix_multiply(left, right):
    """Multiply two 3x3 rotations without introducing another public dependency."""

    return tuple(
        tuple(
            sum(float(left[row][index]) * float(right[index][column]) for index in range(3))
            for column in range(3)
        )
        for row in range(3)
    )


def _periodic_angular_difference(first: float, second: float, period: float) -> float:
    """Return shortest difference for directional or axial circular values."""

    if period <= 0.0 or not math.isfinite(period):
        return math.inf
    return abs((first - second + period / 2.0) % period - period / 2.0)
