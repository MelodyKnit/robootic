"""Offline tests for GPU-independent human pose contracts, tracking, and configuration."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config
from gripper_ai_controller.configuration import PoseTrackingSettings
from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.pose.config_store import PoseTargetConfigStore
from gripper_ai_controller.pose.estimator import (
    PoseEstimator,
    PoseEstimatorError,
    TorchvisionKeypointRcnnEstimator,
    frame_to_rgb_bytes,
    resize_image_for_inference,
)
from gripper_ai_controller.pose.gpu import inspect_cuda_gpu
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D
from gripper_ai_controller.pose.tracker import PoseTargetError, PoseTrackingService


def make_frame(pixel_format="mono8", captured_at=1.0):
    """Build one in-memory frame whose pixels are valid for pose input conversion tests."""

    payload = b"\x10\x80" if pixel_format == "mono8" else b"\x10\x10\x10\x80\x80\x80"
    return ImageFrame(
        "camera-a",
        captured_at,
        "test://frame",
        "test-calibration",
        True,
        payload,
        2,
        1,
        pixel_format,
    )


def make_candidate(
    target_confidence=0.95,
    person_confidence=0.9,
    target_normalized_x=0.1,
    target_normalized_y=0.2,
    bounding_box=None,
):
    """Create a complete named COCO candidate without importing CUDA or a model file."""

    joints = []
    for index, name in enumerate(COCO_KEYPOINT_NAMES):
        confidence = target_confidence if name == "right_wrist" else 0.9
        if name == "right_wrist":
            joints.append(
                PoseJoint2D(
                    name,
                    target_normalized_x * 100.0,
                    target_normalized_y * 100.0,
                    target_normalized_x,
                    target_normalized_y,
                    confidence,
                )
            )
        else:
            joints.append(PoseJoint2D(name, float(index), 2.0, 0.1, 0.2, confidence))
    box = bounding_box or BoundingBox2D(0.1, 0.2, 0.4, 0.5)
    return PoseCandidate(box, person_confidence, tuple(joints))


class FakePoseEstimator(PoseEstimator):
    """Return a configured deterministic candidate sequence without model dependencies."""

    def __init__(self, candidate_sets):
        """Retain a sequence of outputs consumed once per inference call."""

        self.candidate_sets = list(candidate_sets)
        self.calls = 0

    def infer(self, frame):
        """Return the next configured candidate set for one submitted frame."""

        del frame
        self.calls += 1
        if not self.candidate_sets:
            return ()
        return self.candidate_sets.pop(0)


class BlockingPoseEstimator(PoseEstimator):
    """Hold the first inference so a test can verify latest-frame replacement."""

    def __init__(self):
        """Create synchronization points without importing CUDA dependencies."""

        self.started = threading.Event()
        self.release = threading.Event()
        self.captured_at_values = []

    def infer(self, frame):
        """Block only the initial worker call and return one complete candidate."""

        self.captured_at_values.append(frame.captured_at)
        if len(self.captured_at_values) == 1:
            self.started.set()
            self.release.wait(timeout=1.0)
        return (make_candidate(),)


class FakeCuda:
    """Provide a CUDA-capable fake Torch surface for read-only GPU preflight tests."""

    def is_available(self):
        """Report a configured usable CUDA device."""

        return True

    def get_device_name(self, index):
        """Return a deterministic fake device name."""

        self.last_index = index
        return "Fake RTX"


class FakeTorch:
    """Expose only the Torch metadata inspected by the GPU preflight routine."""

    __version__ = "1.13.1+cu117"

    class version:
        """Expose the pinned CUDA runtime version."""

        cuda = "11.7"

    cuda = FakeCuda()


class FakeCommandResult:
    """Represent one successful nvidia-smi command result."""

    returncode = 0
    stdout = "NVIDIA RTX, 610.62, 8.9\n"


class FakeMonotonicClock:
    """Advance tracker rate-limit time deterministically without affecting the event loop."""

    def __init__(self):
        """Start at an arbitrary valid monotonic time origin."""

        self.value = 0.0

    def __call__(self):
        """Provide the current fake monotonic time to the tracker only."""

        return self.value

    def advance(self, seconds):
        """Move forward without sleeping the test or changing asyncio's own clock."""

        self.value += seconds


def fake_command_runner(*arguments, **keyword_arguments):
    """Avoid launching a process in deterministic preflight tests."""

    del arguments, keyword_arguments
    return FakeCommandResult()


