"""Offline tests for the FastAPI camera preview and bounded parameter boundary."""

import asyncio
import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.adapters.jaka import JakaDryRunRobotAdapter
from gripper_ai_controller.adapters.pgi import PgiTcpGripperAdapter
from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter, SimulatedGripperAdapter
from gripper_ai_controller.bootstrap.preview_builder import (
    VisionPreviewConfig,
    load_vision_preview_config,
    validate_web_settings,
)
from gripper_ai_controller.configuration import PoseTrackingSettings, WebPreviewSettings
from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.domain.ports import VisionAdapter
from gripper_ai_controller.pose.config_store import PoseTargetConfigStore
from gripper_ai_controller.pose.estimator import PoseEstimator
from gripper_ai_controller.pose.gpu import GpuPreflightResult
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D
from gripper_ai_controller.pose.tracker import PoseTrackingService
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.codec import FrameEncodingError, JpegFrameEncoder
from gripper_ai_controller.web.service import CameraPreviewService


class FakeVisionAdapter(FrameDispatchingVisionAdapter):
    """Provide controlled frame and lifecycle failures without importing a camera SDK."""

    def __init__(
        self,
        frames: Optional[List[ImageFrame]] = None,
        startup_failures: int = 0,
        capture_failures: int = 0,
    ) -> None:
        """Create a fake adapter with deterministic retry behavior for preview tests."""

        super().__init__()
        self.frames = frames or [make_rgb_frame()]
        self.startup_failures = startup_failures
        self.capture_failures = capture_failures
        self.startup_calls = 0
        self.shutdown_calls = 0
        self.capture_calls = 0

    async def on_startup(self) -> None:
        """Fail selected startup attempts to exercise the service retry boundary."""

        self.startup_calls += 1
        if self.startup_calls <= self.startup_failures:
            raise RuntimeError("fake camera is unavailable")

    async def on_shutdown(self) -> None:
        """Record resource cleanup without retaining any capture data."""

        self.shutdown_calls += 1

    async def capture(self) -> ImageFrame:
        """Return a configured frame or fail before notifying observers."""

        self.ensure_started()
        self.capture_calls += 1
        if self.capture_calls <= self.capture_failures:
            raise RuntimeError("fake frame acquisition failed")
        frame = self.frames[(self.capture_calls - self.capture_failures - 1) % len(self.frames)]
        await self.emit_frame(frame)
        return frame


class FakePoseEstimator(PoseEstimator):
    """Return deterministic pose data without importing Torch or allocating a GPU."""

    def __init__(self, target_confidence: float = 0.95) -> None:
        """Allow tests to model a detected person whose selected joint is unavailable."""

        self.target_confidence = target_confidence

    def infer(self, frame: ImageFrame):
        """Create a complete high-confidence COCO person for the captured test frame."""

        del frame
        joints = tuple(
            PoseJoint2D(
                name,
                1.0,
                0.0,
                0.1 + index * 0.01,
                0.5,
                self.target_confidence if name == "right_wrist" else 0.95,
            )
            for index, name in enumerate(COCO_KEYPOINT_NAMES)
        )
        return (PoseCandidate(BoundingBox2D(0.1, 0.1, 0.5, 0.7), 0.95, joints),)


class MovingFakePoseEstimator(PoseEstimator):
    """Return one moving wrist position from fixture frame timestamps without CUDA."""

    def infer(self, frame: ImageFrame):
        """Create a complete candidate whose locked wrist advances on the later test frame."""

        wrist_x = 0.10 if frame.captured_at < 2.0 else 0.30
        joints = []
        for index, name in enumerate(COCO_KEYPOINT_NAMES):
            normalized_x = wrist_x if name == "right_wrist" else 0.1 + index * 0.01
            joints.append(PoseJoint2D(name, normalized_x * 100.0, 50.0, normalized_x, 0.5, 0.95))
        return (PoseCandidate(BoundingBox2D(0.1, 0.1, 0.5, 0.7), 0.95, tuple(joints)),)


