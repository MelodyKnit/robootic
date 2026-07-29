"""Offline tests for passive image diagnostics, pose visibility, and fixture evaluation."""

import asyncio
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from gripper_ai_controller.bootstrap.preview_builder import load_vision_evaluation_config
from gripper_ai_controller.configuration import PoseTrackingSettings, VisionAnalysisSettings
from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.pose.estimator import PoseEstimator
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D
from gripper_ai_controller.pose.tracker import PoseTrackingService
from gripper_ai_controller.vision.analysis import JointVisibilityEvaluator, VisionAnalysisService
from gripper_ai_controller.vision.fixtures import (
    VisionFixtureManifestError,
    evaluation_report,
    evaluate_fixture,
    load_fixture_frame,
    load_vision_fixtures,
    verify_vision_fixture,
)
from gripper_ai_controller.vision.quality import FrameQualityInspector


def make_mono_frame(payload=b"\x00\x10\x20\x30\x40\x50\x60\x70\x80", captured_at=1.0):
    """Create one 3x3 Mono8 image with deterministic contrast and edges."""

    return ImageFrame("camera-a", captured_at, "test://frame", None, True, payload, 3, 3, "mono8")


def make_rgb_frame(captured_at=1.0):
    """Build one valid RGB8 frame for quality-path coverage without a camera adapter."""

    return ImageFrame(
        "camera-a",
        captured_at,
        "test://rgb-frame",
        None,
        True,
        bytes((0, 0, 0, 255, 255, 255, 128, 64, 32, 16, 32, 64)),
        2,
        2,
        "rgb8",
    )


def make_candidate(target_confidence=0.95, right_wrist_x=2.0):
    """Create a full 17-joint candidate whose coordinates can be classified deterministically."""

    joints = []
    for index, name in enumerate(COCO_KEYPOINT_NAMES):
        confidence = target_confidence if name == "right_wrist" else 0.9
        joints.append(PoseJoint2D(name, 2.0, 2.0, 0.5, 0.5, confidence))
    joints[COCO_KEYPOINT_NAMES.index("right_wrist")] = PoseJoint2D(
        "right_wrist", right_wrist_x, 2.0, min(1.0, right_wrist_x / 4.0), 0.5, target_confidence
    )
    return PoseCandidate(BoundingBox2D(0.1, 0.1, 0.6, 0.8), 0.95, tuple(joints))


