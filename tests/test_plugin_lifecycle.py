"""Regression tests for persisted, operator-selected passive plugin lifecycle state."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.base import FrameDispatchingVisionAdapter
from gripper_ai_controller.bootstrap.preview_builder import (
    VisionPreviewConfig,
    load_vision_preview_config,
)
from gripper_ai_controller.configuration import WebPreviewSettings
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.events import FrameCaptured
from gripper_ai_controller.core.plugin_host import (
    PluginDisabledError,
    PluginFactoryDescriptor,
    PluginHost,
)
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame, RuntimeMode
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.config_store import (
    PluginLifecycleConfigStore,
    PluginLifecycleConfigStoreError,
)
from gripper_ai_controller.web.service import (
    CameraPreviewService,
    PluginLifecycleControlsDisabledError,
    PluginLifecyclePersistenceError,
)


class LifecycleRecordingPlugin(Plugin):
    """Record passive lifecycle and typed-frame delivery without any device client."""

    manifest = ComponentManifest(
        "lifecycle-plugin", "0.1.0", "plugin", ("frame-observer",)
    )
    ui_kind = "lifecycle-test"

    def __init__(self, events):
        """Keep test-owned observable state outside the plugin host implementation."""

        self.events = events

    async def startup(self):
        """Record one successful passive startup."""

        self.events.append("startup")

    async def shutdown(self):
        """Record one passive shutdown."""

        self.events.append("shutdown")

    async def handle_event(self, event):
        """Record the event accepted through the bounded host event bus."""

        self.events.append(event)


def lifecycle_recording_plugin_factory(events):
    """Build the trusted local test plugin referenced by a descriptor."""

    return LifecycleRecordingPlugin(events)


class IdleVisionAdapter(FrameDispatchingVisionAdapter):
    """Supply the shutdown contract needed by a service-level lifecycle rollback test."""

    async def capture(self):
        """Fail if a test accidentally starts camera acquisition through this adapter."""

        raise AssertionError("The lifecycle rollback test must not capture camera frames.")


class FailingLifecycleStore:
    """Fail the durable commit boundary after a host transition for rollback coverage."""

    def persist_enabled(self, plugin_id, enabled):
        """Reject the write without accessing a filesystem or a hardware device."""

        del plugin_id, enabled
        raise PluginLifecycleConfigStoreError("planned local write failure")


class BlockingLifecycleStore:
    """Pause a synchronous local commit to exercise HTTP cancellation safely."""

    def __init__(self, fail_after_release=False):
        """Create test-owned synchronization points without accessing a JSON file."""

        self.started = threading.Event()
        self.release = threading.Event()
        self.fail_after_release = fail_after_release
        self.persisted = {}

    def persist_enabled(self, plugin_id, enabled):
        """Block the executor write until the test releases its deterministic barrier."""

        self.started.set()
        if not self.release.wait(timeout=1.0):
            raise PluginLifecycleConfigStoreError("planned persistence timeout")
        if self.fail_after_release:
            raise PluginLifecycleConfigStoreError("planned local write failure")
        self.persisted[plugin_id] = enabled


class LifecycleApiService:
    """Provide the narrow preview-service contract used by the plugin HTTP routes."""

    def __init__(self, host, store, lifecycle_controls_enabled):
        """Bind one host and explicit local persistence seam for route coverage."""

        self.plugin_host = host
        self._store = store
        self.plugin_lifecycle_controls_enabled = lifecycle_controls_enabled

    async def startup(self):
        """Start only the passive plugin host used by this route test."""

        await self.plugin_host.startup()

    async def shutdown(self):
        """Stop only the passive plugin host used by this route test."""

        await self.plugin_host.shutdown()

    async def set_plugin_enabled(self, plugin_id, enabled):
        """Apply the same host transition then local commit sequence as the real service."""

        if not self.plugin_lifecycle_controls_enabled:
            raise PluginLifecycleControlsDisabledError("planned disabled local policy")
        current = await self.plugin_host.status(plugin_id)
        if current.enabled == enabled:
            return current
        status = await self.plugin_host.set_enabled(plugin_id, enabled)
        self._store.persist_enabled(plugin_id, enabled)
        return status


class PluginLifecycleHostTests(unittest.TestCase):
    """Verify disabled plugins are absent until explicitly enabled by the trusted host."""

    @staticmethod
    def descriptor(events):
        """Return static browser metadata without constructing the plugin instance."""

        return PluginFactoryDescriptor(
            "lifecycle-plugin",
            __name__,
            "lifecycle_recording_plugin_factory",
            {"events": events},
            manifest=LifecycleRecordingPlugin.manifest,
            ui_kind=LifecycleRecordingPlugin.ui_kind,
        )

    @staticmethod
    def frame():
        """Return one valid in-memory RGB frame for event-delivery assertions."""

        return ImageFrame(
            "camera-a", 1.0, None, None, True, b"\x00\x00\x00", 1, 1, "rgb8"
        )

    def test_disabled_at_start_exposes_static_status_then_starts_and_stops_cleanly(self):
        """Keep a disabled plugin visible to the browser while it owns no active work."""

        async def scenario():
            events = []
            host = PluginHost(
                (self.descriptor(events),),
                reload_enabled=True,
                enabled_plugin_ids={"lifecycle-plugin": False},
            )
            await host.startup()
            try:
                disabled = await host.status("lifecycle-plugin")
                self.assertFalse(disabled.enabled)
                self.assertEqual("disabled", disabled.lifecycle_state)
                self.assertFalse(disabled.reloadable)
                self.assertEqual("lifecycle-plugin", disabled.name)
                self.assertEqual("lifecycle-test", disabled.ui_kind)
                self.assertNotIn("lifecycle-plugin", host.registry.components)
                self.assertFalse(await host.publish_frame(self.frame()))
                self.assertEqual([], events)

                enabled = await host.set_enabled("lifecycle-plugin", True)
                self.assertTrue(enabled.enabled)
                self.assertEqual("running", enabled.lifecycle_state)
                self.assertIn("lifecycle-plugin", host.registry.components)
                self.assertTrue(await host.publish_frame(self.frame()))
                self.assertEqual(1, len([item for item in events if isinstance(item, FrameCaptured)]))

                stopped = await host.set_enabled("lifecycle-plugin", False)
                self.assertFalse(stopped.enabled)
                self.assertEqual("disabled", stopped.lifecycle_state)
                self.assertNotIn("lifecycle-plugin", host.registry.components)
                self.assertFalse(await host.publish_frame(self.frame()))
                with self.assertRaises(PluginDisabledError):
                    await host.get_plugin("lifecycle-plugin")
                self.assertEqual(
                    ["startup", "shutdown"],
                    [item for item in events if isinstance(item, str)],
                )
            finally:
                await host.shutdown()

        asyncio.run(scenario())

    def test_reload_rejects_a_disabled_plugin(self):
        """Prevent a development reload from silently re-enabling operator-disabled work."""

        async def scenario():
            host = PluginHost(
                (self.descriptor([]),),
                reload_enabled=True,
                enabled_plugin_ids={"lifecycle-plugin": False},
            )
            await host.startup()
            try:
                with self.assertRaises(PluginDisabledError):
                    await host.reload(("lifecycle-plugin",))
            finally:
                await host.shutdown()

        asyncio.run(scenario())


class PluginLifecycleConfigStoreTests(unittest.TestCase):
    """Verify the localstore writer preserves scope and rejects changed preview identity."""

    @staticmethod
    def payload(plugin_ids=("lifecycle-plugin", "secondary-plugin")):
        """Return one complete, non-sensitive preview configuration fixture."""

        return {
            "camera": {"camera_id": "camera-a"},
            "components": {
                "vision": "simulated-camera",
                "vision_adapter_settings": {},
                "plugins": {"preview": list(plugin_ids)},
            },
            "plugin_runtime": {"enabled": {"lifecycle-plugin": False}},
            "unrelated_project_setting": {"must_remain": ["unchanged"]},
        }

    @staticmethod
    def write_json(path, payload):
        """Write a temporary JSON fixture using the same UTF-8 representation as configuration."""

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_store_requires_localstore_persists_full_map_and_preserves_other_settings(self):
        """Persist only the selected state map in a caller-selected local configuration."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside_path = root / "preview.json"
            self.write_json(outside_path, self.payload())
            with self.assertRaisesRegex(PluginLifecycleConfigStoreError, "localstore"):
                PluginLifecycleConfigStore(
                    str(outside_path),
                    "camera-a",
                    "simulated-camera",
                    ("lifecycle-plugin", "secondary-plugin"),
                )

            localstore = root / "localstore"
            localstore.mkdir()
            config_path = localstore / "preview.local.json"
            self.write_json(config_path, self.payload())
            store = PluginLifecycleConfigStore(
                str(config_path),
                "camera-a",
                "simulated-camera",
                ("lifecycle-plugin", "secondary-plugin"),
            )

            self.assertEqual(
                {"lifecycle-plugin": False, "secondary-plugin": False},
                store.persist_enabled("secondary-plugin", False),
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(
            {"lifecycle-plugin": False, "secondary-plugin": False},
            saved["plugin_runtime"]["enabled"],
        )
        self.assertEqual(
            ["unchanged"], saved["unrelated_project_setting"]["must_remain"]
        )

    def test_store_rejects_changed_preview_identity_without_writing(self):
        """Refuse a stale store when an external edit changes the trusted plugin list."""

        with tempfile.TemporaryDirectory() as directory:
            localstore = Path(directory) / "localstore"
            localstore.mkdir()
            config_path = localstore / "preview.local.json"
            self.write_json(config_path, self.payload())
            store = PluginLifecycleConfigStore(
                str(config_path),
                "camera-a",
                "simulated-camera",
                ("lifecycle-plugin", "secondary-plugin"),
            )
            changed = self.payload(("secondary-plugin", "lifecycle-plugin"))
            self.write_json(config_path, changed)
            before = config_path.read_bytes()

            with self.assertRaisesRegex(PluginLifecycleConfigStoreError, "plugin list"):
                store.persist_enabled("lifecycle-plugin", True)

            self.assertEqual(before, config_path.read_bytes())

    def test_configuration_loader_restores_persisted_disabled_state(self):
        """Make a later process construction receive the locally persisted preference."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            payload = self.payload(("visual-pose-analysis",))
            payload["plugin_runtime"] = {
                "enabled": {"visual-pose-analysis": False}
            }
            self.write_json(config_path, payload)

            preview = load_vision_preview_config(str(config_path))

        self.assertEqual(
            {"visual-pose-analysis": False}, preview.preview_plugin_enabled
        )


class PluginLifecycleServiceTests(unittest.TestCase):
    """Verify a failed local commit restores the prior in-process lifecycle state."""

    def test_persistence_failure_reenables_the_previous_plugin_state(self):
        """Keep a failed browser change from altering the next visible plugin status."""

        async def scenario():
            events = []
            host = PluginHost((PluginLifecycleHostTests.descriptor(events),))
            service = CameraPreviewService(
                "camera-a",
                IdleVisionAdapter(),
                WebPreviewSettings(
                    bind_host="127.0.0.1", plugin_lifecycle_controls_enabled=True
                ),
                plugin_host=host,
                plugin_lifecycle_store=FailingLifecycleStore(),
            )
            await host.startup()
            try:
                with self.assertRaises(PluginLifecyclePersistenceError):
                    await service.set_plugin_enabled("lifecycle-plugin", False)
                restored = await host.status("lifecycle-plugin")
                self.assertTrue(restored.enabled)
                self.assertEqual("running", restored.lifecycle_state)
            finally:
                await service.shutdown()

        asyncio.run(scenario())

    def test_cancelled_disable_waits_for_local_commit_before_propagating_cancellation(self):
        """Keep the persisted disabled choice aligned with the host after a client disconnect."""

        async def scenario():
            events = []
            store = BlockingLifecycleStore()
            host = PluginHost((PluginLifecycleHostTests.descriptor(events),))
            service = CameraPreviewService(
                "camera-a",
                IdleVisionAdapter(),
                WebPreviewSettings(
                    bind_host="127.0.0.1", plugin_lifecycle_controls_enabled=True
                ),
                plugin_host=host,
                plugin_lifecycle_store=store,
            )
            await host.startup()
            try:
                operation = asyncio.ensure_future(
                    service.set_plugin_enabled("lifecycle-plugin", False)
                )
                for _ in range(100):
                    if store.started.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(store.started.is_set())

                operation.cancel()
                store.release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await operation

                status = await host.status("lifecycle-plugin")
                self.assertFalse(status.enabled)
                self.assertEqual({"lifecycle-plugin": False}, store.persisted)
            finally:
                store.release.set()
                await service.shutdown()

        asyncio.run(scenario())

    def test_cancelled_disable_rolls_back_after_the_pending_local_commit_fails(self):
        """Restore the prior host state when a cancelled request's JSON write fails."""

        async def scenario():
            events = []
            store = BlockingLifecycleStore(fail_after_release=True)
            host = PluginHost((PluginLifecycleHostTests.descriptor(events),))
            service = CameraPreviewService(
                "camera-a",
                IdleVisionAdapter(),
                WebPreviewSettings(
                    bind_host="127.0.0.1", plugin_lifecycle_controls_enabled=True
                ),
                plugin_host=host,
                plugin_lifecycle_store=store,
            )
            await host.startup()
            try:
                operation = asyncio.ensure_future(
                    service.set_plugin_enabled("lifecycle-plugin", False)
                )
                for _ in range(100):
                    if store.started.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(store.started.is_set())

                operation.cancel()
                store.release.set()
                with self.assertRaises(PluginLifecyclePersistenceError):
                    await operation

                status = await host.status("lifecycle-plugin")
                self.assertTrue(status.enabled)
                self.assertEqual({}, store.persisted)
            finally:
                store.release.set()
                await service.shutdown()

        asyncio.run(scenario())


class PluginLifecycleHttpTests(unittest.TestCase):
    """Exercise activation endpoint success and stable policy, lookup, and type errors."""

    @staticmethod
    def application(lifecycle_controls_enabled):
        """Build a route-only application with no camera acquisition or physical device."""

        events = []
        host = PluginHost((PluginLifecycleHostTests.descriptor(events),))
        service = LifecycleApiService(host, FailingLifecycleStore(), lifecycle_controls_enabled)
        preview = VisionPreviewConfig(
            camera_id="camera-a",
            vision=object(),
            settings=WebPreviewSettings(
                bind_host="127.0.0.1",
                plugin_lifecycle_controls_enabled=lifecycle_controls_enabled,
            ),
            runtime_mode=RuntimeMode.PRODUCTION,
        )
        return create_web_app(preview, preview_service=service)

    def test_activation_route_reports_real_state_and_rejects_unknown_or_non_boolean_input(self):
        """Expose enabled state while preserving strict request validation and trusted IDs."""

        class RecordingStore:
            """Capture the route's successful local commit without touching hardware."""

            def __init__(self):
                self.calls = []

            def persist_enabled(self, plugin_id, enabled):
                self.calls.append((plugin_id, enabled))

        events = []
        host = PluginHost((PluginLifecycleHostTests.descriptor(events),))
        store = RecordingStore()
        service = LifecycleApiService(host, store, True)
        preview = VisionPreviewConfig(
            camera_id="camera-a",
            vision=object(),
            settings=WebPreviewSettings(
                bind_host="127.0.0.1", plugin_lifecycle_controls_enabled=True
            ),
        )
        with TestClient(create_web_app(preview, preview_service=service)) as client:
            catalog = client.get("/api/plugins")
            self.assertEqual(200, catalog.status_code)
            self.assertTrue(catalog.json()["plugins"][0]["enabled"])
            self.assertTrue(catalog.json()["plugins"][0]["lifecycle_controllable"])

            disabled = client.put(
                "/api/plugins/lifecycle-plugin/activation", json={"enabled": False}
            )
            self.assertEqual(200, disabled.status_code)
            self.assertFalse(disabled.json()["enabled"])
            self.assertEqual("disabled", disabled.json()["state"])
            self.assertEqual([("lifecycle-plugin", False)], store.calls)

            unknown = client.put(
                "/api/plugins/not-configured/activation", json={"enabled": True}
            )
            self.assertEqual(404, unknown.status_code)
            self.assertEqual("plugin_not_found", unknown.json()["code"])

            malformed = client.put(
                "/api/plugins/lifecycle-plugin/activation", json={"enabled": "false"}
            )
            self.assertEqual(422, malformed.status_code)
            self.assertEqual("invalid_request", malformed.json()["code"])

    def test_activation_route_rejects_a_disabled_local_lifecycle_policy(self):
        """Keep browser lifecycle writes unavailable without explicit local authorization."""

        application = self.application(False)
        with TestClient(application) as client:
            response = client.put(
                "/api/plugins/lifecycle-plugin/activation", json={"enabled": False}
            )
            self.assertEqual(403, response.status_code)
            self.assertEqual("plugin_lifecycle_controls_disabled", response.json()["code"])


if __name__ == "__main__":
    unittest.main()
