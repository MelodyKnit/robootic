"""HTTP and configuration tests for the passive preview-plugin workbench boundary."""

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig, load_vision_preview_config
from gripper_ai_controller.configuration import (
    PoseTrackingSettings,
    VisionAnalysisSettings,
    WebPreviewSettings,
)
from gripper_ai_controller.cli import _web
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.plugin_host import PluginFactoryDescriptor, PluginHost
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame, RuntimeMode
from gripper_ai_controller.plugins.visual_pose_analysis import VisualPoseAnalysisPlugin
from gripper_ai_controller.pose.tracker import PoseTrackingService
from gripper_ai_controller.vision.analysis import VisionAnalysisService
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.app import create_web_app_factory
from gripper_ai_controller.web.service import CameraPreviewService


class PreviewPluginTestVisionAdapter(FrameDispatchingVisionAdapter):
    """Supply small in-memory RGB frames without opening a camera device."""

    def __init__(self) -> None:
        """Create a lifecycle-aware fake camera with a changing capture timestamp."""

        super().__init__()
        self._capture_count = 0

    async def capture(self) -> ImageFrame:
        """Return one valid camera frame for JPEG and passive-plugin processing."""

        self.ensure_started()
        self._capture_count += 1
        return ImageFrame(
            camera_id="camera-a",
            captured_at=time.time(),
            frame_reference="test://plugin-preview",
            calibration_id="test-calibration",
            healthy=True,
            pixel_payload=b"\x10\x20\x30\x40\x50\x60",
            width=2,
            height=1,
            pixel_format="rgb8",
        )


class ReloadablePoseTargetPlugin(Plugin):
    """Expose only the pose-target contract needed to exercise service-level serialization."""

    manifest = ComponentManifest("visual-pose-analysis", "test", "plugin", ("frame-observer",))

    def __init__(self, target_joint, created_targets):
        """Record each trusted factory target without creating a model or hardware client."""

        self.target_joint = target_joint
        self.created_targets = created_targets
        self.pose_tracking_service = object()

    async def startup(self):
        """Record the value supplied to this replacement instance."""

        self.created_targets.append(self.target_joint)

    async def shutdown(self):
        """Release no resources because this test plugin owns none."""

    async def pose_snapshot(self):
        """Provide the narrow method shape recognized by the preview service."""

        return self.target_joint

    async def analysis_snapshot(self):
        """Provide the narrow method shape recognized by the preview service."""

        return None

    async def set_target_joint(self, target_joint):
        """Apply the browser selection to this active plugin instance."""

        self.target_joint = target_joint
        return target_joint

    async def reload_factory_kwargs(self):
        """Keep the active target when the host creates a replacement instance."""

        return {"target_joint": self.target_joint}


class BlockingPoseTargetStore:
    """Hold JSON persistence long enough to expose a concurrent reload attempt."""

    def __init__(self):
        """Create deterministic synchronization events without writing a file."""

        self.started = threading.Event()
        self.release = threading.Event()
        self.persisted_targets = []

    def persist_target_joint(self, target_joint):
        """Block the service's persistence boundary until the test permits it to finish."""

        self.started.set()
        self.release.wait(timeout=1.0)
        self.persisted_targets.append(target_joint)


def reloadable_pose_target_plugin_factory(target_joint, created_targets):
    """Build a trusted reloadable plugin for the target/reload race regression test."""

    return ReloadablePoseTargetPlugin(target_joint, created_targets)


class SlowEmptyPoseEstimator:
    """Hold inference long enough to prove the host preserves its actual source JPEG."""

    def infer(self, frame):
        """Return no person after a controlled delay without loading CUDA or hardware."""

        del frame
        time.sleep(0.20)
        return ()


def retained_frame_visual_plugin_factory(camera_id):
    """Build a real passive visual plugin with a slow in-memory pose estimator."""

    pose_settings = PoseTrackingSettings(enabled=True, max_inference_fps=30)
    tracker = PoseTrackingService(camera_id, pose_settings, SlowEmptyPoseEstimator())
    analysis = VisionAnalysisService(
        camera_id,
        VisionAnalysisSettings(max_analysis_fps=30),
        pose_settings,
        tracker,
    )
    return VisualPoseAnalysisPlugin(camera_id, tracker, analysis)


