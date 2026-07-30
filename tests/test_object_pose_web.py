"""HTTP tests for the passive known-workpiece cache endpoint."""

import unittest

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.plugin_host import PluginFactoryDescriptor, PluginHost
from gripper_ai_controller.domain.models import BoundingBox2D, ComponentManifest, ImageFrame
from gripper_ai_controller.object_pose.models import NormalizedPoint2D, PixelPoint2D
from gripper_ai_controller.object_pose.tracker import (
    ObjectPoseTrackingSnapshot,
    OrientationRpy,
    WorkpiecePose,
)
from gripper_ai_controller.calibration.models import Point3D
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.service import CameraPreviewService


class ObjectEndpointVisionAdapter(FrameDispatchingVisionAdapter):
    """Return a fixed in-memory image while exposing no camera SDK or control surface."""

    def __init__(self):
        super().__init__()
        self.capture_calls = 0

    async def capture(self):
        self.ensure_started()
        self.capture_calls += 1
        return ImageFrame(
            "camera-a",
            1.0,
            "test://object-pose",
            "calibration-a",
            True,
            b"\x10\x20\x30\x40\x50\x60",
            2,
            1,
            "rgb8",
        )


class SnapshotOnlyObjectPlugin(Plugin):
    """Expose a static cache to prove the route does not trigger detector work."""

    manifest = ComponentManifest("object-pose-analysis", "test", "plugin", ("frame-observer",))
    ui_kind = "object-pose-analysis"

    def __init__(self):
        self.started = False
        self.object_snapshot_calls = 0
        self.frame_events = 0

    async def startup(self):
        self.started = True

    async def shutdown(self):
        self.started = False

    async def handle_event(self, event):
        self.frame_events += 1

    async def object_snapshot(self):
        self.object_snapshot_calls += 1
        return ObjectPoseTrackingSnapshot(
            "camera-a",
            True,
            1.0,
            True,
            "object_pose_available",
            2.5,
            (
                WorkpiecePose(
                    "known-workpiece-v1",
                    0.91,
                    (
                        NormalizedPoint2D(0.25, 0.2),
                        NormalizedPoint2D(0.75, 0.2),
                        NormalizedPoint2D(0.75, 0.8),
                    ),
                    BoundingBox2D(0.25, 0.2, 0.5, 0.6),
                    PixelPoint2D(1.0, 0.5),
                    NormalizedPoint2D(0.5, 0.5),
                    "jaka_base",
                    Point3D(100.0, -20.0, 30.0),
                    OrientationRpy(0.0, 0.0, 0.25),
                    ("x", "y", "yaw"),
                    ("z", "roll", "pitch"),
                    3.141592653589793,
                    True,
                    "orientation_pi_ambiguous",
                ),
            ),
        )


def object_snapshot_plugin_factory():
    """Return a fresh cache-only test plugin for the fixed trusted host descriptor."""

    return SnapshotOnlyObjectPlugin()


class ObjectPoseHttpTests(unittest.TestCase):
    """Verify the endpoint exposes cached planar results without device operations."""

    def test_objects_endpoint_serializes_cached_result_and_preserves_unknown_camera_contract(self):
        """The route only delegates to the trusted cache plugin and never offers a command field."""

        adapter = ObjectEndpointVisionAdapter()
        host = PluginHost(
            (
                PluginFactoryDescriptor(
                    "object-pose-analysis",
                    __name__,
                    "object_snapshot_plugin_factory",
                ),
            )
        )
        service = CameraPreviewService(
            "camera-a",
            adapter,
            WebPreviewSettings(stream_fps=1, capture_retry_seconds=0.01),
            plugin_host=host,
        )
        preview = VisionPreviewConfig("camera-a", adapter, service.settings)
        application = create_web_app(preview, preview_service=service)
        with TestClient(application) as client:
            response = client.get("/api/cameras/camera-a/objects")
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertTrue(payload["enabled"])
            self.assertTrue(payload["valid"])
            self.assertEqual("object_pose_available", payload["reason"])
            self.assertEqual(1, len(payload["objects"]))
            object_pose = payload["objects"][0]
            self.assertEqual("jaka_base", object_pose["coordinate_frame"])
            self.assertEqual({"x": 100.0, "y": -20.0, "z": 30.0}, object_pose["translation_mm"])
            self.assertEqual(["x", "y", "yaw"], object_pose["observed_dof"])
            self.assertEqual(["z", "roll", "pitch"], object_pose["derived_dof"])
            self.assertNotIn("grasp", object_pose)
            self.assertNotIn("command", object_pose)
            plugin = host.registry.components["object-pose-analysis"]
            self.assertEqual(1, plugin.object_snapshot_calls)

            missing = client.get("/api/cameras/missing/objects")
            self.assertEqual(404, missing.status_code)
            self.assertEqual("camera_not_found", missing.json()["code"])

    def test_disabled_objects_endpoint_is_a_safe_empty_response(self):
        """A config without the object plugin returns status metadata rather than a 404 or capture request."""

        adapter = ObjectEndpointVisionAdapter()
        service = CameraPreviewService(
            "camera-a",
            adapter,
            WebPreviewSettings(stream_fps=1, capture_retry_seconds=0.01),
        )
        preview = VisionPreviewConfig("camera-a", adapter, service.settings)
        application = create_web_app(preview, preview_service=service)
        with TestClient(application) as client:
            response = client.get("/api/cameras/camera-a/objects")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("camera-a", payload["camera_id"])
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["overlay_fresh"])
        self.assertFalse(payload["valid"])
        self.assertEqual("object_pose_disabled", payload["reason"])
        self.assertIsNone(payload["inference_latency_ms"])
        self.assertEqual([], payload["objects"])
