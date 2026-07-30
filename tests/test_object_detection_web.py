"""HTTP contract tests for the passive generic detection endpoint."""

import unittest

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.plugin_host import PluginFactoryDescriptor, PluginHost
from gripper_ai_controller.domain.models import BoundingBox2D, ComponentManifest, ImageFrame
from gripper_ai_controller.object_detection.models import DetectionCandidate, DetectionModelProfile
from gripper_ai_controller.object_detection.tracker import ObjectDetectionTrackingSnapshot
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.service import CameraPreviewService


class DetectionEndpointVisionAdapter(FrameDispatchingVisionAdapter):
    """In-memory adapter used to prove detection routes do not capture on read."""

    def __init__(self):
        super().__init__()
        self.capture_calls = 0

    async def capture(self):
        self.ensure_started()
        self.capture_calls += 1
        return ImageFrame("camera-a", 1.0, None, None, True, b"\x00", 1, 1, "mono8")


class SnapshotOnlyDetectionPlugin(Plugin):
    """Expose a static cache and a model switch without any hardware surface."""

    manifest = ComponentManifest(
        "object-detection-analysis",
        "test",
        "plugin",
        ("frame-observer", "object-detection"),
    )
    ui_kind = "object-detection-analysis"

    def __init__(self):
        self.started = False
        self.snapshot_calls = 0
        self.selected_model_id = "model-a"
        self.profiles = (
            DetectionModelProfile("model-a", "模型 A", "test-provider", "pyproject.toml"),
            DetectionModelProfile("model-b", "模型 B", "test-provider", "pyproject.toml"),
            DetectionModelProfile(
                "model-missing",
                "缺失模型",
                "test-provider",
                "localstore/models/missing-detection-model.pth",
            ),
        )

    async def startup(self):
        self.started = True

    async def shutdown(self):
        self.started = False

    async def handle_event(self, event):
        pass

    async def detection_snapshot(self):
        self.snapshot_calls += 1
        return ObjectDetectionTrackingSnapshot(
            "camera-a",
            True,
            self.selected_model_id,
            "test-provider",
            1.0,
            True,
            "detection_available",
            3.5,
            (
                DetectionCandidate(
                    "wrench", 0.91, BoundingBox2D(0.2, 0.25, 0.3, 0.4), 7
                ),
            ),
        )

    def model_profiles(self):
        return self.profiles

    async def select_model(self, model_id):
        self.selected_model_id = model_id
        return await self.detection_snapshot()


def detection_plugin_factory():
    return SnapshotOnlyDetectionPlugin()


class ObjectDetectionHttpTests(unittest.TestCase):
    """Verify cache-only reads, model selection, and safe empty responses."""

    def test_detections_endpoint_returns_boxes_and_model_catalog(self):
        adapter = DetectionEndpointVisionAdapter()
        host = PluginHost(
            (
                PluginFactoryDescriptor(
                    "object-detection-analysis", __name__, "detection_plugin_factory"
                ),
            )
        )
        service = CameraPreviewService(
            "camera-a", adapter, WebPreviewSettings(stream_fps=1, capture_retry_seconds=0.01), plugin_host=host
        )
        preview = VisionPreviewConfig("camera-a", adapter, service.settings)
        application = create_web_app(preview, preview_service=service)

        with TestClient(application) as client:
            capture_calls_before = adapter.capture_calls
            response = client.get("/api/cameras/camera-a/detections")
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertTrue(payload["valid"])
            self.assertEqual("model-a", payload["selected_model_id"])
            self.assertEqual(3, len(payload["models"]))
            self.assertFalse(payload["models"][2]["available"])
            self.assertEqual("wrench", payload["detections"][0]["label"])
            self.assertEqual(7, payload["detections"][0]["class_id"])
            self.assertNotIn("command", payload["detections"][0])
            self.assertEqual(capture_calls_before, adapter.capture_calls)

            switched = client.put(
                "/api/cameras/camera-a/detections/model-selection",
                json={"model_id": "model-b"},
            )
            self.assertEqual(200, switched.status_code)
            self.assertEqual("model-b", switched.json()["selected_model_id"])

            unknown_model = client.put(
                "/api/cameras/camera-a/detections/model-selection",
                json={"model_id": "unknown-model"},
            )
            self.assertEqual(404, unknown_model.status_code)
            self.assertEqual("detection_model_not_found", unknown_model.json()["code"])

            missing_model = client.put(
                "/api/cameras/camera-a/detections/model-selection",
                json={"model_id": "model-missing"},
            )
            self.assertEqual(409, missing_model.status_code)
            self.assertEqual("detection_model_unavailable", missing_model.json()["code"])

            missing = client.get("/api/cameras/missing/detections")
            self.assertEqual(404, missing.status_code)
            self.assertEqual("camera_not_found", missing.json()["code"])

    def test_disabled_detection_endpoint_is_safe_empty_result(self):
        adapter = DetectionEndpointVisionAdapter()
        service = CameraPreviewService(
            "camera-a", adapter, WebPreviewSettings(stream_fps=1, capture_retry_seconds=0.01)
        )
        preview = VisionPreviewConfig("camera-a", adapter, service.settings)
        application = create_web_app(preview, preview_service=service)

        with TestClient(application) as client:
            response = client.get("/api/cameras/camera-a/detections")
            selection = client.put(
                "/api/cameras/camera-a/detections/model-selection",
                json={"model_id": "model-a"},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertEqual("object_detection_disabled", payload["reason"])
        self.assertEqual([], payload["models"])
        self.assertEqual([], payload["detections"])
        self.assertEqual(409, selection.status_code)
        self.assertEqual("detection_model_unavailable", selection.json()["code"])


if __name__ == "__main__":
    unittest.main()