class PreviewPluginConfigurationTests(unittest.TestCase):
    """Verify the preview-specific plugin configuration preserves a fail-closed reload policy."""

    def test_missing_preview_list_keeps_visual_plugin_compatible(self):
        """Older preview JSON files receive the existing visual pipeline by default."""

        preview = self._load_payload({"web": {"bind_host": "127.0.0.1"}})
        self.assertEqual(("visual-pose-analysis",), preview.preview_plugin_names)
        self.assertFalse(preview.settings.plugin_reload_enabled)

    def test_reload_requires_development_and_loopback_configuration(self):
        """Reject production or non-loopback reload requests before the server starts."""

        with self.assertRaisesRegex(ValueError, "runtime_mode"):
            self._load_payload(
                {
                    "runtime_mode": "production",
                    "web": {"bind_host": "127.0.0.1", "plugin_reload_enabled": True},
                }
            )
        with self.assertRaisesRegex(ValueError, "bind_host"):
            self._load_payload(
                {
                    "runtime_mode": "development",
                    "web": {"bind_host": "0.0.0.0", "plugin_reload_enabled": True},
                }
            )

        preview = self._load_payload(
            {
                "runtime_mode": "development",
                "components": {"plugins": {"preview": ["visual-pose-analysis"]}},
                "web": {"bind_host": "127.0.0.1", "plugin_reload_enabled": True},
            }
        )
        self.assertEqual(RuntimeMode.DEVELOPMENT, preview.runtime_mode)
        self.assertTrue(preview.settings.plugin_reload_enabled)

    def test_process_reload_cli_rejects_production_preview_configuration(self):
        """Keep Uvicorn process reload from bypassing the production restart policy."""

        preview = VisionPreviewConfig(
            camera_id="camera-a",
            vision=PreviewPluginTestVisionAdapter(),
            settings=WebPreviewSettings(),
            runtime_mode=RuntimeMode.PRODUCTION,
        )
        arguments = Namespace(
            config_file="configs/production.json",
            host=None,
            port=None,
            frontend_dist_dir=None,
            reload=True,
        )
        with patch(
            "gripper_ai_controller.bootstrap.preview_builder.load_vision_preview_config",
            return_value=preview,
        ):
            with self.assertRaisesRegex(ValueError, "runtime_mode"):
                _web(arguments)

    def test_process_reload_cli_passes_the_explicit_config_to_the_factory_environment(self):
        """Keep the Uvicorn reload child bound to the exact CLI-selected configuration."""

        preview = VisionPreviewConfig(
            camera_id="camera-a",
            vision=PreviewPluginTestVisionAdapter(),
            settings=WebPreviewSettings(),
            runtime_mode=RuntimeMode.DEVELOPMENT,
        )
        arguments = Namespace(
            config_file="configs/pose-preview.example.json",
            host=None,
            port=None,
            frontend_dist_dir=None,
            reload=True,
        )
        with patch(
            "gripper_ai_controller.bootstrap.preview_builder.load_vision_preview_config",
            return_value=preview,
        ):
            with patch("uvicorn.run") as run:
                with patch.dict(os.environ, {}, clear=True):
                    _web(arguments)
                    self.assertEqual(arguments.config_file, os.environ.get("GRIPPER_CONFIG_FILE"))
        self.assertTrue(run.called)

    def test_reload_factory_requires_the_cli_config_environment_variable(self):
        """Reject a direct factory call instead of choosing a versioned or local fallback."""

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GRIPPER_CONFIG_FILE"):
                create_web_app_factory()

    def test_reload_factory_uses_only_the_explicit_environment_configuration(self):
        """Prove the zero-argument factory forwards the CLI-selected file unchanged."""

        preview = SimpleNamespace(settings=SimpleNamespace(frontend_dist_dir="src/web/dist"))
        application = object()
        with patch.dict(os.environ, {"GRIPPER_CONFIG_FILE": "localstore/reload.local.json"}, clear=True):
            with patch(
                "gripper_ai_controller.bootstrap.preview_builder.load_vision_preview_config",
                return_value=preview,
            ) as load_config:
                with patch("gripper_ai_controller.web.app.create_web_app", return_value=application) as build_app:
                    self.assertIs(application, create_web_app_factory())
        load_config.assert_called_once_with("localstore/reload.local.json")
        build_app.assert_called_once_with(preview, "src/web/dist")

    @staticmethod
    def _load_payload(overrides):
        """Load a minimal temporary preview configuration without local hardware settings."""

        payload = {
            "camera": {"camera_id": "camera-a"},
            "components": {"vision": "simulated-camera"},
        }
        for key, value in overrides.items():
            if key == "components":
                payload["components"].update(value)
            else:
                payload[key] = value
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "preview.json"
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            return load_vision_preview_config(str(config_file))