def make_rgb_frame(captured_at: Optional[float] = None) -> ImageFrame:
    """Return a small valid RGB8 frame suitable for lossless contract assertions."""

    return ImageFrame(
        camera_id="camera-a",
        captured_at=time.time() if captured_at is None else captured_at,
        frame_reference="test://camera-a/frame",
        calibration_id="test-calibration",
        healthy=True,
        pixel_payload=b"\x10\x20\x30\x40\x50\x60",
        width=2,
        height=1,
        pixel_format="rgb8",
    )


class JpegFrameEncoderTests(unittest.TestCase):
    """Verify normalized pixel payloads become browser-ready JPEG without file output."""

    def test_encodes_rgb8_and_mono8_frames(self):
        encoder = JpegFrameEncoder(80)
        mono_frame = ImageFrame(
            "camera-a",
            1.0,
            "test://mono",
            "test-calibration",
            True,
            b"\x00\xff",
            2,
            1,
            "mono8",
        )

        for frame in (make_rgb_frame(), mono_frame):
            payload = encoder.encode(frame)
            self.assertTrue(payload.startswith(b"\xff\xd8"))
            with Image.open(BytesIO(payload)) as image:
                self.assertEqual((2, 1), image.size)

    def test_rejects_malformed_payload_and_unknown_pixel_format(self):
        encoder = JpegFrameEncoder(80)
        malformed = ImageFrame(
            "camera-a", 1.0, "test://bad", "test-calibration", True, b"\x00", 2, 1, "rgb8"
        )
        unsupported = ImageFrame(
            "camera-a", 1.0, "test://bad", "test-calibration", True, b"\x00", 1, 1, "bgr8"
        )

        with self.assertRaises(FrameEncodingError):
            encoder.encode(malformed)
        with self.assertRaises(FrameEncodingError):
            encoder.encode(unsupported)