def make_analysis_settings(**overrides):
    """Create a focused settings double for quality-worker scheduling tests."""

    values = {
        "minimum_width": 320,
        "minimum_height": 240,
        "minimum_brightness": 20.0,
        "maximum_brightness": 235.0,
        "minimum_contrast": 8.0,
        "minimum_sharpness": 5.0,
        "sample_max_side": 640,
        "max_analysis_fps": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakePoseEstimator(PoseEstimator):
    """Return a fixed candidate tuple without importing Torch or allocating CUDA memory."""

    def __init__(self, candidates):
        self.candidates = tuple(candidates)

    def infer(self, frame):
        del frame
        return self.candidates


class FrameQualityAndVisibilityTests(unittest.TestCase):
    """Verify pure image metrics and joint classifications without camera hardware."""

    def test_frame_quality_reports_measurements_and_non_blocking_warnings(self):
        inspector = FrameQualityInspector(
            VisionAnalysisSettings(
                minimum_width=4,
                minimum_height=4,
                minimum_brightness=10.0,
                maximum_brightness=240.0,
                minimum_contrast=3.0,
                minimum_sharpness=1.0,
            )
        )
        diagnostics = inspector.inspect(make_mono_frame())
        self.assertTrue(diagnostics.valid)
        self.assertIsNotNone(diagnostics.brightness_mean)
        self.assertIsNotNone(diagnostics.contrast)
        self.assertIsNotNone(diagnostics.sharpness)
        self.assertIn("resolution_low", diagnostics.warnings)

    def test_malformed_frame_becomes_a_diagnostic_without_raising(self):
        diagnostics = FrameQualityInspector(VisionAnalysisSettings()).inspect(
            ImageFrame("camera-a", 1.0, None, None, True, b"\x00", 2, 2, "mono8")
        )
        self.assertFalse(diagnostics.valid)
        self.assertEqual(("mono_payload_size_invalid",), diagnostics.warnings)

    def test_rgb8_quality_uses_luminance_not_a_camera_specific_conversion(self):
        diagnostics = FrameQualityInspector(VisionAnalysisSettings()).inspect(make_rgb_frame())
        self.assertTrue(diagnostics.valid)
        self.assertEqual("rgb8", diagnostics.pixel_format)
        self.assertGreater(diagnostics.brightness_mean, 0.0)
        self.assertGreater(diagnostics.contrast, 0.0)

    def test_large_frame_quality_uses_a_bounded_native_sample(self):
        """High-resolution diagnostics must not allocate full-size float arrays."""

        frame = ImageFrame(
            "camera-a",
            1.0,
            "test://large-frame",
            None,
            True,
            bytes(range(120)) * 5,
            20,
            30,
            "mono8",
        )
        inspector = FrameQualityInspector(make_analysis_settings(sample_max_side=10))
        luminance = inspector._luminance(frame)
        diagnostics = inspector.inspect(frame)
        self.assertEqual((10, 6), luminance.shape)
        self.assertLessEqual(max(luminance.shape), 10)
        self.assertEqual(20, diagnostics.width)
        self.assertEqual(30, diagnostics.height)

    def test_joint_visibility_uses_image_bounds_before_confidence(self):
        visibility = JointVisibilityEvaluator(0.8).evaluate(make_candidate(0.2, 5.0).joints, 4, 4)
        states = {joint.name: joint.state for joint in visibility}
        self.assertEqual("out_of_frame", states["right_wrist"])
        self.assertEqual("detected", states["left_wrist"])

    def test_joint_visibility_uses_normalized_bounds_without_a_matching_quality_frame(self):
        """Keep timestamp-mismatched diagnostics from reporting impossible joints as detected."""

        visibility = JointVisibilityEvaluator(0.8).evaluate(make_candidate(0.95, 5.0).joints, None, None)
        states = {joint.name: joint.state for joint in visibility}
        self.assertEqual("out_of_frame", states["right_wrist"])


class VisionAnalysisServiceTests(unittest.TestCase):
    """Verify analysis composes cached frame diagnostics with the one existing inference task."""

    def run_async(self, coroutine):
        """Run one bounded asynchronous test under Python 3.7's standard library."""

        return asyncio.run(coroutine)

    async def wait_for(self, predicate, timeout_seconds=2.0):
        """Wait for bounded executor work without relying on fixed scheduling delays."""

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("Timed out waiting for asynchronous quality analysis.")

    def test_disabled_tracking_still_reports_frame_quality(self):
        async def scenario():
            service = VisionAnalysisService(
                "camera-a",
                VisionAnalysisSettings(),
                PoseTrackingSettings(),
                None,
            )
            try:
                await service.record_frame(make_mono_frame())
                await self.wait_for(lambda: bool(service._qualities))
                snapshot = await service.snapshot()
                self.assertFalse(snapshot.pose_enabled)
                self.assertFalse(snapshot.valid)
                self.assertIsNotNone(snapshot.frame)
                self.assertEqual((), snapshot.persons)
            finally:
                await service.shutdown()

        self.run_async(scenario())

    def test_tracking_analysis_reuses_qualified_person_candidates(self):
        async def scenario():
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                FakePoseEstimator((make_candidate(),)),
            )
            service = VisionAnalysisService(
                "camera-a",
                VisionAnalysisSettings(),
                tracker.settings,
                tracker,
            )
            frame = make_mono_frame(captured_at=4.0)
            await service.record_frame(frame)
            await tracker.submit_frame(frame)
            for _ in range(100):
                snapshot = await service.snapshot()
                if snapshot.inference_captured_at == 4.0:
                    break
                await asyncio.sleep(0.01)
            try:
                self.assertEqual(1, len(snapshot.persons))
                self.assertTrue(snapshot.persons[0].selected)
                self.assertIn("right_wrist", snapshot.visible_joint_names)
                self.assertTrue(snapshot.valid)
            finally:
                await service.shutdown()
                await tracker.shutdown()

        self.run_async(scenario())

    def test_quality_worker_replaces_pending_frame_without_blocking_capture(self):
        """Only the active frame and the latest queued frame may reach the inspector."""

        class BlockingInspector:
            """Block the first inspection until the test has submitted newer frames."""

            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.calls = []
                self.delegate = FrameQualityInspector(make_analysis_settings())

            def inspect(self, frame):
                self.calls.append(frame.captured_at)
                self.started.set()
                self.release.wait(timeout=2.0)
                return self.delegate.inspect(frame)

        async def scenario():
            service = VisionAnalysisService(
                "camera-a",
                make_analysis_settings(max_analysis_fps=100),
                PoseTrackingSettings(),
                None,
            )
            inspector = BlockingInspector()
            service._inspector = inspector
            try:
                await service.record_frame(make_mono_frame(captured_at=1.0))
                await self.wait_for(inspector.started.is_set)
                queued_at = time.monotonic()
                await service.record_frame(make_mono_frame(captured_at=2.0))
                await service.record_frame(make_mono_frame(captured_at=3.0))
                self.assertLess(time.monotonic() - queued_at, 0.2)
                inspector.release.set()
                await self.wait_for(lambda: inspector.calls == [1.0, 3.0])
                await self.wait_for(lambda: 3.0 in service._qualities)
                snapshot = await service.snapshot()
                self.assertEqual(3.0, snapshot.frame_captured_at)
            finally:
                inspector.release.set()
                await service.shutdown()

        self.run_async(scenario())

    def test_analysis_reset_discards_old_quality_and_reuses_the_worker(self):
        """A camera switch clears diagnostics after draining the one active inspection."""

        class BlockingInspector:
            """Block only the first inspection to expose reset and pending-frame behavior."""

            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.calls = []
                self.delegate = FrameQualityInspector(make_analysis_settings())

            def inspect(self, frame):
                self.calls.append(frame.captured_at)
                if len(self.calls) == 1:
                    self.started.set()
                    self.release.wait(timeout=2.0)
                return self.delegate.inspect(frame)

        async def scenario():
            service = VisionAnalysisService(
                "camera-a",
                make_analysis_settings(max_analysis_fps=100),
                PoseTrackingSettings(),
                None,
            )
            inspector = BlockingInspector()
            service._inspector = inspector
            original_executor = service._analysis_executor
            reset_task = None
            try:
                await service.record_frame(make_mono_frame(captured_at=1.0))
                await self.wait_for(inspector.started.is_set)
                await service.record_frame(make_mono_frame(captured_at=2.0))

                reset_task = asyncio.create_task(service.reset_frame_state())
                await self.wait_for(lambda: service._resetting_frame_state)
                await service.record_frame(make_mono_frame(captured_at=3.0))
                self.assertFalse(reset_task.done())

                inspector.release.set()
                await reset_task
                snapshot = await service.snapshot()
                self.assertIsNone(snapshot.frame_captured_at)
                self.assertIsNone(snapshot.frame)
                self.assertEqual((), snapshot.persons)
                self.assertEqual([1.0], inspector.calls)
                self.assertIs(original_executor, service._analysis_executor)
                self.assertFalse(service._executor_shutdown)

                await service.record_frame(make_mono_frame(captured_at=4.0))
                await self.wait_for(lambda: 4.0 in service._qualities)
                snapshot = await service.snapshot()
                self.assertEqual(4.0, snapshot.frame_captured_at)
                self.assertIsNotNone(snapshot.frame)
                self.assertEqual([1.0, 4.0], inspector.calls)
            finally:
                inspector.release.set()
                if reset_task is not None and not reset_task.done():
                    await reset_task
                await service.shutdown()

        self.run_async(scenario())

    def test_quality_worker_honors_configured_analysis_rate(self):
        """The worker delays a newer quality measurement until its rate window opens."""

        class TimestampInspector:
            """Record native worker start times while preserving normal diagnostics."""

            def __init__(self):
                self.started_at = []
                self.delegate = FrameQualityInspector(make_analysis_settings())

            def inspect(self, frame):
                self.started_at.append(time.monotonic())
                return self.delegate.inspect(frame)

        async def scenario():
            service = VisionAnalysisService(
                "camera-a",
                make_analysis_settings(max_analysis_fps=10),
                PoseTrackingSettings(),
                None,
            )
            inspector = TimestampInspector()
            service._inspector = inspector
            try:
                await service.record_frame(make_mono_frame(captured_at=1.0))
                await self.wait_for(lambda: len(inspector.started_at) == 1)
                await service.record_frame(make_mono_frame(captured_at=2.0))
                await self.wait_for(lambda: len(inspector.started_at) == 2)
                self.assertGreaterEqual(inspector.started_at[1] - inspector.started_at[0], 0.08)
            finally:
                await service.shutdown()

        self.run_async(scenario())