class PreviewPluginServiceConcurrencyTests(unittest.TestCase):
    """Verify visual target persistence and host replacement share one service boundary."""

    def test_target_update_finishes_before_a_concurrent_reload_rebuilds_the_plugin(self):
        """Keep the persisted and active pose target aligned across a development reload."""

        async def scenario():
            created_targets = []
            store = BlockingPoseTargetStore()
            host = PluginHost(
                (
                    PluginFactoryDescriptor(
                        "visual-pose-analysis",
                        __name__,
                        "reloadable_pose_target_plugin_factory",
                        {
                            "target_joint": "right_wrist",
                            "created_targets": created_targets,
                        },
                    ),
                ),
                reload_enabled=True,
            )
            service = CameraPreviewService(
                "camera-a",
                PreviewPluginTestVisionAdapter(),
                WebPreviewSettings(bind_host="127.0.0.1"),
                pose_target_store=store,
                plugin_host=host,
            )
            await host.startup()
            try:
                update_task = asyncio.create_task(service.update_pose_target("left_wrist"))
                for _ in range(100):
                    if store.started.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(store.started.is_set())

                with patch(
                    "gripper_ai_controller.core.plugin_host.importlib.reload",
                    side_effect=lambda module: module,
                ):
                    reload_task = asyncio.create_task(
                        service.reload_plugins(("visual-pose-analysis",))
                    )
                    await asyncio.sleep(0.01)
                    self.assertFalse(reload_task.done())
                    store.release.set()
                    self.assertEqual("left_wrist", await update_task)
                    await reload_task

                active = await host.get_plugin("visual-pose-analysis")
                self.assertEqual(["left_wrist"], store.persisted_targets)
                self.assertEqual("left_wrist", active.target_joint)
                self.assertEqual(["right_wrist", "left_wrist"], created_targets)
            finally:
                store.release.set()
                await service.shutdown()

        asyncio.run(scenario())


class PreviewPluginFrameRetentionTests(unittest.TestCase):
    """Verify Host-mode pose diagnostics retain only a real inference source frame."""

    def test_slow_pose_inference_keeps_its_source_jpeg_after_newer_preview_frames_arrive(self):
        """Prevent the bounded diagnostic API from losing a source frame at preview rate."""

        async def scenario():
            host = PluginHost(
                (
                    PluginFactoryDescriptor(
                        "visual-pose-analysis",
                        __name__,
                        "retained_frame_visual_plugin_factory",
                        {"camera_id": "camera-a"},
                    ),
                )
            )
            service = CameraPreviewService(
                "camera-a",
                PreviewPluginTestVisionAdapter(),
                WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01),
                plugin_host=host,
            )
            await service.startup()
            try:
                for _ in range(300):
                    snapshot = await service.get_pose_snapshot()
                    if snapshot.captured_at is not None:
                        source_frame = await service.get_pose_frame(snapshot.captured_at)
                        if source_frame is not None:
                            self.assertEqual(snapshot.captured_at, source_frame.captured_at)
                            return
                    await asyncio.sleep(0.01)
                self.fail("The pose source JPEG was evicted before the diagnostic API could return it.")
            finally:
                await service.shutdown()

        asyncio.run(scenario())