class PreviewConfigurationTests(unittest.TestCase):
    """Verify the web graph validates settings and avoids runtime construction."""

    def test_loads_only_the_simulated_vision_adapter(self):
        preview = load_vision_preview_config("configs/development.json")

        self.assertEqual("sim-camera", preview.camera_id)
        self.assertIsInstance(preview.vision, SimulatedCameraAdapter)
        self.assertFalse(preview.vision.started)
        self.assertEqual(10, preview.settings.stream_fps)
        self.assertTrue(preview.settings.camera_controls_enabled)

    def test_disabled_gripper_template_keeps_a_read_only_simulated_adapter(self):
        preview = load_vision_preview_config("configs/gripper-web-control.example.json")

        self.assertFalse(preview.settings.gripper_controls_enabled)
        self.assertIsInstance(preview.gripper, SimulatedGripperAdapter)
        self.assertFalse(preview.gripper.started)
        self.assertEqual("sim-primary", preview.gripper_control_settings.target_name)

    def test_rejects_unknown_or_invalid_web_settings(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "web": {"stream_fps": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid-preview.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stream_fps"):
                load_vision_preview_config(str(config_path))

            payload["web"] = {"unexpected": True}
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported web settings"):
                load_vision_preview_config(str(config_path))

            payload["web"] = {"frontend_dist_dir": "../outside"}
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relative directory"):
                load_vision_preview_config(str(config_path))

            payload["web"] = {"camera_controls_enabled": "yes"}
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "camera_controls_enabled"):
                load_vision_preview_config(str(config_path))

    def test_gripper_control_requires_loopback_and_a_primary_target(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "targets": [
                {
                    "name": "sim-primary",
                    "role": "primary",
                    "robot_adapter": "simulated-robot",
                    "gripper_adapter": "simulated-gripper",
                }
            ],
            "gripper_control": {
                "target_name": "sim-primary",
                "open_position": 900,
                "close_position": 100,
            },
            "web": {"bind_host": "0.0.0.0", "gripper_controls_enabled": True},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "gripper-preview.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "127.0.0.1"):
                load_vision_preview_config(str(config_path))

            payload["web"]["bind_host"] = "127.0.0.1"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            preview = load_vision_preview_config(str(config_path))
            self.assertEqual("sim-primary", preview.gripper_control_settings.target_name)
            self.assertEqual(900, preview.gripper_control_settings.open_position)
            self.assertFalse(preview.gripper.started)

            payload["targets"][0]["role"] = "mirror"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "primary target"):
                load_vision_preview_config(str(config_path))

    def test_jaka_control_is_only_wired_when_the_config_declares_it(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "targets": [
                {
                    "name": "jaka-primary",
                    "role": "primary",
                    "robot_adapter": "jaka-dry-run-robot",
                    "gripper_adapter": "simulated-gripper",
                }
            ],
            "web": {"bind_host": "127.0.0.1", "camera_controls_enabled": True},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "jaka-preview.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            preview = load_vision_preview_config(str(config_path))
            self.assertIsNone(preview.jaka)
            self.assertIsNone(preview.jaka_control_settings)
            self.assertFalse(preview.settings.jaka_controls_enabled)

            payload["web"]["jaka_controls_enabled"] = True
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "jaka_control is required"):
                load_vision_preview_config(str(config_path))

            payload["jaka_control"] = {"target_name": "jaka-primary"}
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            preview = load_vision_preview_config(str(config_path))
            self.assertIsInstance(preview.jaka, JakaDryRunRobotAdapter)
            self.assertEqual("jaka-primary", preview.jaka_control_settings.target_name)
            self.assertFalse(preview.jaka.started)

    def test_jaka_control_requires_loopback_and_a_primary_target(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "targets": [
                {
                    "name": "jaka-primary",
                    "role": "primary",
                    "robot_adapter": "jaka-dry-run-robot",
                    "gripper_adapter": "simulated-gripper",
                }
            ],
            "jaka_control": {"target_name": "jaka-primary"},
            "web": {"bind_host": "0.0.0.0", "jaka_controls_enabled": True},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "jaka-preview.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "127.0.0.1"):
                load_vision_preview_config(str(config_path))

            payload["web"]["bind_host"] = "127.0.0.1"
            payload["targets"][0]["role"] = "mirror"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "primary target"):
                load_vision_preview_config(str(config_path))

            payload["targets"][0]["role"] = "primary"
            payload["jaka_control"]["target_name"] = "missing-target"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one configured target"):
                load_vision_preview_config(str(config_path))

    def test_enabled_jaka_controls_cannot_be_exposed_by_cli_host_override(self):
        settings = WebPreviewSettings(bind_host="0.0.0.0", jaka_controls_enabled=True)
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            validate_web_settings(settings)

    def test_enabled_gripper_controls_cannot_be_exposed_by_cli_host_override(self):
        settings = WebPreviewSettings(bind_host="0.0.0.0", gripper_controls_enabled=True)
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            validate_web_settings(settings)

    def test_pgi_gripper_configuration_does_not_connect_during_preview_build(self):
        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
            "targets": [
                {
                    "name": "pgi-primary",
                    "role": "primary",
                    "robot_adapter": "simulated-robot",
                    "gripper_adapter": "pgi-tcp-gripper",
                    "gripper_adapter_settings": {"host": "test.invalid"},
                }
            ],
            "gripper_control": {
                "target_name": "pgi-primary",
                "open_position": 900,
                "close_position": 100,
            },
            "web": {"bind_host": "127.0.0.1", "gripper_controls_enabled": True},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "pgi-preview.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            preview = load_vision_preview_config(str(config_path))

        self.assertIsInstance(preview.gripper, PgiTcpGripperAdapter)
        self.assertFalse(preview.gripper.started)

    def test_enabled_pose_preview_blocks_before_camera_start_when_cuda_is_not_ready(self):
        """Prevent an enabled model pipeline from retrying frames without a supported CUDA runtime."""

        preview = VisionPreviewConfig(
            "camera-a",
            FakeVisionAdapter(),
            WebPreviewSettings(),
            pose_settings=PoseTrackingSettings(enabled=True),
        )
        unavailable = GpuPreflightResult(
            None,
            None,
            None,
            False,
            False,
            None,
            None,
            False,
            None,
            False,
            "No NVIDIA driver compatible with CUDA 11.7 was detected.",
        )
        with patch("gripper_ai_controller.web.app.inspect_cuda_gpu", return_value=unavailable):
            with self.assertRaisesRegex(RuntimeError, "CUDA pose inference is not ready"):
                create_web_app(preview)


class CameraPreviewServiceTests(unittest.TestCase):
    """Verify camera retry and one-frame fan-out without a network listener or hardware."""

    def run_async(self, coroutine):
        """Run one isolated asyncio scenario under Python 3.7's standard library."""

        return asyncio.run(coroutine)

    def test_retries_unavailable_camera_then_publishes_one_frame(self):
        async def scenario():
            adapter = FakeVisionAdapter(startup_failures=1, capture_failures=1)
            service = CameraPreviewService(
                "camera-a",
                adapter,
                WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01),
            )
            await service.startup()
            try:
                await wait_for_frame(service)
                status = await service.hub.status()
                self.assertEqual("streaming", status.state)
                self.assertIsNotNone(status.latest_frame_at)
                self.assertGreaterEqual(adapter.startup_calls, 3)
                self.assertGreaterEqual(adapter.shutdown_calls, 1)
            finally:
                await service.shutdown()

        self.run_async(scenario())

    def test_two_mjpeg_consumers_read_one_cached_capture(self):
        async def scenario():
            adapter = FakeVisionAdapter()
            service = CameraPreviewService(
                "camera-a",
                adapter,
                WebPreviewSettings(stream_fps=1, capture_retry_seconds=0.01),
            )
            application = create_web_app_from_service(service)
            await service.startup()
            try:
                await wait_for_frame(service)
                first_status, first_type, first_chunk = await request_first_stream_chunk(
                    application, "/api/cameras/camera-a/stream"
                )
                second_status, second_type, second_chunk = await request_first_stream_chunk(
                    application, "/api/cameras/camera-a/stream"
                )
                self.assertEqual(200, first_status)
                self.assertEqual(200, second_status)
                self.assertIn(b"multipart/x-mixed-replace", first_type)
                self.assertIn(b"multipart/x-mixed-replace", second_type)
                self.assertIn(b"Content-Type: image/jpeg", first_chunk)
                self.assertIn(b"Content-Type: image/jpeg", second_chunk)
                self.assertEqual(1, adapter.capture_calls)
            finally:
                await service.shutdown()

        self.run_async(scenario())

    def test_pose_preview_marks_an_old_model_result_stale_without_stopping_mjpeg(self):
        """Expose a strict overlay freshness flag while the frame hub remains independently live."""

        async def scenario():
            frame = make_rgb_frame(captured_at=1.0)
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(
                    enabled=True,
                    max_inference_fps=30,
                    overlay_max_frame_lag_seconds=0.35,
                ),
                FakePoseEstimator(),
            )
            service = CameraPreviewService(
                "camera-a",
                FakeVisionAdapter(),
                WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01),
                pose_tracking_service=tracker,
            )
            try:
                await tracker.submit_frame(frame)
                for _ in range(100):
                    if (await tracker.snapshot()).captured_at == 1.0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(1.0, (await tracker.snapshot()).captured_at)
                jpeg = JpegFrameEncoder(80).encode(frame)
                await service.hub.publish(jpeg, 1.20)
                fresh = await service.get_pose_preview_snapshot()
                self.assertTrue(fresh.overlay_fresh)
                self.assertEqual(1.20, fresh.latest_frame_at)

                await service.hub.publish(jpeg, 1.40)
                stale = await service.get_pose_preview_snapshot()
                self.assertFalse(stale.overlay_fresh)
                self.assertTrue(stale.snapshot.valid)
                self.assertEqual(1.40, stale.latest_frame_at)
            finally:
                await service.shutdown()

        self.run_async(scenario())

    def test_pose_preview_keeps_a_fresh_detected_person_visible_when_target_is_unavailable(self):
        """Keep visual skeleton eligibility separate from selected-joint lock eligibility."""

        async def scenario():
            frame = make_rgb_frame(captured_at=1.0)
            tracker = PoseTrackingService(
                "camera-a",
                PoseTrackingSettings(
                    enabled=True,
                    max_inference_fps=30,
                    overlay_max_frame_lag_seconds=0.35,
                ),
                FakePoseEstimator(target_confidence=0.1),
            )
            service = CameraPreviewService(
                "camera-a",
                FakeVisionAdapter(),
                WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01),
                pose_tracking_service=tracker,
            )
            try:
                await tracker.submit_frame(frame)
                for _ in range(100):
                    snapshot = await tracker.snapshot()
                    if snapshot.captured_at == 1.0:
                        break
                    await asyncio.sleep(0.01)
                snapshot = await tracker.snapshot()
                self.assertFalse(snapshot.valid)
                self.assertIsNotNone(snapshot.person)

                await service.hub.publish(JpegFrameEncoder(80).encode(frame), 1.20)
                preview = await service.get_pose_preview_snapshot()
                self.assertFalse(preview.snapshot.valid)
                self.assertTrue(preview.overlay_fresh)
            finally:
                await service.shutdown()

        self.run_async(scenario())


