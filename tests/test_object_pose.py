"""Pure in-memory tests for known-workpiece foreground pose analysis."""

import math
import time
import unittest

from PIL import Image, ImageDraw

from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.object_pose import (
    ForegroundObjectPoseEstimator,
    KnownWorkpieceProfile,
    NormalizedRect,
    OBJECT_POSE_REASON_CODES,
    ObjectPoseSettings,
)


class ObjectPoseReasonCodeContractTests(unittest.TestCase):
    """Keep passive-plugin and tracking failures in the public reason-code contract."""

    def test_plugin_and_tracker_failures_are_stable_reason_codes(self):
        """Browser state mapping must not receive an undocumented internal failure code."""

        emitted_reasons = {
            "object_pose_disabled",
            "object_pose_waiting_for_frame",
            "object_pose_unstable",
            "object_pose_analysis_failed",
            "object_pose_projection_failed",
            "background_reference_unavailable",
            "calibration_unavailable",
            "calibration_camera_mismatch",
            "calibration_id_mismatch",
            "calibration_reprojection_error_excessive",
            "calibration_invalid",
        }

        self.assertTrue(emitted_reasons.issubset(set(OBJECT_POSE_REASON_CODES)))


class ForegroundObjectPoseEstimatorTests(unittest.TestCase):
    """Verify conservative 2D results without an SDK, camera, or persisted frame."""

    def test_detects_equivalent_rgb8_and_mono8_rotated_workpiece(self):
        """Use Pillow conversion paths for both normalized camera formats."""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (60.0, 48.0), 58.0, 12.0, 30.0)
        for pixel_format in ("mono8", "rgb8"):
            with self.subTest(pixel_format=pixel_format):
                result = self._estimator(
                    self._frame(background, pixel_format), self._settings()
                ).analyze(self._frame(current, pixel_format, captured_at=2.0))

                self.assertTrue(result.valid)
                self.assertEqual("object_pose_available", result.reason)
                self.assertEqual(1, len(result.candidates))
                candidate = result.candidates[0]
                self.assertTrue(candidate.orientation_defined)
                self.assertEqual("orientation_pi_ambiguous", candidate.warning)
                self.assertAlmostEqual(math.pi, candidate.yaw_period_rad)
                self.assertAlmostEqual(60.0, candidate.pixel_center.x, delta=1.0)
                self.assertAlmostEqual(48.0, candidate.pixel_center.y, delta=1.0)
                self.assertLess(self._axial_error(candidate.yaw_rad, math.radians(30.0)), math.radians(8.0))
                self.assertGreaterEqual(len(candidate.contour), 3)
                self.assertTrue(all(0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0 for point in candidate.contour))

    def test_rejects_an_empty_table_without_retaining_a_stale_candidate(self):
        """An unchanged background is a safe no-foreground result."""

        background = self._image()
        result = self._estimator(self._frame(background), self._settings()).analyze(
            self._frame(background, captured_at=2.0)
        )

        self.assertFalse(result.valid)
        self.assertEqual("no_foreground", result.reason)
        self.assertEqual((), result.candidates)
        self.assertEqual(0.0, result.foreground_ratio)

    def test_roi_and_static_exclusion_leave_only_the_allowed_workpiece(self):
        """Fixed fixtures and out-of-workspace objects never reach connected components."""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (25.0, 45.0), 30.0, 8.0, 0.0)
        self._rotated_rectangle(current, (94.0, 45.0), 30.0, 8.0, 0.0)

        right_only = self._settings(roi=NormalizedRect(0.50, 0.0, 0.50, 1.0))
        right_result = self._estimator(self._frame(background), right_only).analyze(self._frame(current))
        self.assertTrue(right_result.valid)
        self.assertGreater(right_result.candidates[0].normalized_center.x, 0.5)

        left_only = self._settings(excluded_regions=(NormalizedRect(0.62, 0.0, 0.38, 1.0),))
        left_result = self._estimator(self._frame(background), left_only).analyze(self._frame(current))
        self.assertTrue(left_result.valid)
        self.assertLess(left_result.candidates[0].normalized_center.x, 0.5)

    def test_opening_removes_an_isolated_pixel_before_single_candidate_validation(self):
        """A bright one-pixel reflection must not produce a second accepted object."""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (62.0, 45.0), 44.0, 10.0, 0.0)
        current.putpixel((8, 8), 255)
        profile = self._profile(minimum_area_px=1, minimum_aspect_ratio=1.0)
        settings = self._settings(profile=profile, opening_iterations=1, closing_iterations=0)

        result = self._estimator(self._frame(background), settings).analyze(self._frame(current))

        self.assertTrue(result.valid)
        self.assertEqual(1, len(result.candidates))

    def test_rejects_multiple_qualified_components_as_ambiguous(self):
        """Two valid workpieces must not be collapsed to an arbitrary highest score."""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (30.0, 45.0), 34.0, 8.0, 0.0)
        self._rotated_rectangle(current, (92.0, 45.0), 34.0, 8.0, 0.0)

        result = self._estimator(self._frame(background), self._settings()).analyze(self._frame(current))

        self.assertFalse(result.valid)
        self.assertEqual("multiple_candidates", result.reason)
        self.assertEqual(2, len(result.candidates))

    def test_rejects_a_component_that_does_not_match_the_workpiece_profile(self):
        """A square foreground region fails the configured elongated-workpiece constraint."""

        background = self._image()
        current = self._image()
        ImageDraw.Draw(current).rectangle((45, 35, 75, 65), fill=255)
        profile = self._profile(minimum_aspect_ratio=3.0)

        result = self._estimator(self._frame(background), self._settings(profile=profile)).analyze(
            self._frame(current)
        )

        self.assertFalse(result.valid)
        self.assertEqual("candidate_shape_mismatch", result.reason)

    def test_returns_the_candidate_but_rejects_a_near_circular_orientation(self):
        """A center can exist while its main axis is too uncertain to be a pose."""

        background = self._image()
        current = self._image()
        ImageDraw.Draw(current).ellipse((45, 33, 75, 63), fill=255)
        profile = self._profile(minimum_aspect_ratio=1.0)

        result = self._estimator(self._frame(background), self._settings(profile=profile)).analyze(
            self._frame(current)
        )

        self.assertFalse(result.valid)
        self.assertEqual("orientation_undefined", result.reason)
        self.assertEqual(1, len(result.candidates))
        self.assertFalse(result.candidates[0].orientation_defined)
        self.assertIsNone(result.candidates[0].yaw_rad)
        self.assertEqual("orientation_undefined", result.candidates[0].warning)

    def test_rejects_directional_pose_when_only_an_axial_yaw_is_observable(self):
        """未声明头尾特征时，即使工件细长也只能得到轴向偏航。"""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (60.0, 48.0), 50.0, 10.0, 0.0)
        profile = self._profile(require_directional_yaw=True)

        result = self._estimator(self._frame(background), self._settings(profile=profile)).analyze(
            self._frame(current)
        )

        self.assertFalse(result.valid)
        self.assertEqual("directional_yaw_ambiguous", result.reason)
        self.assertEqual(math.pi, result.candidates[0].yaw_period_rad)

    def test_uses_a_declared_larger_end_feature_for_directional_yaw(self):
        """端部面积差足够明显时，受限档案可以将主轴升级为完整偏航。"""

        background = self._image()
        current = self._image()
        drawing = ImageDraw.Draw(current)
        drawing.rectangle((25, 44, 76, 52), fill=255)
        drawing.rectangle((72, 38, 92, 58), fill=255)
        profile = self._profile(
            require_directional_yaw=True,
            directional_feature="larger_end",
            minimum_directional_asymmetry=0.15,
        )

        result = self._estimator(self._frame(background), self._settings(profile=profile)).analyze(
            self._frame(current)
        )

        self.assertTrue(result.valid)
        candidate = result.candidates[0]
        self.assertTrue(candidate.orientation_defined)
        self.assertIsNone(candidate.warning)
        self.assertAlmostEqual(2.0 * math.pi, candidate.yaw_period_rad)
        self.assertLess(abs(candidate.yaw_rad), math.radians(10.0))

    def test_rejects_camera_switch_and_malformed_rgb_payload_with_explicit_reason_codes(self):
        """Background identity and byte layout are both fail-closed boundaries."""

        background = self._frame(self._image(), camera_id="camera-b")
        current = self._frame(self._image(), camera_id="camera-a")
        switched = self._estimator(background, self._settings()).analyze(current)
        self.assertEqual("background_camera_mismatch", switched.reason)

        malformed = ImageFrame(
            "camera-a",
            2.0,
            None,
            None,
            True,
            b"not-rgb8",
            120,
            96,
            "rgb8",
        )
        invalid = self._estimator(self._frame(self._image()), self._settings()).analyze(malformed)
        self.assertFalse(invalid.valid)
        self.assertEqual("rgb_payload_size_invalid", invalid.reason)

    def test_bounds_the_published_contour_without_changing_the_candidate_center(self):
        """High-resolution boundary points cannot make a browser payload unbounded."""

        background = self._image()
        current = self._image()
        self._rotated_rectangle(current, (60.0, 48.0), 52.0, 12.0, 20.0)
        result = self._estimator(
            self._frame(background), self._settings(maximum_contour_points=3)
        ).analyze(self._frame(current))

        self.assertTrue(result.valid)
        self.assertEqual(3, len(result.candidates[0].contour))
        self.assertAlmostEqual(60.0, result.candidates[0].pixel_center.x, delta=1.0)

    def test_processes_a_5mp_workpiece_frame_with_the_native_component_path(self):
        """Exercise the 2448x2048 camera scale without a Python per-pixel component walk."""

        background = Image.new("L", (2448, 2048), 0)
        current = background.copy()
        self._rotated_rectangle(current, (1224.0, 1024.0), 900.0, 160.0, 18.0)
        profile = self._profile(minimum_area_px=1000)
        settings = self._settings(profile=profile, maximum_foreground_ratio=0.20)

        started_at = time.perf_counter()
        result = self._estimator(self._frame(background), settings).analyze(self._frame(current))
        elapsed_seconds = time.perf_counter() - started_at

        self.assertTrue(result.valid)
        self.assertEqual("object_pose_available", result.reason)
        self.assertLess(
            elapsed_seconds,
            5.0,
            "5MP foreground analysis exceeded the bounded native-path regression limit.",
        )

    def test_validates_normalized_geometry_and_detection_settings(self):
        """Invalid config is rejected before a passive worker sees any camera frame."""

        with self.assertRaisesRegex(ValueError, "image bounds"):
            NormalizedRect(0.9, 0.0, 0.2, 0.5)
        with self.assertRaisesRegex(ValueError, "difference_threshold"):
            ObjectPoseSettings(difference_threshold=0)
        with self.assertRaisesRegex(ValueError, "excluded_regions"):
            ObjectPoseSettings(excluded_regions=[NormalizedRect(0.0, 0.0, 1.0, 1.0)])
        with self.assertRaisesRegex(ValueError, "directional_feature"):
            KnownWorkpieceProfile(directional_feature="unverified")
        with self.assertRaisesRegex(ValueError, "grasp origin offset"):
            KnownWorkpieceProfile(grasp_origin_offset_x_mm=1.0)

    @staticmethod
    def _image():
        """Return one deterministic empty dark worktable image."""

        return Image.new("L", (120, 96), 0)

    @staticmethod
    def _frame(image, pixel_format="mono8", camera_id="camera-a", captured_at=1.0):
        """Build one normalized in-memory ImageFrame in either supported format."""

        if pixel_format == "mono8":
            converted = image.convert("L")
        elif pixel_format == "rgb8":
            converted = image.convert("RGB")
        else:
            raise AssertionError("Unsupported test pixel format.")
        return ImageFrame(
            camera_id,
            captured_at,
            "memory://frame",
            None,
            True,
            converted.tobytes(),
            converted.width,
            converted.height,
            pixel_format,
        )

    @staticmethod
    def _profile(**overrides):
        """Create a permissive but elongated v1 fixture profile."""

        values = {
            "minimum_area_px": 40,
            "minimum_aspect_ratio": 1.2,
            "maximum_aspect_ratio": 20.0,
            "minimum_fill_ratio": 0.02,
            "minimum_solidity": 0.0,
        }
        values.update(overrides)
        return KnownWorkpieceProfile(**values)

    def _settings(self, **overrides):
        """Keep fixture thresholds explicit while allowing each test to vary one policy."""

        values = {
            "profile": self._profile(),
            "difference_threshold": 20,
            "morphology_kernel_size": 3,
            "opening_iterations": 0,
            "closing_iterations": 0,
            "maximum_foreground_ratio": 0.80,
            "minimum_orientation_eccentricity": 0.15,
        }
        values.update(overrides)
        return ObjectPoseSettings(**values)

    @staticmethod
    def _rotated_rectangle(image, center, length, thickness, angle_degrees):
        """Draw one bright elongated planar workpiece with a controlled principal axis."""

        angle = math.radians(angle_degrees)
        direction_x = math.cos(angle) * length * 0.5
        direction_y = math.sin(angle) * length * 0.5
        normal_x = -math.sin(angle) * thickness * 0.5
        normal_y = math.cos(angle) * thickness * 0.5
        points = (
            (center[0] - direction_x - normal_x, center[1] - direction_y - normal_y),
            (center[0] + direction_x - normal_x, center[1] + direction_y - normal_y),
            (center[0] + direction_x + normal_x, center[1] + direction_y + normal_y),
            (center[0] - direction_x + normal_x, center[1] - direction_y + normal_y),
        )
        ImageDraw.Draw(image).polygon(points, fill=255)

    @staticmethod
    def _estimator(background_frame, settings):
        """Construct the core without any device adapter or persistence dependency."""

        return ForegroundObjectPoseEstimator(settings, background_frame)

    @staticmethod
    def _axial_error(actual, expected):
        """Calculate the smallest angular error for a pi-periodic axis."""

        difference = (actual - expected + math.pi * 0.5) % math.pi - math.pi * 0.5
        return abs(difference)


if __name__ == "__main__":
    unittest.main()