class PreviewPluginHttpTests(unittest.TestCase):
    """Exercise plugin resources while the camera stays on its isolated preview lifecycle."""

    def test_visual_plugin_is_discoverable_and_reloads_without_stopping_preview(self):
        """Verify catalog, status, analysis delegation, and reload all remain browser-safe."""

        application = self._application(plugin_reload_enabled=True)
        with TestClient(application) as client:
            plugins = client.get("/api/plugins")
            self.assertEqual(200, plugins.status_code)
            self.assertEqual(1, len(plugins.json()["plugins"]))
            plugin = plugins.json()["plugins"][0]
            self.assertEqual("visual-pose-analysis", plugin["plugin_id"])
            self.assertEqual("visual-pose-analysis", plugin["ui_kind"])
            self.assertEqual("running", plugin["state"])
            self.assertTrue(plugin["reloadable"])

            status = client.get("/api/plugins/visual-pose-analysis/status")
            self.assertEqual(200, status.status_code)
            self.assertEqual("running", status.json()["state"])
            self.assertEqual(404, client.get("/api/plugins/not-configured/status").status_code)

            frame = self._wait_for_frame(client)
            self.assertEqual("image/jpeg", frame.headers["content-type"])
            analysis = self._wait_for_analysis(client)
            self.assertEqual("camera-a", analysis.json()["camera_id"])

            reload_response = client.post(
                "/api/plugins/reload", json={"plugin_ids": ["visual-pose-analysis"]}
            )
            self.assertEqual(200, reload_response.status_code)
            self.assertEqual("running", reload_response.json()["plugins"][0]["state"])
            self.assertEqual(200, self._wait_for_frame(client).status_code)

            duplicate = client.post(
                "/api/plugins/reload",
                json={"plugin_ids": ["visual-pose-analysis", "visual-pose-analysis"]},
            )
            self.assertEqual(422, duplicate.status_code)
            self.assertEqual("invalid_plugin_reload_request", duplicate.json()["code"])
            unknown = client.post("/api/plugins/reload", json={"plugin_ids": ["not-configured"]})
            self.assertEqual(404, unknown.status_code)
            self.assertEqual("plugin_not_found", unknown.json()["code"])

    def test_reload_is_rejected_when_local_development_gate_is_disabled(self):
        """Keep the API unavailable when configuration does not explicitly opt into reload."""

        application = self._application(plugin_reload_enabled=False)
        with TestClient(application) as client:
            response = client.post("/api/plugins/reload", json={"plugin_ids": []})
            self.assertEqual(403, response.status_code)
            self.assertEqual("plugin_reload_disabled", response.json()["code"])

    @staticmethod
    def _application(plugin_reload_enabled):
        """Build an app with the real passive plugin but no model, SDK, or device connection."""

        settings = WebPreviewSettings(
            bind_host="127.0.0.1",
            stream_fps=30,
            capture_retry_seconds=0.01,
            plugin_reload_enabled=plugin_reload_enabled,
        )
        return create_web_app(
            VisionPreviewConfig(
                camera_id="camera-a",
                vision=PreviewPluginTestVisionAdapter(),
                settings=settings,
                runtime_mode=RuntimeMode.DEVELOPMENT,
            )
        )

    @staticmethod
    def _wait_for_frame(client):
        """Poll only the existing snapshot endpoint until JPEG publication completes."""

        for _ in range(100):
            response = client.get("/api/cameras/camera-a/frame")
            if response.status_code == 200:
                return response
            time.sleep(0.01)
        raise AssertionError("The passive plugin preview did not publish a JPEG frame.")

    @staticmethod
    def _wait_for_analysis(client):
        """Poll the cached plugin result without triggering another capture or inference."""

        for _ in range(100):
            response = client.get("/api/cameras/camera-a/vision/analysis")
            if response.status_code == 200 and response.json()["frame"] is not None:
                return response
            time.sleep(0.01)
        raise AssertionError("The passive plugin analysis did not receive a captured frame.")


if __name__ == "__main__":
    unittest.main()