class CameraPreviewHttpTests(unittest.TestCase):
    """Verify documented HTTP responses through FastAPI with fake camera resources."""

    def test_status_snapshot_unknown_camera_and_jpeg_response(self):
        adapter = FakeVisionAdapter()
        application = create_preview_application(adapter)

        with TestClient(application) as client:
            self.assertEqual(200, client.get("/api/cameras").status_code)
            ready = wait_for_http_frame(client, "/api/cameras/camera-a/frame")
            self.assertEqual("image/jpeg", ready.headers["content-type"])
            self.assertNotIn("x-frame-sequence", ready.headers)
            self.assertNotIn("frame_sequence", client.get("/api/cameras/camera-a/status").json())
            self.assertEqual(200, client.get("/api/cameras/camera-a/status").status_code)

            analysis = client.get("/api/cameras/camera-a/vision/analysis")
            self.assertEqual(200, analysis.status_code)
            self.assertFalse(analysis.json()["pose_enabled"])
            self.assertEqual(0, analysis.json()["person_count"])
            self.assertEqual("rgb8", analysis.json()["frame"]["pixel_format"])

            unknown = client.get("/api/cameras/missing/status")
            self.assertEqual(404, unknown.status_code)
            self.assertEqual("camera_not_found", unknown.json()["code"])
            self.assertEqual(404, client.get("/api/cameras/missing/frame").status_code)
            self.assertEqual(404, client.get("/api/cameras/missing/stream").status_code)
            self.assertEqual(404, client.get("/api/cameras/missing/vision/analysis").status_code)

    def test_unavailable_camera_returns_normalized_503(self):
        adapter = FakeVisionAdapter(startup_failures=100)
        application = create_preview_application(adapter)

        with TestClient(application) as client:
            unavailable = wait_for_http_status(client, "/api/cameras/camera-a/status", "degraded")
            self.assertEqual("camera_unavailable", unavailable.json()["error"]["code"])
            frame = client.get("/api/cameras/camera-a/frame")
            self.assertEqual(503, frame.status_code)
            self.assertEqual(
                {"code": "camera_unavailable", "message": "No browser-ready camera frame is available yet."},
                frame.json(),
            )

    def test_malformed_frame_degrades_the_preview_and_does_not_create_a_snapshot(self):
        malformed = ImageFrame(
            "camera-a", 1.0, "test://malformed", "test-calibration", True, b"\x00", 2, 1, "rgb8"
        )
        application = create_preview_application(FakeVisionAdapter(frames=[malformed]))

        with TestClient(application) as client:
            status = wait_for_http_status(client, "/api/cameras/camera-a/status", "degraded")
            self.assertEqual("camera_unavailable", status.json()["error"]["code"])
            self.assertEqual(503, client.get("/api/cameras/camera-a/frame").status_code)

    def test_parameter_endpoints_apply_live_and_restart_updates_with_simulated_camera(self):
        application = create_preview_application(SimulatedCameraAdapter(), camera_controls_enabled=True)

        with TestClient(application) as client:
            wait_for_http_frame(client, "/api/cameras/camera-a/frame")
            parameters = client.get("/api/cameras/camera-a/parameters")
            self.assertEqual(200, parameters.status_code)
            self.assertTrue(parameters.json()["write_enabled"])
            self.assertIn("exposure_time_us", [item["key"] for item in parameters.json()["parameters"]])

            live = client.patch(
                "/api/cameras/camera-a/parameters/exposure_time_us",
                json={"value": 12000.0},
            )
            self.assertEqual(200, live.status_code)
            self.assertFalse(live.json()["restarted_acquisition"])
            exposure = next(item for item in live.json()["parameters"] if item["key"] == "exposure_time_us")
            self.assertEqual(12000.0, exposure["value"])

            restarted = client.post(
                "/api/cameras/camera-a/parameters/apply",
                json={"values": {"pixel_format": "Mono8"}},
            )
            self.assertEqual(200, restarted.status_code)
            self.assertTrue(restarted.json()["restarted_acquisition"])
            pixel_format = next(item for item in restarted.json()["parameters"] if item["key"] == "pixel_format")
            self.assertEqual("Mono8", pixel_format["value"])

    def test_parameter_writes_require_local_configuration_and_correct_apply_mode(self):
        application = create_preview_application(SimulatedCameraAdapter(), camera_controls_enabled=False)

        with TestClient(application) as client:
            parameters = client.get("/api/cameras/camera-a/parameters")
            self.assertEqual(200, parameters.status_code)
            self.assertFalse(parameters.json()["write_enabled"])

            disabled = client.patch(
                "/api/cameras/camera-a/parameters/exposure_time_us",
                json={"value": 12000.0},
            )
            self.assertEqual(403, disabled.status_code)
            self.assertEqual("camera_controls_disabled", disabled.json()["code"])

        enabled_application = create_preview_application(SimulatedCameraAdapter(), camera_controls_enabled=True)
        with TestClient(enabled_application) as client:
            wait_for_http_frame(client, "/api/cameras/camera-a/frame")
            wrong_mode = client.patch(
                "/api/cameras/camera-a/parameters/pixel_format",
                json={"value": "Mono8"},
            )
            self.assertEqual(422, wrong_mode.status_code)
            self.assertEqual("invalid_camera_parameter", wrong_mode.json()["code"])

            invalid_value = client.patch(
                "/api/cameras/camera-a/parameters/gain_db",
                json={"value": 100.0},
            )
            self.assertEqual(422, invalid_value.status_code)
            self.assertEqual("invalid_camera_parameter", invalid_value.json()["code"])

            malformed = client.post("/api/cameras/camera-a/parameters/apply", json={})
            self.assertEqual(422, malformed.status_code)
            self.assertEqual("invalid_request", malformed.json()["code"])

            unavailable = client.get("/api/cameras/camera-a/parameters")
            self.assertEqual(200, unavailable.status_code)

            unknown = client.get("/api/cameras/missing/parameters")
            self.assertEqual(404, unknown.status_code)

    def test_pose_endpoints_report_disabled_tracking_and_preserve_unknown_camera_contract(self):
        """Expose a safe unavailable pose state without constructing a CUDA estimator."""

        application = create_preview_application(FakeVisionAdapter())
        with TestClient(application) as client:
            disabled = client.get("/api/cameras/camera-a/pose")
            self.assertEqual(200, disabled.status_code)
            self.assertFalse(disabled.json()["enabled"])
            self.assertFalse(disabled.json()["valid"])
            self.assertEqual("right_wrist", disabled.json()["target_joint"])

            update = client.put(
                "/api/cameras/camera-a/pose/target",
                json={"target_joint": "left_wrist"},
            )
            self.assertEqual(409, update.status_code)
            self.assertEqual("pose_tracking_disabled", update.json()["code"])

            unknown = client.get("/api/cameras/missing/pose")
            self.assertEqual(404, unknown.status_code)
            self.assertEqual("camera_not_found", unknown.json()["code"])

            unavailable_frame = client.get(
                "/api/cameras/camera-a/pose/frame?captured_at=1.0"
            )
            self.assertEqual(503, unavailable_frame.status_code)
            self.assertEqual("pose_frame_unavailable", unavailable_frame.json()["code"])
            self.assertEqual(404, client.get("/api/cameras/missing/pose/frame?captured_at=1.0").status_code)

    def test_pose_target_is_persisted_and_returned_without_motion_control(self):
        """Exercise valid pose delivery and target selection through the public HTTP boundary."""

        with tempfile.TemporaryDirectory() as directory:
            localstore = Path(directory) / "localstore"
            localstore.mkdir()
            config_path = localstore / "preview.json"
            config_path.write_text(
                json.dumps(
                    {
                        "camera": {"camera_id": "camera-a"},
                        "components": {"vision": "test-vision", "vision_adapter_settings": {}},
                        "pose": {"enabled": True, "target_joint": "right_wrist"},
                    }
                ),
                encoding="utf-8",
            )
            adapter = FakeVisionAdapter()
            pose_settings = PoseTrackingSettings(enabled=True, max_inference_fps=30)
            tracker = PoseTrackingService("camera-a", pose_settings, FakePoseEstimator())
            service = CameraPreviewService(
                "camera-a",
                adapter,
                WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01),
                pose_tracking_service=tracker,
                pose_target_store=PoseTargetConfigStore(str(config_path), "camera-a", "test-vision", {}),
            )
            preview = VisionPreviewConfig(
                "camera-a",
                adapter,
                service.settings,
                config_file=str(config_path),
                vision_name="test-vision",
                pose_settings=pose_settings,
            )
            ready = GpuPreflightResult(
                "Fake RTX",
                "610.62",
                "8.9",
                True,
                True,
                "1.13.1+cu117",
                "11.7",
                True,
                "Fake RTX",
                True,
                "CUDA 11.7 Torch runtime and NVIDIA GPU are ready for pose inference.",
            )
            with patch("gripper_ai_controller.web.app.inspect_cuda_gpu", return_value=ready):
                application = create_web_app(preview, preview_service=service)

            with TestClient(application) as client:
                pose = wait_for_http_pose(client, "/api/cameras/camera-a/pose")
                self.assertTrue(pose.json()["enabled"])
                self.assertTrue(pose.json()["valid"])
                self.assertEqual("right_wrist", pose.json()["target"]["name"])
                self.assertIn("motion", pose.json())

                pose_frame = wait_for_http_pose_frame(
                    client,
                    "/api/cameras/camera-a/pose/frame?captured_at={0}".format(
                        pose.json()["captured_at"]
                    ),
                )
                self.assertEqual("image/jpeg", pose_frame.headers["content-type"])
                self.assertEqual("no-store", pose_frame.headers["cache-control"])
                self.assertEqual(str(pose.json()["captured_at"]), pose_frame.headers["x-camera-captured-at"])
                with Image.open(BytesIO(pose_frame.content)) as image:
                    self.assertEqual((2, 1), image.size)

                analysis = wait_for_http_analysis(client, "/api/cameras/camera-a/vision/analysis")
                self.assertTrue(analysis.json()["pose_enabled"])
                self.assertEqual(1, analysis.json()["person_count"])
                self.assertTrue(analysis.json()["persons"][0]["selected"])
                self.assertIn("right_wrist", analysis.json()["visible_joint_names"])

                changed = client.put(
                    "/api/cameras/camera-a/pose/target",
                    json={"target_joint": "left_elbow"},
                )
                self.assertEqual(200, changed.status_code)
                self.assertTrue(changed.json()["valid"])
                self.assertEqual("left_elbow", changed.json()["target_joint"])
                self.assertEqual("left_elbow", changed.json()["target"]["name"])

                rejected = client.put(
                    "/api/cameras/camera-a/pose/target",
                    json={"target_joint": "not-a-joint"},
                )
                self.assertEqual(422, rejected.status_code)
                self.assertEqual("invalid_pose_target", rejected.json()["code"])

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("left_elbow", persisted["pose"]["target_joint"])

    def test_pose_endpoint_exposes_passive_motion_for_consecutive_valid_frames(self):
        """Confirm the public endpoint serializes a moving 2D target without a control path."""

        adapter = FakeVisionAdapter([make_rgb_frame(1.0), make_rgb_frame(2.0)])
        pose_settings = PoseTrackingSettings(
            enabled=True,
            max_inference_fps=30,
            motion_speed_threshold=0.05,
        )
        tracker = PoseTrackingService("camera-a", pose_settings, MovingFakePoseEstimator())
        service = CameraPreviewService(
            "camera-a",
            adapter,
            WebPreviewSettings(stream_fps=5, capture_retry_seconds=0.01),
            pose_tracking_service=tracker,
        )
        preview = VisionPreviewConfig("camera-a", adapter, service.settings, pose_settings=pose_settings)
        ready = GpuPreflightResult(
            "Fake RTX",
            "610.62",
            "8.9",
            True,
            True,
            "1.13.1+cu117",
            "11.7",
            True,
            "Fake RTX",
            True,
            "CUDA 11.7 Torch runtime and NVIDIA GPU are ready for pose inference.",
        )
        with patch("gripper_ai_controller.web.app.inspect_cuda_gpu", return_value=ready):
            application = create_web_app(preview, preview_service=service)

        with TestClient(application) as client:
            motion = wait_for_http_motion(client, "/api/cameras/camera-a/pose")
            self.assertEqual("right_wrist", motion["target_joint"])
            self.assertAlmostEqual(0.20, motion["delta_x"])
            self.assertTrue(motion["moving"])


