"""Pure offline tests for ChArUco contracts, rigid fitting, and plane projection."""

import json
import math
import unittest
from unittest.mock import patch

from gripper_ai_controller.calibration import (
    DEFAULT_CHARUCO_BOARD,
    MINIMUM_CHARUCO_VIEWS,
    CalibrationValidationError,
    CameraCalibrationInput,
    CameraCalibrationResult,
    CameraIntrinsics,
    CharucoBoardSpec,
    CharucoObservation,
    OpenCvUnavailableError,
    PixelPlaneProjector,
    PixelPoint,
    Point3D,
    ProjectionError,
    RigidTransform,
    RigidTransformFitResult,
    WorkcellCalibration,
    fit_board_to_jaka_base,
    detect_charuco_observation,
    generate_charuco_board_image,
)


def _opencv_charuco_is_available():
    """Keep the complete offline smoke test optional until Poetry installs OpenCV."""

    try:
        from gripper_ai_controller.calibration.opencv import require_opencv_aruco

        require_opencv_aruco()
    except OpenCvUnavailableError:
        return False
    return True


class CharucoBoardTests(unittest.TestCase):
    """Verify fixed board dimensions and optional OpenCV behavior without hardware."""

    def test_default_board_matches_the_specified_printable_board(self):
        """Keep the documented 7 x 5 DICT_5X5_100 board stable."""

        self.assertEqual("DICT_5X5_100", DEFAULT_CHARUCO_BOARD.dictionary_name)
        self.assertEqual(7, DEFAULT_CHARUCO_BOARD.squares_x)
        self.assertEqual(5, DEFAULT_CHARUCO_BOARD.squares_y)
        self.assertEqual(25.0, DEFAULT_CHARUCO_BOARD.square_length_mm)
        self.assertEqual(18.0, DEFAULT_CHARUCO_BOARD.marker_length_mm)
        self.assertEqual(DEFAULT_CHARUCO_BOARD, CharucoBoardSpec.from_dict(DEFAULT_CHARUCO_BOARD.to_dict()))

    def test_marker_must_fit_inside_each_square(self):
        """Reject physically impossible marker geometry before rendering."""

        with self.assertRaises(CalibrationValidationError):
            CharucoBoardSpec(marker_length_mm=25.0)

    def test_generation_reports_a_clear_error_when_opencv_is_not_installed(self):
        """Optional calibration support must not turn into an import-time failure."""

        with patch(
            "gripper_ai_controller.calibration.opencv.importlib.import_module",
            side_effect=ImportError("cv2 missing"),
        ):
            with self.assertRaises(OpenCvUnavailableError):
                generate_charuco_board_image(DEFAULT_CHARUCO_BOARD, 1400, 1000)

    def test_detection_reports_a_clear_error_when_opencv_is_not_installed(self):
        """A capture CLI receives an actionable dependency error rather than a crash."""

        import numpy

        with patch(
            "gripper_ai_controller.calibration.opencv.importlib.import_module",
            side_effect=ImportError("cv2 missing"),
        ):
            with self.assertRaises(OpenCvUnavailableError):
                detect_charuco_observation(numpy.zeros((32, 32), dtype=numpy.uint8), DEFAULT_CHARUCO_BOARD)


class CameraCalibrationContractTests(unittest.TestCase):
    """Validate offline ChArUco inputs and JSON-safe intrinsic result records."""

    @staticmethod
    def _observation(offset):
        return CharucoObservation(
            corners=(
                PixelPoint(100.0 + offset, 100.0),
                PixelPoint(200.0 + offset, 100.0),
                PixelPoint(100.0 + offset, 200.0),
                PixelPoint(200.0 + offset, 200.0),
            ),
            ids=(0, 1, 2, 3),
        )

    def test_input_requires_the_documented_minimum_number_of_views(self):
        """A handful of attractive frames cannot pass as a site calibration."""

        observations = tuple(self._observation(index) for index in range(MINIMUM_CHARUCO_VIEWS - 1))
        with self.assertRaises(CalibrationValidationError):
            CameraCalibrationInput(
                "camera-calibration-a",
                "camera-a",
                DEFAULT_CHARUCO_BOARD,
                2448,
                2048,
                observations,
            )

    def test_result_round_trips_through_standard_json(self):
        """Local calibration persistence contains only built-in JSON values."""

        result = CameraCalibrationResult(
            "camera-calibration-a",
            "camera-a",
            DEFAULT_CHARUCO_BOARD,
            CameraIntrinsics(2448, 2048, 1800.0, 1795.0, 1224.0, 1024.0, (0.1, -0.02, 0.0, 0.0, 0.0)),
            0.32,
            MINIMUM_CHARUCO_VIEWS,
        )
        encoded = json.dumps(result.to_dict())
        decoded = CameraCalibrationResult.from_dict(json.loads(encoded))
        self.assertEqual(result, decoded)
        self.assertIn('"schema_version": 1', result.to_json())