class FakeTensor:
    """Provide the limited Torch tensor conversion surface used by result mapping tests."""

    def __init__(self, value):
        """Keep one Python-native tensor value for deterministic conversion."""

        self.value = value

    def detach(self):
        """Mirror Torch's detached tensor API without retaining graph state."""

        return self

    def cpu(self):
        """Mirror CPU transfer for a fake data-only tensor."""

        return self

    def tolist(self):
        """Return the original nested Python values."""

        return self.value


class FakeModelTransform:
    """Expose Torchvision resize limits without importing the real model package."""

    def __init__(self):
        """Start with Torchvision's common detection-model resize defaults."""

        self.min_size = (800,)
        self.max_size = 1333


class FakeKeypointModel:
    """Provide the model transform surface configured by the estimator."""

    def __init__(self):
        """Create one mutable fake transform for the white-box resize contract test."""

        self.transform = FakeModelTransform()


class PoseFrameAndTrackingTests(unittest.TestCase):
    """Verify monochrome conversion, single-person selection, loss, and target selection."""

    def run_async(self, coroutine):
        """Run one tracker scenario under Python 3.7's asyncio standard library."""

        return asyncio.run(coroutine)

    def test_mono8_input_is_replicated_into_three_luminance_channels(self):
        self.assertEqual(b"\x10\x10\x10\x80\x80\x80", frame_to_rgb_bytes(make_frame()))
        self.assertEqual(make_frame("rgb8").pixel_payload, frame_to_rgb_bytes(make_frame("rgb8")))

    def test_invalid_frame_shape_is_rejected_before_model_inference(self):
        malformed = ImageFrame("camera-a", 1.0, None, None, True, b"\x10", 2, 1, "mono8")
        with self.assertRaises(PoseEstimatorError):
            frame_to_rgb_bytes(malformed)

    def test_inference_resize_preserves_aspect_ratio_and_uses_the_configured_limit(self):
        """Keep high-resolution Mono8 preprocessing bounded before CUDA receives pixels."""

        resized = resize_image_for_inference(Image.new("RGB", (2448, 2048)), 960)
        self.assertEqual((960, 803), resized.size)
        self.assertEqual((640, 480), resize_image_for_inference(Image.new("RGB", (640, 480)), 960).size)

    def test_model_transform_keeps_the_configured_inference_cap(self):
        """Prevent Torchvision from enlarging a frame after bounded preprocessing."""

        model = FakeKeypointModel()
        TorchvisionKeypointRcnnEstimator._configure_model_input_limit(model, 768)

        self.assertEqual((768,), model.transform.min_size)
        self.assertEqual(768, model.transform.max_size)

    def test_torchvision_result_mapping_keeps_named_normalized_keypoints(self):
        """Map fake model tensors without importing Torch or a CUDA runtime."""

        keypoints = [[float(index), float(index + 1), 1.0] for index in range(len(COCO_KEYPOINT_NAMES))]
        prediction = {
            "boxes": FakeTensor([[10.0, 20.0, 50.0, 80.0]]),
            "scores": FakeTensor([0.91]),
            "labels": FakeTensor([1]),
            "keypoints": FakeTensor([keypoints]),
            "keypoints_scores": FakeTensor([[0.88] * len(COCO_KEYPOINT_NAMES)]),
        }

        candidates = TorchvisionKeypointRcnnEstimator._map_prediction(prediction, 100, 100)

        self.assertEqual(1, len(candidates))
        self.assertEqual(0.1, candidates[0].bounding_box.x)
        self.assertEqual(0.6, candidates[0].bounding_box.height)
        self.assertEqual("right_wrist", candidates[0].joints[10].name)
        self.assertEqual(0.88, candidates[0].joints[10].confidence)

    def test_torchvision_result_without_joint_scores_never_creates_a_high_confidence_lock(self):
        """Treat unavailable model joint scores as unsafe instead of assuming full confidence."""

        keypoints = [[float(index), float(index + 1), 1.0] for index in range(len(COCO_KEYPOINT_NAMES))]
        prediction = {
            "boxes": FakeTensor([[10.0, 20.0, 50.0, 80.0]]),
            "scores": FakeTensor([0.91]),
            "labels": FakeTensor([1]),
            "keypoints": FakeTensor([keypoints]),
        }

        candidates = TorchvisionKeypointRcnnEstimator._map_prediction(prediction, 100, 100)

        self.assertEqual(1, len(candidates))
        self.assertEqual(0.0, candidates[0].joints[10].confidence)

    def test_torchvision_result_mapping_scales_coordinates_back_to_original_frame(self):
        """Map predictions made on a reduced image into original camera pixel coordinates."""

        keypoints = [[float(index), float(index + 1), 1.0] for index in range(len(COCO_KEYPOINT_NAMES))]
        prediction = {
            "boxes": FakeTensor([[10.0, 5.0, 50.0, 40.0]]),
            "scores": FakeTensor([0.91]),
            "labels": FakeTensor([1]),
            "keypoints": FakeTensor([keypoints]),
            "keypoints_scores": FakeTensor([[0.88] * len(COCO_KEYPOINT_NAMES)]),
        }

        candidates = TorchvisionKeypointRcnnEstimator._map_prediction(
            prediction,
            200,
            100,
            2.0,
            2.0,
        )

        self.assertEqual(1, len(candidates))
        self.assertAlmostEqual(0.1, candidates[0].bounding_box.x)
        self.assertAlmostEqual(0.1, candidates[0].bounding_box.y)
        self.assertAlmostEqual(0.4, candidates[0].bounding_box.width)
        self.assertAlmostEqual(0.7, candidates[0].bounding_box.height)
        self.assertEqual(20.0, candidates[0].joints[10].x_px)
        self.assertEqual(22.0, candidates[0].joints[10].y_px)

    def test_tracker_replaces_pending_frames_while_one_inference_is_active(self):
        """Ensure slow GPU work never turns camera captures into an unbounded FIFO queue."""

        async def scenario():
            accepted_sources = []

            async def retain_source(captured_at):
                """Record only a frame selected as an actual model input."""

                accepted_sources.append(captured_at)

            estimator = BlockingPoseEstimator()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                estimator,
            )
            await tracker.submit_frame(
                make_frame(captured_at=1.0),
                lambda: retain_source(1.0),
            )
            for _ in range(100):
                if estimator.started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(estimator.started.is_set())
            await tracker.submit_frame(
                make_frame(captured_at=2.0),
                lambda: retain_source(2.0),
            )
            await tracker.submit_frame(
                make_frame(captured_at=3.0),
                lambda: retain_source(3.0),
            )
            estimator.release.set()
            for _ in range(100):
                snapshot = await tracker.snapshot()
                if snapshot.captured_at == 3.0:
                    break
                await asyncio.sleep(0.01)
            await tracker.shutdown()

            self.assertEqual([1.0, 3.0], estimator.captured_at_values)
            self.assertEqual([1.0, 3.0], accepted_sources)
            self.assertEqual(3.0, snapshot.captured_at)
            self.assertEqual(1, tracker._inference_executor._max_workers)

        self.run_async(scenario())

    def test_tracker_reset_discards_old_results_and_reuses_the_inference_worker(self):
        """A camera switch must hide stale poses without rebuilding the model executor."""

        async def scenario():
            estimator = BlockingPoseEstimator()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                estimator,
            )
            original_executor = tracker._inference_executor
            reset_task = None
            try:
                self.assertTrue(await tracker.submit_frame(make_frame(captured_at=1.0)))
                for _ in range(100):
                    if estimator.started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(estimator.started.is_set())
                self.assertTrue(await tracker.submit_frame(make_frame(captured_at=2.0)))

                reset_task = asyncio.create_task(tracker.reset_frame_state())
                for _ in range(100):
                    if tracker._resetting_frame_state:
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(tracker._resetting_frame_state)
                self.assertFalse(await tracker.submit_frame(make_frame(captured_at=3.0)))
                self.assertFalse(reset_task.done())

                estimator.release.set()
                await reset_task
                pose_snapshot = await tracker.snapshot()
                inference_snapshot = await tracker.inference_snapshot()
                self.assertIsNone(pose_snapshot.captured_at)
                self.assertFalse(pose_snapshot.valid)
                self.assertIsNone(pose_snapshot.person)
                self.assertIsNone(pose_snapshot.target)
                self.assertEqual(0, pose_snapshot.lost_frames)
                self.assertIsNone(inference_snapshot.captured_at)
                self.assertEqual((), inference_snapshot.candidates)
                self.assertEqual([1.0], estimator.captured_at_values)
                self.assertIs(original_executor, tracker._inference_executor)

                self.assertTrue(await tracker.submit_frame(make_frame(captured_at=4.0)))
                for _ in range(100):
                    pose_snapshot = await tracker.snapshot()
                    if pose_snapshot.captured_at == 4.0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(4.0, pose_snapshot.captured_at)
                self.assertTrue(pose_snapshot.valid)
                self.assertEqual([1.0, 4.0], estimator.captured_at_values)
            finally:
                estimator.release.set()
                if reset_task is not None and not reset_task.done():
                    await reset_task
                await tracker.shutdown()

        self.run_async(scenario())

    def test_highest_confidence_single_person_and_target_joint_become_valid_snapshot(self):
        async def scenario():
            estimator = FakePoseEstimator(((make_candidate(person_confidence=0.8), make_candidate(person_confidence=0.95)),))
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                estimator,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            snapshot = await wait_for_pose(tracker)
            await tracker.shutdown()
            self.assertTrue(snapshot.valid)
            self.assertEqual("right_wrist", snapshot.target_joint)
            self.assertEqual(0.95, snapshot.person.confidence)
            self.assertEqual("right_wrist", snapshot.target.name)

        self.run_async(scenario())

    def test_low_confidence_target_and_missing_person_are_never_valid(self):
        async def scenario():
            estimator = FakePoseEstimator(((make_candidate(target_confidence=0.2),), ()))
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30, lost_after_frames=2),
                estimator,
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=2.0))
            second = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            await tracker.shutdown()
            self.assertFalse(first.valid)
            self.assertFalse(second.valid)
            self.assertEqual(2, second.lost_frames)

        self.run_async(scenario())

    def test_target_selection_rejects_unknown_joint_and_switches_to_a_visible_joint(self):
        async def scenario():
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                FakePoseEstimator(((make_candidate(),),)),
            )
            await tracker.submit_frame(make_frame())
            await wait_for_pose(tracker)
            switched = await tracker.set_target_joint("left_wrist")
            with self.assertRaises(PoseTargetError):
                await tracker.set_target_joint("unsupported")
            await tracker.shutdown()
            self.assertTrue(switched.valid)
            self.assertEqual("left_wrist", switched.target_joint)
            self.assertEqual("left_wrist", switched.target.name)

        self.run_async(scenario())

    def test_consecutive_valid_target_frames_publish_image_space_motion(self):
        """Expose passive target-joint velocity only after two consecutive valid inferences."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(
                    enabled=True,
                    max_inference_fps=30,
                    motion_speed_threshold=0.05,
                ),
                FakePoseEstimator(
                    (
                        (make_candidate(target_normalized_x=0.10),),
                        (make_candidate(target_normalized_x=0.30),),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=2.0))
            second = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            await tracker.shutdown()

            self.assertIsNone(first.motion)
            self.assertIsNotNone(second.motion)
            self.assertAlmostEqual(0.20, second.motion.delta_x)
            self.assertAlmostEqual(0.20, second.motion.displacement)
            self.assertAlmostEqual(0.20, second.motion.speed)
            self.assertTrue(second.motion.moving)

        self.run_async(scenario())

    def test_target_loss_resets_motion_baseline_before_a_new_person_lock(self):
        """Never compare a newly reacquired target to a coordinate from before a loss."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                FakePoseEstimator(
                    (
                        (make_candidate(target_normalized_x=0.10),),
                        (),
                        (make_candidate(target_normalized_x=0.80),),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=2.0))
            lost = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=3.0))
            reacquired = await wait_for_pose(tracker, previous_captured_at=lost.captured_at)
            await tracker.shutdown()

            self.assertFalse(lost.valid)
            self.assertIsNone(lost.motion)
            self.assertTrue(reacquired.valid)
            self.assertIsNone(reacquired.motion)

        self.run_async(scenario())

    def test_tracker_keeps_the_associated_person_when_another_person_has_a_higher_score(self):
        """Do not turn a detector-score swap between two people into false wrist movement."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracked_box = BoundingBox2D(0.10, 0.20, 0.30, 0.50)
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                FakePoseEstimator(
                    (
                        (make_candidate(0.95, 0.91, 0.10, bounding_box=tracked_box),),
                        (
                            make_candidate(
                                0.95,
                                0.84,
                                0.20,
                                bounding_box=BoundingBox2D(0.12, 0.20, 0.30, 0.50),
                            ),
                            make_candidate(
                                0.95,
                                0.99,
                                0.88,
                                bounding_box=BoundingBox2D(0.62, 0.20, 0.30, 0.50),
                            ),
                        ),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=2.0))
            second = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            await tracker.shutdown()

            self.assertTrue(second.valid)
            self.assertAlmostEqual(0.12, second.person.bounding_box.x)
            self.assertIsNotNone(second.motion)
            self.assertAlmostEqual(0.10, second.motion.delta_x)

        self.run_async(scenario())

    def test_unassociated_person_switch_invalidates_the_motion_baseline(self):
        """Require a fresh second frame after the previous selected person disappears."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(enabled=True, max_inference_fps=30),
                FakePoseEstimator(
                    (
                        (
                            make_candidate(
                                target_normalized_x=0.10,
                                bounding_box=BoundingBox2D(0.10, 0.20, 0.30, 0.50),
                            ),
                        ),
                        (
                            make_candidate(
                                target_normalized_x=0.85,
                                bounding_box=BoundingBox2D(0.62, 0.20, 0.30, 0.50),
                            ),
                        ),
                        (
                            make_candidate(
                                target_normalized_x=0.80,
                                bounding_box=BoundingBox2D(0.61, 0.20, 0.30, 0.50),
                            ),
                        ),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=2.0))
            switched = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=3.0))
            reacquired = await wait_for_pose(tracker, previous_captured_at=switched.captured_at)
            await tracker.shutdown()

            self.assertFalse(switched.valid)
            self.assertIn("could not be associated", switched.reason)
            self.assertIsNone(switched.motion)
            self.assertTrue(reacquired.valid)
            self.assertIsNone(reacquired.motion)

        self.run_async(scenario())

    def test_out_of_order_capture_time_does_not_replace_the_valid_motion_baseline(self):
        """Keep the last increasing timestamp when an SDK clock supplies an older frame."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(
                    enabled=True,
                    max_inference_fps=30,
                    motion_max_interval_seconds=5.0,
                ),
                FakePoseEstimator(
                    (
                        (make_candidate(target_normalized_x=0.10),),
                        (make_candidate(target_normalized_x=0.90),),
                        (make_candidate(target_normalized_x=0.30),),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=10.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=9.0))
            older = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=11.0))
            recovered = await wait_for_pose(tracker, previous_captured_at=older.captured_at)
            await tracker.shutdown()

            self.assertIsNone(older.motion)
            self.assertIsNotNone(recovered.motion)
            self.assertAlmostEqual(0.20, recovered.motion.delta_x)
            self.assertAlmostEqual(0.20, recovered.motion.speed)

        self.run_async(scenario())

    def test_long_capture_gap_rebuilds_the_motion_baseline_before_reporting_speed(self):
        """Avoid interpreting a stalled camera interval as continuous image-space movement."""

        async def scenario():
            clock = FakeMonotonicClock()
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(
                    enabled=True,
                    max_inference_fps=30,
                    motion_max_interval_seconds=1.0,
                ),
                FakePoseEstimator(
                    (
                        (make_candidate(target_normalized_x=0.10),),
                        (make_candidate(target_normalized_x=0.50),),
                        (make_candidate(target_normalized_x=0.60),),
                    )
                ),
                clock,
            )
            await tracker.submit_frame(make_frame(captured_at=1.0))
            first = await wait_for_pose(tracker)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=4.0))
            delayed = await wait_for_pose(tracker, previous_captured_at=first.captured_at)
            clock.advance(1.0)
            await tracker.submit_frame(make_frame(captured_at=4.5))
            resumed = await wait_for_pose(tracker, previous_captured_at=delayed.captured_at)
            await tracker.shutdown()

            self.assertIsNone(delayed.motion)
            self.assertIsNotNone(resumed.motion)
            self.assertAlmostEqual(0.10, resumed.motion.delta_x)
            self.assertAlmostEqual(0.20, resumed.motion.speed)

        self.run_async(scenario())


class PoseConfigurationAndGpuTests(unittest.TestCase):
    """Verify configuration persistence and GPU checks without a local model or GPU process."""

    def test_pose_target_store_replaces_only_the_selected_joint(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera", "vision_adapter_settings": {}},
            "camera_parameters": {"gain_db": 1.0},
            "pose": {"enabled": True, "target_joint": "right_wrist"},
        }
        with tempfile.TemporaryDirectory() as directory:
            localstore = Path(directory) / "localstore"
            localstore.mkdir()
            path = localstore / "preview.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            store = PoseTargetConfigStore(str(path), "camera-a", "simulated-camera", {})
            store.persist_target_joint("left_elbow")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("left_elbow", saved["pose"]["target_joint"])
            self.assertEqual({"gain_db": 1.0}, saved["camera_parameters"])

    def test_gpu_preflight_requires_cuda_117_torch_after_driver_check(self):
        ready = inspect_cuda_gpu(fake_command_runner, FakeTorch())
        with patch("gripper_ai_controller.pose.gpu.importlib.import_module", side_effect=ImportError):
            no_torch = inspect_cuda_gpu(fake_command_runner, None)
        self.assertTrue(ready.cuda_driver_compatible)
        self.assertTrue(ready.ready_for_pose_inference)
        self.assertEqual("Fake RTX", ready.torch_device_name)
        self.assertFalse(no_torch.ready_for_pose_inference)
        self.assertTrue(no_torch.cuda_driver_compatible)

    def test_preview_configuration_rejects_cpu_and_non_local_weight_paths(self):
        """Keep browser pose tracking CUDA-only and localstore-scoped before app creation."""

        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "pose": {"enabled": True, "device": "cpu"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pose.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pose.device"):
                load_vision_preview_config(str(path))

            absolute_windows_path = "C:" + chr(92) + "models" + chr(92) + "pose.pth"
            payload["pose"] = {"enabled": True, "weights_path": absolute_windows_path}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "localstore-relative"):
                load_vision_preview_config(str(path))

    def test_preview_configuration_validates_tracking_continuity_bounds(self):
        """Keep association and temporal-reset policy explicit in local JSON configuration."""

        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "pose": {
                "enabled": True,
                "tracking_min_iou": 0.35,
                "tracking_max_center_distance": 0.20,
                "motion_max_interval_seconds": 2.0,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pose.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            configuration = load_vision_preview_config(str(path))
            self.assertAlmostEqual(0.35, configuration.pose_settings.tracking_min_iou)
            self.assertAlmostEqual(0.20, configuration.pose_settings.tracking_max_center_distance)
            self.assertAlmostEqual(2.0, configuration.pose_settings.motion_max_interval_seconds)

            payload["pose"]["motion_max_interval_seconds"] = 0.0
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "motion_max_interval_seconds"):
                load_vision_preview_config(str(path))

    def test_preview_configuration_validates_low_latency_pose_settings(self):
        """Keep image-size, worker-thread, and overlay-freshness limits explicit in JSON."""

        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "pose": {
                "enabled": True,
                "inference_max_side": 960,
                "max_inference_fps": 2,
                "torch_cpu_threads": 2,
                "torch_interop_threads": 1,
                "overlay_max_frame_lag_seconds": 0.35,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pose.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            configuration = load_vision_preview_config(str(path))
            self.assertEqual(960, configuration.pose_settings.inference_max_side)
            self.assertEqual(2, configuration.pose_settings.max_inference_fps)
            self.assertEqual(2, configuration.pose_settings.torch_cpu_threads)
            self.assertEqual(1, configuration.pose_settings.torch_interop_threads)
            self.assertAlmostEqual(0.35, configuration.pose_settings.overlay_max_frame_lag_seconds)

            del payload["pose"]["inference_max_side"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            configuration = load_vision_preview_config(str(path))
            self.assertEqual(768, configuration.pose_settings.inference_max_side)

            payload["pose"]["inference_max_side"] = 0
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inference_max_side"):
                load_vision_preview_config(str(path))

    def test_preview_configuration_rejects_non_finite_numeric_values(self):
        """Reject JSON NaN and Infinity before they can disable freshness or retry bounds."""

        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "web": {"capture_retry_seconds": float("nan")},
            "pose": {"enabled": True, "overlay_max_frame_lag_seconds": 0.35},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pose.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "capture_retry_seconds"):
                load_vision_preview_config(str(path))

            payload["web"] = {}
            payload["pose"]["overlay_max_frame_lag_seconds"] = float("inf")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlay_max_frame_lag_seconds"):
                load_vision_preview_config(str(path))


async def wait_for_pose(tracker, previous_captured_at=None):
    """Wait for one completed background fake inference without relying on real timeouts externally."""

    for _ in range(100):
        snapshot = await tracker.snapshot()
        if snapshot.captured_at is not None and snapshot.captured_at != previous_captured_at:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError("The fake pose inference did not publish a snapshot.")


if __name__ == "__main__":
    unittest.main()