def create_preview_application(adapter: VisionAdapter, camera_controls_enabled: bool = False):
    """Create an app that uses a fake adapter and never creates runtime targets."""

    preview = VisionPreviewConfig(
        "camera-a",
        adapter,
        WebPreviewSettings(
            stream_fps=30,
            capture_retry_seconds=0.01,
            camera_controls_enabled=camera_controls_enabled,
        ),
    )
    return create_web_app(preview)


def create_web_app_from_service(service: CameraPreviewService):
    """Create a route graph for direct ASGI stream testing with an existing service."""

    preview = VisionPreviewConfig(service.camera_id, service.vision, service.settings)
    return create_web_app(preview, preview_service=service)


async def wait_for_frame(service: CameraPreviewService) -> None:
    """Wait briefly for a background capture without tracking an image sequence number."""

    for _ in range(100):
        status = await service.hub.status()
        if status.latest_frame_at is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("The fake camera did not publish a frame in time.")


def wait_for_http_frame(client: TestClient, path: str):
    """Poll a snapshot endpoint until the preview service has encoded its first frame."""

    for _ in range(100):
        response = client.get(path)
        if response.status_code == 200:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not produce a JPEG frame in time.")


def wait_for_http_pose_frame(client: TestClient, path: str):
    """Poll the bounded pose-aligned frame resource until its matching JPEG is available."""

    for _ in range(100):
        response = client.get(path)
        if response.status_code == 200:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not retain the pose-aligned JPEG frame in time.")