@unittest.skipUnless(_opencv_charuco_is_available(), "opencv-contrib-python-headless is not installed")
class OpenCvCharucoSmokeTests(unittest.TestCase):
    """Exercise the pinned OpenCV path with generated pixels only, never a camera."""

    def test_generated_board_detects_and_calibrates_from_varied_memory_views(self):
        """Prove board rendering, corner extraction, and calibration work together offline."""

        import cv2
        import numpy

        board_image = generate_charuco_board_image(DEFAULT_CHARUCO_BOARD, 1400, 1000, margin_px=40)
        source = numpy.asarray(
            [[0.0, 0.0], [1399.0, 0.0], [1399.0, 999.0], [0.0, 999.0]],
            dtype=numpy.float32,
        )
        observations = []
        for index in range(MINIMUM_CHARUCO_VIEWS):
            horizontal = float((index % 5) * 12 - 24)
            vertical = float((index // 5) * 10 - 20)
            tilt_x = 18.0 * math.sin(index * 0.7)
            tilt_y = 16.0 * math.cos(index * 0.5)
            destination = numpy.asarray(
                [
                    [220.0 + horizontal + tilt_x, 160.0 + vertical],
                    [1220.0 + horizontal, 145.0 + vertical + tilt_y],
                    [1200.0 + horizontal - tilt_x, 860.0 + vertical],
                    [205.0 + horizontal, 875.0 + vertical - tilt_y],
                ],
                dtype=numpy.float32,
            )
            image = cv2.warpPerspective(
                board_image,
                cv2.getPerspectiveTransform(source, destination),
                (1600, 1200),
                borderValue=255,
            )
            observation = detect_charuco_observation(image, DEFAULT_CHARUCO_BOARD)
            self.assertIsNotNone(observation)
            observations.append(observation)

        request = CameraCalibrationInput(
            "synthetic-camera-calibration",
            "synthetic-camera",
            DEFAULT_CHARUCO_BOARD,
            1600,
            1200,
            tuple(observations),
        )
        from gripper_ai_controller.calibration import calibrate_charuco_camera

        result = calibrate_charuco_camera(request)
        self.assertEqual(MINIMUM_CHARUCO_VIEWS, result.views_used)
        self.assertGreater(result.intrinsics.fx_px, 0.0)
        self.assertGreater(result.intrinsics.fy_px, 0.0)
        self.assertTrue(math.isfinite(result.reprojection_error_px))


class RigidTransformTests(unittest.TestCase):
    """Verify the least-squares board-to-JAKA-base transform and residual contract."""

    def test_fit_recovers_a_planar_rotation_and_translation(self):
        """A non-collinear planar board is sufficient for a proper rigid transform."""

        board_points = (
            Point3D(0.0, 0.0, 0.0),
            Point3D(100.0, 0.0, 0.0),
            Point3D(0.0, 100.0, 0.0),
            Point3D(100.0, 100.0, 0.0),
        )
        base_points = (
            Point3D(10.0, 20.0, 30.0),
            Point3D(10.0, 120.0, 30.0),
            Point3D(-90.0, 20.0, 30.0),
            Point3D(-90.0, 120.0, 30.0),
        )
        fit = fit_board_to_jaka_base(board_points, base_points)
        transformed = fit.transform.apply(Point3D(25.0, 10.0, 0.0))
        self.assertAlmostEqual(0.0, fit.rms_error_mm, places=8)
        self.assertAlmostEqual(0.0, fit.maximum_error_mm, places=8)
        self.assertAlmostEqual(0.0, transformed.x_mm, places=8)
        self.assertAlmostEqual(45.0, transformed.y_mm, places=8)
        self.assertAlmostEqual(30.0, transformed.z_mm, places=8)
        self.assertEqual("board", fit.transform.source_frame)
        self.assertEqual("jaka_base", fit.transform.target_frame)

    def test_fit_rejects_collinear_reference_points(self):
        """Collinear taught points leave board orientation unconstrained."""

        points = tuple(Point3D(float(index), 0.0, 0.0) for index in range(4))
        with self.assertRaises(CalibrationValidationError):
            fit_board_to_jaka_base(points, points)


class PlaneProjectionTests(unittest.TestCase):
    """Verify image-to-plane-to-JAKA-base projection without a camera or OpenCV."""

    @staticmethod
    def _projector():
        intrinsics = CameraIntrinsics(640, 480, 1000.0, 1000.0, 320.0, 240.0)
        board_to_camera = RigidTransform(
            "board",
            "camera",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 1000.0),
        )
        board_to_jaka_base = RigidTransform(
            "board",
            "jaka_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(100.0, 200.0, 50.0),
        )
        return PixelPlaneProjector(intrinsics, board_to_camera, board_to_jaka_base)

    def test_projection_maps_an_offset_pixel_to_board_and_jaka_base(self):
        """Only position is produced; no depth or object orientation is inferred."""

        projected = self._projector().project(PixelPoint(420.0, 240.0))
        self.assertAlmostEqual(100.0, projected.board_point_mm.x_mm)
        self.assertAlmostEqual(0.0, projected.board_point_mm.y_mm)
        self.assertAlmostEqual(0.0, projected.board_point_mm.z_mm)
        self.assertAlmostEqual(200.0, projected.jaka_base_point_mm.x_mm)
        self.assertAlmostEqual(200.0, projected.jaka_base_point_mm.y_mm)
        self.assertAlmostEqual(50.0, projected.jaka_base_point_mm.z_mm)
        self.assertGreater(projected.ray_distance_mm, 1000.0)

    def test_projection_rejects_pixels_outside_the_calibrated_image(self):
        """A stale detector result from another image dimension cannot be projected."""

        with self.assertRaises(ProjectionError):
            self._projector().project(PixelPoint(640.0, 240.0))

    def test_projection_rejects_mismatched_board_frames(self):
        """Avoid silently combining camera and robot calibration records from different boards."""

        intrinsics = CameraIntrinsics(640, 480, 1000.0, 1000.0, 320.0, 240.0)
        board_to_camera = RigidTransform(
            "board-a",
            "camera",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 1000.0),
        )
        board_to_jaka_base = RigidTransform(
            "board-b",
            "jaka_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 0.0),
        )
        with self.assertRaises(ProjectionError):
            PixelPlaneProjector(intrinsics, board_to_camera, board_to_jaka_base)


