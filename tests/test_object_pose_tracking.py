"""Passive known-workpiece tracking tests using only synthetic frames and calibration."""

import asyncio
import math
import time
import unittest

from PIL import Image, ImageDraw

from gripper_ai_controller.calibration import (
    DEFAULT_CHARUCO_BOARD,
    CameraCalibrationResult,
    CameraIntrinsics,
    Point3D,
    RigidTransform,
    RigidTransformFitResult,
    WorkcellCalibration,
)
from gripper_ai_controller.configuration import WorkpiecePoseSettings
from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.object_pose import (
    ForegroundObjectPoseEstimator,
    KnownWorkpieceProfile,
    ObjectPoseSettings,
    ObjectPoseTrackingService,
)
from gripper_ai_controller.plugins.object_pose_analysis import build_object_pose_analysis_plugin


class ObjectPoseTrackingServiceTests(unittest.TestCase):
    """Verify cached plane-constrained output without an adapter or hardware connection."""

    def test_projects_xy_yaw_and_marks_height_and_tilt_as_derived(self):
        """A stable horizontal workpiece publishes only calibrated passive metadata."""

        async def scenario():
            background = self._frame(self._table(), captured_at=0.0)
            current = self._table()
            ImageDraw.Draw(current).rectangle((55, 43, 85, 53), fill=255)
            settings = self._settings(stable_required_frames=1, grasp_height_mm=4.0)
            tracker = ObjectPoseTrackingService(
                "camera-a",
                settings,
                ForegroundObjectPoseEstimator(settings.detector_settings, background),
                self._calibration(),
            )
            try:
                await tracker.submit_frame(self._frame(current, captured_at=1.0))
                snapshot = await self._wait_for_capture(tracker, 1.0)
            finally:
                await tracker.shutdown()
            self.assertTrue(snapshot.valid)
            self.assertEqual("object_pose_available", snapshot.reason)
            self.assertEqual(1, len(snapshot.objects))
            object_pose = snapshot.objects[0]
            self.assertEqual("known-workpiece-v1", object_pose.profile_id)
            self.assertEqual(("x", "y", "yaw"), object_pose.observed_dof)
            self.assertEqual(("z", "roll", "pitch"), object_pose.derived_dof)
            self.assertEqual("jaka_base", object_pose.coordinate_frame)
            self.assertAlmostEqual(110.0, object_pose.translation_mm.x_mm, delta=1.5)
            self.assertAlmostEqual(200.0, object_pose.translation_mm.y_mm, delta=1.5)
            self.assertAlmostEqual(54.0, object_pose.translation_mm.z_mm, delta=0.01)
            self.assertAlmostEqual(0.0, object_pose.orientation_rpy_rad.roll_rad, delta=0.05)
            self.assertAlmostEqual(0.0, object_pose.orientation_rpy_rad.pitch_rad, delta=0.05)
            self.assertLess(abs(object_pose.orientation_rpy_rad.yaw_rad), 0.08)
            self.assertAlmostEqual(math.pi, object_pose.yaw_period_rad)
            self.assertEqual("orientation_pi_ambiguous", object_pose.warning)

        asyncio.run(scenario())

    def test_rejects_a_frame_with_another_calibration_before_position_is_published(self):
        """A selected camera or calibration switch leaves no stale base-frame object."""

        async def scenario():
            background = self._frame(self._table(), captured_at=0.0)
            current = self._table()
            ImageDraw.Draw(current).rectangle((55, 43, 85, 53), fill=255)
            settings = self._settings(stable_required_frames=1)
            tracker = ObjectPoseTrackingService(
                "camera-a",
                settings,
                ForegroundObjectPoseEstimator(settings.detector_settings, background),
                self._calibration(),
            )
            try:
                wrong_calibration = self._frame(
                    current,
                    captured_at=2.0,
                    calibration_id="another-calibration",
                )
                await tracker.submit_frame(wrong_calibration)
                snapshot = await self._wait_for_capture(tracker, 2.0)
            finally:
                await tracker.shutdown()
            self.assertFalse(snapshot.valid)
            self.assertEqual("calibration_id_mismatch", snapshot.reason)
            self.assertEqual((), snapshot.objects)

        asyncio.run(scenario())

    def test_rejects_projected_dimensions_that_contradict_the_flat_workpiece_profile(self):
        """Tilt, occlusion, adhesion, and wrong shapes remain a safe no-pose result."""

        async def scenario():
            background = self._frame(self._table(), captured_at=0.0)
            current = self._table()
            ImageDraw.Draw(current).rectangle((55, 44, 70, 50), fill=255)
            detector_settings = ObjectPoseSettings(
                profile=KnownWorkpieceProfile(
                    minimum_area_px=80,
                    minimum_aspect_ratio=2.0,
                    nominal_length_mm=30.0,
                    nominal_width_mm=10.0,
                    object_thickness_mm=4.0,
                    maximum_planar_dimension_error_ratio=0.15,
                ),
                difference_threshold=20,
                maximum_foreground_ratio=0.4,
            )
            settings = self._settings(
                detector_settings=detector_settings,
                stable_required_frames=1,
            )
            tracker = ObjectPoseTrackingService(
                "camera-a",
                settings,
                ForegroundObjectPoseEstimator(detector_settings, background),
                self._calibration(),
            )
            try:
                await tracker.submit_frame(self._frame(current, captured_at=1.0))
                snapshot = await self._wait_for_capture(tracker, 1.0)
            finally:
                await tracker.shutdown()
            self.assertFalse(snapshot.valid)
            self.assertEqual("planarity_suspected", snapshot.reason)
            self.assertEqual((), snapshot.objects)

        asyncio.run(scenario())

    def test_applies_a_declared_directional_grasp_origin_offset_in_board_coordinates(self):
        """A measured profile offset moves the passive position, but still creates no command."""

        async def scenario():
            background = self._frame(self._table(), captured_at=0.0)
            current = self._table()
            drawing = ImageDraw.Draw(current)
            drawing.rectangle((42, 44, 80, 52), fill=255)
            drawing.rectangle((75, 38, 92, 58), fill=255)
            detector_settings = ObjectPoseSettings(
                profile=KnownWorkpieceProfile(
                    profile_id="known-workpiece-v1",
                    minimum_area_px=80,
                    minimum_aspect_ratio=1.5,
                    require_directional_yaw=True,
                    directional_feature="larger_end",
                    minimum_directional_asymmetry=0.15,
                    nominal_length_mm=50.0,
                    nominal_width_mm=20.0,
                    object_thickness_mm=4.0,
                    grasp_origin_offset_x_mm=5.0,
                    maximum_planar_dimension_error_ratio=0.35,
                ),
                difference_threshold=20,
                maximum_foreground_ratio=0.4,
            )
            settings = self._settings(
                detector_settings=detector_settings,
                stable_required_frames=1,
                grasp_height_mm=4.0,
            )
            tracker = ObjectPoseTrackingService(
                "camera-a",
                settings,
                ForegroundObjectPoseEstimator(detector_settings, background),
                self._calibration(),
            )
            try:
                await tracker.submit_frame(self._frame(current, captured_at=1.0))
                snapshot = await self._wait_for_capture(tracker, 1.0)
            finally:
                await tracker.shutdown()
            self.assertTrue(snapshot.valid)
            object_pose = snapshot.objects[0]
            self.assertAlmostEqual(2.0 * math.pi, object_pose.yaw_period_rad)
            self.assertAlmostEqual(
                100.0 + (object_pose.pixel_center.x - 60.0) + 5.0,
                object_pose.translation_mm.x_mm,
                delta=1.0,
            )
            self.assertAlmostEqual(54.0, object_pose.translation_mm.z_mm, delta=0.01)

        asyncio.run(scenario())

    def test_replaces_pending_frames_with_the_latest_capture(self):
        """Slow classical segmentation cannot make the cache progress through an old FIFO."""

        class SlowEstimator:
            def __init__(self, delegate):
                self.delegate = delegate

            def analyze(self, frame):
                time.sleep(0.04)
                return self.delegate.analyze(frame)

        async def scenario():
            background = self._frame(self._table(), captured_at=0.0)
            image = self._table()
            ImageDraw.Draw(image).rectangle((55, 43, 85, 53), fill=255)
            settings = self._settings(stable_required_frames=1, max_analysis_fps=30)
            tracker = ObjectPoseTrackingService(
                "camera-a",
                settings,
                SlowEstimator(ForegroundObjectPoseEstimator(settings.detector_settings, background)),
                self._calibration(),
            )
            try:
                await tracker.submit_frame(self._frame(image, captured_at=1.0))
                await tracker.submit_frame(self._frame(image, captured_at=2.0))
                await tracker.submit_frame(self._frame(image, captured_at=3.0))
                snapshot = await self._wait_for_capture(tracker, 3.0)
            finally:
                await tracker.shutdown()
            self.assertTrue(snapshot.valid)
            self.assertEqual(3.0, snapshot.captured_at)

        asyncio.run(scenario())

    def test_disabled_plugin_never_requires_assets_or_submits_frames(self):
        """The default template has an explicit safe cache state without local files."""

        async def scenario():
            plugin = build_object_pose_analysis_plugin("camera-a", WorkpiecePoseSettings())
            await plugin.startup()
            try:
                await plugin.handle_event(object())
                snapshot = await plugin.object_snapshot()
            finally:
                await plugin.shutdown()
            self.assertFalse(snapshot.enabled)
            self.assertFalse(snapshot.valid)
            self.assertEqual("object_pose_disabled", snapshot.reason)
            self.assertEqual((), snapshot.objects)

        asyncio.run(scenario())

    @staticmethod
    async def _wait_for_capture(tracker, captured_at):
        """Wait for a bounded worker to publish one named source timestamp."""

        for _ in range(200):
            snapshot = await tracker.snapshot()
            if snapshot.captured_at == captured_at:
                return snapshot
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for object-pose worker output.")

    @staticmethod
    def _table():
        """Create a black fixed-table background for a small deterministic test frame."""

        return Image.new("L", (120, 96), 0)

    @staticmethod
    def _frame(image, captured_at=1.0, calibration_id="calibration-a"):
        """Wrap an in-memory mono image in the project's normalized capture contract."""

        return ImageFrame(
            "camera-a",
            captured_at,
            None,
            calibration_id,
            True,
            image.tobytes(),
            image.width,
            image.height,
            "mono8",
        )

    @staticmethod
    def _settings(**overrides):
        """Build a strict small-frame profile without relying on local configuration files."""

        detector_settings = ObjectPoseSettings(
            profile=KnownWorkpieceProfile(
                profile_id="known-workpiece-v1",
                minimum_area_px=80,
                minimum_aspect_ratio=2.0,
            ),
            difference_threshold=20,
            maximum_foreground_ratio=0.4,
        )
        values = {
            "enabled": True,
            "expected_calibration_id": "calibration-a",
            "detector_settings": detector_settings,
            "stable_required_frames": 1,
            "maximum_center_jitter_px": 4.0,
            "maximum_yaw_jitter_rad": 0.08,
            "maximum_reprojection_error_px": 0.5,
            "maximum_base_fit_rms_error_mm": 1.0,
        }
        values.update(overrides)
        return WorkpiecePoseSettings(**values)

    @staticmethod
    def _calibration():
        """Create an exact synthetic fixed-camera board calibration in memory."""

        camera_calibration = CameraCalibrationResult(
            "calibration-a",
            "camera-a",
            DEFAULT_CHARUCO_BOARD,
            CameraIntrinsics(120, 96, 1000.0, 1000.0, 60.0, 48.0),
            0.2,
            25,
        )
        board_to_camera = RigidTransform(
            "board",
            "camera",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 1000.0),
        )
        board_to_base = RigidTransform(
            "board",
            "jaka_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(100.0, 200.0, 50.0),
        )
        return WorkcellCalibration(
            "calibration-a",
            "camera-a",
            camera_calibration,
            board_to_camera,
            RigidTransformFitResult(board_to_base, 4, 0.2, 0.4),
        )
