"""Known-workpiece foreground detector built on Pillow, NumPy, and OpenCV C kernels."""

import math
from typing import List, Optional, Tuple

import cv2
import numpy
from PIL import Image

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.object_pose.models import (
    KnownWorkpieceProfile,
    NormalizedPoint2D,
    NormalizedRect,
    ObjectPoseAnalysis,
    ObjectPoseCandidate,
    ObjectPoseSettings,
    PixelPoint2D,
)


class _FrameDataError(ValueError):
    """Map input frame layout issues to stable analysis reason codes."""


class ForegroundObjectPoseEstimator:
    """Produce a conservative known-workpiece two-dimensional pose by background subtraction.

    The detector uses only in-memory ``ImageFrame`` values. It does not open a
    camera, persist images, or import robot or gripper interfaces. The default
    orientation is the contour major axis with ``pi`` periodicity. A directed
    orientation is emitted only when the profile declares ``larger_end`` and
    its end evidence meets the threshold.
    """

    def __init__(
        self,
        settings: ObjectPoseSettings,
        background_frame: Optional[ImageFrame],
    ) -> None:
        """Bind explicit settings and an empty-table background frame from the same camera."""

        self.settings = settings
        self.background_frame = background_frame

    def analyze(self, frame: ImageFrame) -> ObjectPoseAnalysis:
        """Analyze one frame and return an invalid snapshot for every uncertain outcome."""

        try:
            luminance = self._luminance(frame)
        except _FrameDataError as error:
            return self._invalid(frame, str(error))

        background = self.background_frame
        if background is None:
            return self._invalid(frame, "background_unavailable")
        if background.camera_id != frame.camera_id:
            return self._invalid(frame, "background_camera_mismatch")
        try:
            background_luminance = self._luminance(background)
        except _FrameDataError as error:
            return self._invalid(frame, "background_{0}".format(error))
        if luminance.shape != background_luminance.shape:
            return self._invalid(frame, "background_dimensions_mismatch")

        active_region = self._active_region(luminance.shape[1], luminance.shape[0])
        active_pixels = int(numpy.count_nonzero(active_region))
        if active_pixels == 0:
            return self._invalid(frame, "no_foreground", foreground_ratio=0.0)

        difference = numpy.abs(luminance.astype(numpy.int16) - background_luminance.astype(numpy.int16))
        foreground = difference >= self.settings.difference_threshold
        foreground &= active_region
        foreground = self._morphology(foreground)
        foreground &= active_region
        foreground_count = int(numpy.count_nonzero(foreground))
        foreground_ratio = float(foreground_count) / float(active_pixels)
        if foreground_count == 0:
            return self._invalid(frame, "no_foreground", foreground_ratio=foreground_ratio)
        if foreground_ratio > self.settings.maximum_foreground_ratio:
            return self._invalid(frame, "foreground_area_excessive", foreground_ratio=foreground_ratio)

        candidates = self._candidates(foreground, luminance.shape[1], luminance.shape[0])
        if not candidates:
            return self._invalid(frame, "candidate_shape_mismatch", foreground_ratio=foreground_ratio)
        if len(candidates) > 1:
            return ObjectPoseAnalysis(
                frame.camera_id,
                frame.captured_at,
                False,
                "multiple_candidates",
                candidates,
                foreground_ratio,
                frame.width,
                frame.height,
            )

        candidate = candidates[0]
        if not candidate.orientation_defined:
            return ObjectPoseAnalysis(
                frame.camera_id,
                frame.captured_at,
                False,
                "orientation_undefined",
                (candidate,),
                foreground_ratio,
                frame.width,
                frame.height,
            )
        if self.settings.profile.require_directional_yaw and candidate.yaw_period_rad != 2.0 * math.pi:
            return ObjectPoseAnalysis(
                frame.camera_id,
                frame.captured_at,
                False,
                "directional_yaw_ambiguous",
                (candidate,),
                foreground_ratio,
                frame.width,
                frame.height,
            )
        return ObjectPoseAnalysis(
            frame.camera_id,
            frame.captured_at,
            True,
            "object_pose_available",
            (candidate,),
            foreground_ratio,
            frame.width,
            frame.height,
        )

    def _candidates(
        self, foreground: numpy.ndarray, width: int, height: int
    ) -> Tuple[ObjectPoseCandidate, ...]:
        """Extract connected components in OpenCV C kernels and keep only area-eligible labels."""

        label_count, labels, statistics, centroids = cv2.connectedComponentsWithStats(
            foreground.astype(numpy.uint8),
            connectivity=8,
            ltype=cv2.CV_32S,
        )
        if label_count <= 1:
            return ()
        profile = self.settings.profile
        component_statistics = statistics[1:]
        areas = component_statistics[:, cv2.CC_STAT_AREA]
        eligible = areas >= profile.minimum_area_px
        if profile.maximum_area_px is not None:
            eligible &= areas <= profile.maximum_area_px
        label_ids = numpy.flatnonzero(eligible) + 1
        candidates = []  # type: List[ObjectPoseCandidate]
        for label_id in label_ids.tolist():
            candidate = self._candidate(
                labels,
                int(label_id),
                statistics[int(label_id)],
                centroids[int(label_id)],
                width,
                height,
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def _candidate(
        self,
        labels: numpy.ndarray,
        label_id: int,
        statistic: numpy.ndarray,
        centroid: numpy.ndarray,
        width: int,
        height: int,
    ) -> Optional[ObjectPoseCandidate]:
        """Calculate contour, shape statistics, and axial orientation from one C-labeled component."""

        left = int(statistic[cv2.CC_STAT_LEFT])
        top = int(statistic[cv2.CC_STAT_TOP])
        box_width = int(statistic[cv2.CC_STAT_WIDTH])
        box_height = int(statistic[cv2.CC_STAT_HEIGHT])
        area = int(statistic[cv2.CC_STAT_AREA])
        if box_width <= 0 or box_height <= 0:
            return None
        aspect_ratio = float(max(box_width, box_height)) / float(min(box_width, box_height))
        fill_ratio = float(area) / float(box_width * box_height)
        component_mask = numpy.where(
            labels[top : top + box_height, left : left + box_width] == label_id,
            255,
            0,
        ).astype(numpy.uint8)
        moments = cv2.moments(component_mask, binaryImage=True)
        contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(contour)
        if hull is None or len(hull) < 3:
            return None
        hull_area = float(cv2.contourArea(hull))
        solidity = min(1.0, float(area) / max(1.0, hull_area))
        profile = self.settings.profile
        if not self._matches_profile(profile, aspect_ratio, fill_ratio, solidity):
            return None

        pixel_center = PixelPoint2D(float(centroid[0]), float(centroid[1]))
        normalized_center = NormalizedPoint2D(
            pixel_center.x / float(width), pixel_center.y / float(height)
        )
        (
            orientation_defined,
            yaw_rad,
            yaw_period_rad,
            warning,
            eccentricity,
        ) = self._orientation(moments, component_mask)
        contour = self._normalize_contour(hull, left, top, width, height)
        confidence = self._confidence(area, profile.minimum_area_px, eccentricity)
        return ObjectPoseCandidate(
            profile.profile_id,
            contour,
            pixel_center,
            normalized_center,
            BoundingBox2D(
                float(left) / float(width),
                float(top) / float(height),
                float(box_width) / float(width),
                float(box_height) / float(height),
            ),
            confidence,
            orientation_defined,
            yaw_rad,
            yaw_period_rad,
            warning,
            area,
            fill_ratio,
            solidity,
        )

    @staticmethod
    def _matches_profile(
        profile: KnownWorkpieceProfile,
        aspect_ratio: float,
        fill_ratio: float,
        solidity: float,
    ) -> bool:
        """Determine whether one component satisfies a known-workpiece profile's contour constraints."""

        if aspect_ratio < profile.minimum_aspect_ratio:
            return False
        if profile.maximum_aspect_ratio is not None and aspect_ratio > profile.maximum_aspect_ratio:
            return False
        if fill_ratio < profile.minimum_fill_ratio or fill_ratio > profile.maximum_fill_ratio:
            return False
        return solidity >= profile.minimum_solidity

    @staticmethod
    def _confidence(area: int, minimum_area: int, eccentricity: float) -> float:
        """Produce an interpretable segmentation-evidence score, not a semantic-model probability."""

        area_evidence = min(1.0, float(area) / float(max(1, minimum_area * 4)))
        orientation_evidence = max(0.0, min(1.0, eccentricity))
        return min(1.0, 0.5 + 0.3 * area_evidence + 0.2 * orientation_evidence)

    def _active_region(self, width: int, height: int) -> numpy.ndarray:
        """Map normalized work area and static fixture exclusions to source-image pixels."""

        active = numpy.zeros((height, width), dtype=bool)
        roi = self.settings.roi
        if roi is None:
            active[:, :] = True
        else:
            left, top, right, bottom = self._rect_bounds(roi, width, height)
            active[top:bottom, left:right] = True
        for excluded_region in self.settings.excluded_regions:
            left, top, right, bottom = self._rect_bounds(excluded_region, width, height)
            active[top:bottom, left:right] = False
        return active

    @staticmethod
    def _rect_bounds(rect: NormalizedRect, width: int, height: int) -> Tuple[int, int, int, int]:
        """Convert to a right-bottom-open range so edge coordinates stay within the image."""

        left = max(0, min(width, int(math.floor(rect.x * width))))
        top = max(0, min(height, int(math.floor(rect.y * height))))
        right = max(left, min(width, int(math.ceil((rect.x + rect.width) * width))))
        bottom = max(top, min(height, int(math.ceil((rect.y + rect.height) * height))))
        return left, top, right, bottom

    def _morphology(self, foreground: numpy.ndarray) -> numpy.ndarray:
        """Run opening and closing in OpenCV C kernels instead of Python pixel loops."""

        if self.settings.morphology_kernel_size == 1:
            return foreground
        kernel = numpy.ones(
            (self.settings.morphology_kernel_size, self.settings.morphology_kernel_size),
            dtype=numpy.uint8,
        )
        result = foreground.astype(numpy.uint8)
        if self.settings.opening_iterations:
            result = cv2.morphologyEx(
                result,
                cv2.MORPH_OPEN,
                kernel,
                iterations=self.settings.opening_iterations,
                borderType=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
        if self.settings.closing_iterations:
            result = cv2.morphologyEx(
                result,
                cv2.MORPH_CLOSE,
                kernel,
                iterations=self.settings.closing_iterations,
                borderType=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
        return result.astype(bool)

    def _normalize_contour(
        self,
        hull: numpy.ndarray,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> Tuple[NormalizedPoint2D, ...]:
        """Publish a bounded number of OpenCV hull vertices to limit high-resolution API payloads."""

        points = hull.reshape((-1, 2))
        if len(points) > self.settings.maximum_contour_points:
            indexes = (
                numpy.arange(self.settings.maximum_contour_points, dtype=numpy.int32)
                * len(points)
                // self.settings.maximum_contour_points
            )
            points = points[indexes]
        return tuple(
            NormalizedPoint2D(
                (float(point[0]) + float(left)) / float(width),
                (float(point[1]) + float(top)) / float(height),
            )
            for point in points
        )

    def _orientation(
        self, moments: dict, component_mask: numpy.ndarray
    ) -> Tuple[bool, Optional[float], Optional[float], Optional[str], float]:
        """Calculate the major axis and distinguish its ends only with decisive declared evidence."""

        area = float(moments.get("m00", 0.0))
        if area < 3.0:
            return False, None, None, "orientation_undefined", 0.0
        covariance = numpy.asarray(
            (
                (float(moments["mu20"]) / area, float(moments["mu11"]) / area),
                (float(moments["mu11"]) / area, float(moments["mu02"]) / area),
            ),
            dtype=numpy.float64,
        )
        eigenvalues, eigenvectors = numpy.linalg.eigh(covariance)
        major_index = int(numpy.argmax(eigenvalues))
        major_value = float(eigenvalues[major_index])
        minor_value = float(eigenvalues[1 - major_index])
        total = major_value + minor_value
        if not math.isfinite(total) or total <= 0.0:
            return False, None, None, "orientation_undefined", 0.0
        eccentricity = max(0.0, min(1.0, (major_value - minor_value) / total))
        if eccentricity < self.settings.minimum_orientation_eccentricity:
            return False, None, None, "orientation_undefined", eccentricity
        axis = eigenvectors[:, major_index]
        yaw = math.atan2(float(axis[1]), float(axis[0]))
        while yaw < -math.pi / 2.0:
            yaw += math.pi
        while yaw >= math.pi / 2.0:
            yaw -= math.pi
        profile = self.settings.profile
        if profile.directional_feature == "larger_end":
            directional_yaw = self._larger_end_directional_yaw(component_mask, moments, yaw)
            if directional_yaw is not None:
                return True, directional_yaw, 2.0 * math.pi, None, eccentricity
        return True, yaw, math.pi, "orientation_pi_ambiguous", eccentricity

    def _larger_end_directional_yaw(
        self, component_mask: numpy.ndarray, moments: dict, axial_yaw: float
    ) -> Optional[float]:
        """Return a directed axis only when a profile's larger-end cue is decisive.

        The rule uses occupancy in the outer 20 percent of both main-axis ends.
        It is intentionally profile-gated: arbitrary silhouettes must not gain a
        synthetic head/tail direction merely because one noisy end contains a few
        more pixels.
        """

        rows, columns = numpy.nonzero(component_mask)
        if len(rows) < 3:
            return None
        center_x = float(moments["m10"]) / float(moments["m00"])
        center_y = float(moments["m01"]) / float(moments["m00"])
        projection = (
            (columns.astype(numpy.float64) - center_x) * math.cos(axial_yaw)
            + (rows.astype(numpy.float64) - center_y) * math.sin(axial_yaw)
        )
        lower = float(numpy.min(projection))
        upper = float(numpy.max(projection))
        span = upper - lower
        if not math.isfinite(span) or span <= 1.0:
            return None
        end_band = span * 0.20
        negative_count = int(numpy.count_nonzero(projection <= lower + end_band))
        positive_count = int(numpy.count_nonzero(projection >= upper - end_band))
        larger_count = max(negative_count, positive_count)
        if larger_count <= 0:
            return None
        asymmetry = abs(float(positive_count - negative_count)) / float(larger_count)
        if asymmetry < self.settings.profile.minimum_directional_asymmetry:
            return None
        if positive_count > negative_count:
            return axial_yaw
        return _wrap_angle(axial_yaw + math.pi)

    @staticmethod
    def _luminance(frame: ImageFrame) -> numpy.ndarray:
        """Validate RGB8 or Mono8 byte layout and convert it to a luminance matrix with Pillow."""

        if not frame.healthy or frame.pixel_payload is None:
            raise _FrameDataError("frame_unavailable")
        if frame.width is None or frame.height is None or frame.width <= 0 or frame.height <= 0:
            raise _FrameDataError("frame_dimensions_invalid")
        pixel_count = frame.width * frame.height
        if frame.pixel_format == "mono8":
            if len(frame.pixel_payload) != pixel_count:
                raise _FrameDataError("mono_payload_size_invalid")
            image = Image.frombytes("L", (frame.width, frame.height), frame.pixel_payload)
        elif frame.pixel_format == "rgb8":
            if len(frame.pixel_payload) != pixel_count * 3:
                raise _FrameDataError("rgb_payload_size_invalid")
            image = Image.frombytes("RGB", (frame.width, frame.height), frame.pixel_payload).convert("L")
        else:
            raise _FrameDataError("pixel_format_unsupported")
        return numpy.asarray(image, dtype=numpy.uint8)

    @staticmethod
    def _invalid(
        frame: ImageFrame,
        reason: str,
        foreground_ratio: Optional[float] = None,
    ) -> ObjectPoseAnalysis:
        """Build one consistent conservative failure result without candidates."""

        return ObjectPoseAnalysis(
            frame.camera_id,
            frame.captured_at,
            False,
            reason,
            (),
            foreground_ratio,
            frame.width,
            frame.height,
        )


def _wrap_angle(value: float) -> float:
    """Normalize one directed angle to the conventional closed-open pi interval."""

    return (value + math.pi) % (2.0 * math.pi) - math.pi