class FixtureManifestTests(unittest.TestCase):
    """Verify fixture assets are local, checksummed, and evaluated in RGB and Mono8 modes."""

    def test_manifest_hash_and_monochrome_fixture_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "fixture.png"
            Image.new("RGB", (4, 4), (120, 120, 120)).save(str(image_path), format="PNG")
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "fixtures": [
                            {
                                "id": "fixture-person",
                                "file": "fixture.png",
                                "source_url": "https://example.invalid/fixture.png",
                                "author": "Public Domain Test",
                                "license": "Public Domain",
                                "sha256": digest,
                                "minimum_person_count": 1,
                                "maximum_person_count": 1,
                                "minimum_visible_joints": 1,
                                "required_visible_joints": ["right_wrist"],
                                "required_quality_warnings": [],
                                "manual_review": "Verify the overlay by inspection.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            fixture = load_vision_fixtures(str(manifest_path))[0]
            frame = load_fixture_frame(fixture, "mono8")
            self.assertEqual("mono8", frame.pixel_format)
            evaluation = evaluate_fixture(
                fixture,
                "rgb8",
                FakePoseEstimator((make_candidate(),)),
                FrameQualityInspector(VisionAnalysisSettings()),
                JointVisibilityEvaluator(0.6),
                0.75,
            )
            self.assertTrue(evaluation.passed)
            report = evaluation_report((evaluation,))
            self.assertEqual([], report["fixtures"][0]["required_quality_warnings"])

    def test_versioned_fixture_manifest_hashes_match_committed_images(self):
        fixtures = load_vision_fixtures("data/vision-fixtures/manifest.json")
        self.assertEqual(
            {
                "full-body-front",
                "upper-body",
                "occluded-side",
                "no-person",
                "low-light",
            },
            {fixture.fixture_id for fixture in fixtures},
        )
        for fixture in fixtures:
            verify_vision_fixture(fixture)

    def test_manifest_rejects_unapproved_license_and_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "fixtures": [
                            {
                                "id": "bad",
                                "file": "../outside.png",
                                "source_url": "https://example.invalid/outside.png",
                                "author": "Unknown",
                                "license": "CC-BY-4.0",
                                "sha256": "0" * 64,
                                "minimum_person_count": 0,
                                "maximum_person_count": 0,
                                "minimum_visible_joints": 0,
                                "required_visible_joints": [],
                                "required_quality_warnings": [],
                                "manual_review": "No review.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(VisionFixtureManifestError):
                load_vision_fixtures(str(manifest_path))

    def test_manifest_rejects_unknown_required_joint(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "fixtures": [
                            {
                                "id": "bad-joint",
                                "file": "fixture.png",
                                "source_url": "https://example.invalid/fixture.png",
                                "author": "Unknown",
                                "license": "Public Domain",
                                "sha256": "0" * 64,
                                "minimum_person_count": 0,
                                "maximum_person_count": 0,
                                "minimum_visible_joints": 0,
                                "required_visible_joints": ["not_a_coco_joint"],
                                "required_quality_warnings": [],
                                "manual_review": "No review.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(VisionFixtureManifestError):
                load_vision_fixtures(str(manifest_path))

    def test_fixture_requires_declared_low_brightness_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "dark.png"
            Image.new("RGB", (32, 32), (0, 0, 0)).save(str(image_path), format="PNG")
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "fixtures": [
                            {
                                "id": "dark",
                                "file": "dark.png",
                                "source_url": "https://example.invalid/dark.png",
                                "author": "Public Domain Test",
                                "license": "Public Domain",
                                "sha256": digest,
                                "minimum_person_count": 0,
                                "maximum_person_count": 0,
                                "minimum_visible_joints": 0,
                                "required_visible_joints": [],
                                "required_quality_warnings": ["brightness_low"],
                                "manual_review": "Verify the quality warning.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            fixture = load_vision_fixtures(str(manifest_path))[0]
            evaluation = evaluate_fixture(
                fixture,
                "mono8",
                FakePoseEstimator(()),
                FrameQualityInspector(VisionAnalysisSettings()),
                JointVisibilityEvaluator(0.6),
                0.75,
            )
            self.assertTrue(evaluation.passed)


class VisionEvaluationConfigurationTests(unittest.TestCase):
    """Verify static-image evaluation does not require camera or component JSON sections."""

    def test_loads_pose_and_analysis_without_constructing_a_camera_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.json"
            path.write_text(
                json.dumps(
                    {
                        "pose": {"enabled": True, "weights_path": "localstore/models/pose.pth"},
                        "vision_analysis": {"minimum_width": 640, "minimum_height": 480},
                    }
                ),
                encoding="utf-8",
            )
            configuration = load_vision_evaluation_config(str(path))
            self.assertTrue(configuration.pose_settings.enabled)
            self.assertEqual(640, configuration.vision_analysis_settings.minimum_width)


if __name__ == "__main__":
    unittest.main()
