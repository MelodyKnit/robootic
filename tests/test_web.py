"""Offline tests for the read-only FastAPI camera preview boundary."""

import asyncio
import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from fastapi.testclient import TestClient
from PIL import Image

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig, load_vision_preview_config
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.domain.models import ImageFrame
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
                await wait_for_sequence(service, 1)
                status = await service.hub.status()
                self.assertEqual("streaming", status.state)
                self.assertEqual(1, status.frame_sequence)
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
                await wait_for_sequence(service, 1)
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


class CameraPreviewHttpTests(unittest.TestCase):
    """Verify documented HTTP responses through FastAPI with fake camera resources."""

    def test_status_snapshot_unknown_camera_and_jpeg_response(self):
        adapter = FakeVisionAdapter()
        application = create_preview_application(adapter)

        with TestClient(application) as client:
            self.assertEqual(200, client.get("/api/cameras").status_code)
            ready = wait_for_http_frame(client, "/api/cameras/camera-a/frame")
            self.assertEqual("image/jpeg", ready.headers["content-type"])
            self.assertEqual(200, client.get("/api/cameras/camera-a/status").status_code)

            unknown = client.get("/api/cameras/missing/status")
            self.assertEqual(404, unknown.status_code)
            self.assertEqual("camera_not_found", unknown.json()["code"])
            self.assertEqual(404, client.get("/api/cameras/missing/frame").status_code)
            self.assertEqual(404, client.get("/api/cameras/missing/stream").status_code)

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


def create_preview_application(adapter: FakeVisionAdapter):
    """Create an app that uses a fake adapter and never creates runtime targets."""

    preview = VisionPreviewConfig(
        "camera-a", adapter, WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01)
    )
    return create_web_app(preview)


def create_web_app_from_service(service: CameraPreviewService):
    """Create a route graph for direct ASGI stream testing with an existing service."""

    preview = VisionPreviewConfig(service.camera_id, service.vision, service.settings)
    return create_web_app(preview, preview_service=service)


async def wait_for_sequence(service: CameraPreviewService, sequence: int) -> None:
    """Wait briefly for a background capture without introducing a hardware-dependent timeout."""

    for _ in range(100):
        status = await service.hub.status()
        if status.frame_sequence is not None and status.frame_sequence >= sequence:
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


def wait_for_http_status(client: TestClient, path: str, expected_state: str):
    """Poll camera status until asynchronous retry state is visible through HTTP."""

    for _ in range(100):
        response = client.get(path)
        if response.json().get("state") == expected_state:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview HTTP endpoint did not report the expected state in time.")


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