def wait_for_http_status(client: TestClient, path: str, expected_state: str):
    """Poll camera status until asynchronous retry state is visible through HTTP."""

    for _ in range(100):
        response = client.get(path)
        if response.json().get("state") == expected_state:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not report the expected state in time.")


def wait_for_http_pose(client: TestClient, path: str):
    """Poll pose state until the background estimator publishes one valid target."""

    for _ in range(100):
        response = client.get(path)
        if response.status_code == 200 and response.json().get("valid"):
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not produce a valid pose snapshot in time.")


def wait_for_http_motion(client: TestClient, path: str):
    """Poll pose metadata until two valid fake frames produce a movement observation."""

    for _ in range(100):
        response = client.get(path)
        motion = response.json().get("motion") if response.status_code == 200 else None
        if motion is not None:
            return motion
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not produce a motion observation in time.")


def wait_for_http_analysis(client: TestClient, path: str):
    """Poll cached analysis until the existing fake inference exposes one person box."""

    for _ in range(100):
        response = client.get(path)
        if response.status_code == 200 and response.json().get("person_count") == 1:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not produce a person analysis result in time.")


async def request_first_stream_chunk(application, path: str):
    """Invoke one ASGI MJPEG request until its first body chunk then disconnect cleanly."""

    request_sent = False
    disconnect = asyncio.Event()
    first_chunk_available = asyncio.Event()
    chunks = []
    response_status = None
    response_headers = []

    async def receive():
        """Supply one request event and then simulate a browser closing after first data."""

        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        """Capture the first multipart body chunk emitted by StreamingResponse."""

        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = message["headers"]
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            first_chunk_available.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    task = asyncio.create_task(application(scope, receive, send))
    try:
        await asyncio.wait_for(first_chunk_available.wait(), timeout=1.0)
    finally:
        disconnect.set()
        await asyncio.wait_for(task, timeout=1.0)
    content_type = dict(response_headers).get(b"content-type", b"")
    return response_status, content_type, chunks[0]


if __name__ == "__main__":
    unittest.main()