class WorkcellCalibrationTests(unittest.TestCase):
    """Verify plugin-facing calibration identity and image-size checks."""

    @staticmethod
    def _workcell_calibration():
        camera_result = CameraCalibrationResult(
            "calibration-a",
            "camera-a",
            DEFAULT_CHARUCO_BOARD,
            CameraIntrinsics(640, 480, 1000.0, 1000.0, 320.0, 240.0),
            0.25,
            MINIMUM_CHARUCO_VIEWS,
        )
        board_to_camera = RigidTransform(
            "board",
            "camera",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 1000.0),
        )
        board_to_jaka_base = RigidTransform(
            "board",
            "jaka_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(100.0, 200.0, 50.0),
        )
        board_fit = RigidTransformFitResult(board_to_jaka_base, 4, 0.5, 0.8)
        return WorkcellCalibration("calibration-a", "camera-a", camera_result, board_to_camera, board_fit)

    def test_workcell_round_trips_and_creates_projector_for_matching_frame(self):
        """The passive plugin can load one JSON record and project only its own frames."""

        calibration = self._workcell_calibration()
        restored = WorkcellCalibration.from_dict(json.loads(json.dumps(calibration.to_dict())))
        self.assertEqual(0.5, restored.board_to_jaka_base_fit.rms_error_mm)
        self.assertEqual(0.8, restored.board_to_jaka_base_fit.maximum_error_mm)
        projection = restored.plane_projector_for_frame("camera-a", "calibration-a", 640, 480).project(
            PixelPoint(320.0, 240.0)
        )
        self.assertAlmostEqual(100.0, projection.jaka_base_point_mm.x_mm)
        self.assertAlmostEqual(200.0, projection.jaka_base_point_mm.y_mm)

    def test_workcell_rejects_other_camera_calibration_or_image_size(self):
        """Camera changes cannot reuse an old physical-coordinate calibration."""

        calibration = self._workcell_calibration()
        for frame_values in (
            ("camera-b", "calibration-a", 640, 480),
            ("camera-a", "calibration-b", 640, 480),
            ("camera-a", "calibration-a", 1280, 960),
        ):
            with self.assertRaises(CalibrationValidationError):
                calibration.plane_projector_for_frame(*frame_values)

    def test_workcell_rejects_a_fit_that_exceeds_the_default_acceptance_limit(self):
        """The transform remains inspectable, but not usable for a physical-coordinate result."""

        accepted = self._workcell_calibration()
        rejected_fit = RigidTransformFitResult(accepted.board_to_jaka_base, 4, 1.01, 2.0)
        calibration = WorkcellCalibration(
            "calibration-a",
            "camera-a",
            accepted.camera_calibration,
            accepted.board_to_camera,
            rejected_fit,
        )
        with self.assertRaises(CalibrationValidationError):
            calibration.plane_projector_for_frame("camera-a", "calibration-a", 640, 480)

    def test_workcell_rejects_a_transform_that_does_not_target_jaka_base(self):
        """A position may not be published as JAKA-base data under another frame name."""

        accepted = self._workcell_calibration()
        wrong_target = RigidTransform(
            "board",
            "robot_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(100.0, 200.0, 50.0),
        )
        with self.assertRaises(CalibrationValidationError):
            WorkcellCalibration(
                "calibration-a",
                "camera-a",
                accepted.camera_calibration,
                accepted.board_to_camera,
                RigidTransformFitResult(wrong_target, 4, 0.2, 0.3),
            )
