"""Offline tests for local camera discovery, selection, persistence, and rollback."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter
from gripper_ai_controller.bootstrap.preview_builder import (
    VisionPreviewConfig,
    load_vision_preview_config,
)
from gripper_ai_controller.configuration import CameraSelectionSettings, WebPreviewSettings
from gripper_ai_controller.domain.models import CameraDeviceDescriptor
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.config_store import (
    CameraSelectionConfigStore,
    CameraSelectionConfigStoreError,
)
from gripper_ai_controller.web.service import CameraPreviewService


def preview_payload(
    camera_selection: Optional[Dict[str, object]] = None,
    bind_host: str = "0.0.0.0",
    vision_name: str = "simulated-camera",
) -> Dict[str, object]:
    """Build one minimal preview document with an optional selection policy."""

    payload = {
        "camera": {"camera_id": "sim-camera"},
        "components": {"vision": vision_name},
        "web": {
            "bind_host": bind_host,
            "stream_fps": 30,
            "capture_retry_seconds": 0.1,
        },
        "unrelated_project_setting": {"must_remain": ["unchanged"]},
    }  # type: Dict[str, object]
    if camera_selection is not None:
        payload["camera_selection"] = camera_selection
    return payload


def write_json(path: Path, payload: Dict[str, object]) -> None:
    """Write a temporary explicit configuration without repository path inference."""

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class MultiCameraAdapter(SimulatedCameraAdapter):
    """Provide two deterministic camera devices with controllable lifecycle failures."""

    DEVICE_IDS = ("device-a", "device-b")

    def __init__(self) -> None:
        """Select the first device and initialize observable failure seams."""

        super().__init__()
        self._selected_camera_device_id = "device-a"
        self.fail_startup_ids = set()
        self.discovery_error = None  # type: Optional[Exception]
        self.configure_calls = []
        self.startup_calls = []
        self.shutdown_calls = []

    async def on_startup(self) -> None:
        """Record the selected source and optionally reject its startup."""

        selected = self._selected_camera_device_id
        self.startup_calls.append(selected)
        if selected in self.fail_startup_ids:
            raise RuntimeError("planned camera startup failure")

    async def on_shutdown(self) -> None:
        """Record each stopped source without opening any external device."""

        self.shutdown_calls.append(self._selected_camera_device_id)

    async def list_camera_devices(self):
        """Return fresh descriptors or one deterministic discovery failure."""

        if self.discovery_error is not None:
            raise self.discovery_error
        return tuple(
            CameraDeviceDescriptor(
                device_id=device_id,
                display_name="Test camera {0}".format(index + 1),
                model_name="Deterministic camera",
                transport="simulation",
                selected=device_id == self._selected_camera_device_id,
                calibrated=device_id == "device-a",
            )
            for index, device_id in enumerate(self.DEVICE_IDS)
        )

    async def configure_camera_device(
        self, device_id: Optional[str]
    ) -> Optional[CameraDeviceDescriptor]:
        """Select a known device only while the inherited lifecycle is stopped."""

        if self.started:
            raise RuntimeError("Camera selection requires a stopped adapter.")
        if device_id is not None and device_id not in self.DEVICE_IDS:
            raise ValueError("The requested camera device is unavailable.")
        self.configure_calls.append(device_id)
        self._selected_camera_device_id = device_id
        if device_id is None:
            return None
        return next(
            descriptor
            for descriptor in await self.list_camera_devices()
            if descriptor.device_id == device_id
        )


class RecordingSelectionStore:
    """Record selected identifiers and optionally simulate a local write failure."""

    def __init__(self, failure: Optional[Exception] = None) -> None:
        self.failure = failure
        self.values = []

    def persist_selected_device_id(self, device_id: str) -> str:
        """Record one synchronous executor call before returning or failing."""

        self.values.append(device_id)
        if self.failure is not None:
            raise self.failure
        return device_id


class BlockingSelectionStore(RecordingSelectionStore):
    """Hold one executor-backed persistence call until a cancellation test releases it."""

    def __init__(self) -> None:
        """Create deterministic synchronization points around the local write."""

        super().__init__()
        self.started = threading.Event()
        self.allow_write = threading.Event()

    def persist_selected_device_id(self, device_id: str) -> str:
        """Finish the configured write after the HTTP coroutine has been cancelled."""

        self.started.set()
        if not self.allow_write.wait(2.0):
            raise RuntimeError("planned persistence wait timed out")
        return super().persist_selected_device_id(device_id)


class ResetRecordingPluginHost:
    """Expose only the passive host surface needed by a cache-reset integration test."""

    def __init__(self) -> None:
        self.reset_calls = 0
        self.started = False

    async def startup(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.started = False

    def offer_frame(self, frame, on_pose_inference_started=None) -> bool:
        del frame, on_pose_inference_started
        return self.started

    async def reset_frame_state(self) -> None:
        self.reset_calls += 1


def preview_config(
    adapter: MultiCameraAdapter,
    settings: CameraSelectionSettings,
    config_file: Optional[str] = None,
) -> VisionPreviewConfig:
    """Bind a loopback-only preview graph without constructing physical adapters."""

    return VisionPreviewConfig(
        camera_id="sim-camera",
        vision=adapter,
        settings=WebPreviewSettings(
            bind_host="127.0.0.1",
            stream_fps=30,
            capture_retry_seconds=0.01,
        ),
        config_file=config_file,
        vision_name="multi-camera",
        camera_selection_settings=settings,
    )


class CameraSelectionConfigurationTests(unittest.TestCase):
    """Verify old preview files remain valid and enabled selection is local-only."""

    def test_missing_camera_selection_keeps_backward_compatible_defaults(self):
        """An existing non-loopback preview remains read-only and loadable by default."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json(config_path, preview_payload())

            preview = load_vision_preview_config(str(config_path))

        self.assertFalse(preview.camera_selection_settings.enabled)
        self.assertIsNone(preview.camera_selection_settings.selected_device_id)
        self.assertEqual({}, preview.camera_selection_settings.calibration_ids)
        self.assertEqual("0.0.0.0", preview.settings.bind_host)

    def test_enabled_selection_accepts_only_valid_fields_on_loopback(self):
        """Retain opaque selection and calibration mappings after strict JSON validation."""

        selection = {
            "enabled": True,
            "selected_device_id": "device-a",
            "calibration_ids": {"device-a": "calibration-a"},
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json(config_path, preview_payload(selection, bind_host="127.0.0.1"))

            preview = load_vision_preview_config(str(config_path))

        self.assertTrue(preview.camera_selection_settings.enabled)
        self.assertEqual("device-a", preview.camera_selection_settings.selected_device_id)
        self.assertEqual(
            {"device-a": "calibration-a"},
            preview.camera_selection_settings.calibration_ids,
        )

    def test_enabled_selection_rejects_non_loopback_binding(self):
        selection = {"enabled": True, "selected_device_id": "device-a"}
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json(config_path, preview_payload(selection, bind_host="0.0.0.0"))

            with self.assertRaisesRegex(ValueError, "127.0.0.1"):
                load_vision_preview_config(str(config_path))

    def test_selection_field_validation_rejects_ambiguous_json(self):
        invalid_selections = (
            ({"enabled": 1}, "enabled"),
            ({"enabled": True, "selected_device_id": ""}, "selected_device_id"),
            ({"enabled": True, "selected_device_id": 7}, "selected_device_id"),
            ({"enabled": True, "calibration_ids": []}, "calibration_ids"),
            ({"enabled": True, "calibration_ids": {"": "calibration-a"}}, "key"),
            ({"enabled": True, "calibration_ids": {"device-a": ""}}, "value"),
            ({"enabled": True, "unexpected": True}, "Unsupported"),
        )
        for selection, message in invalid_selections:
            with self.subTest(selection=selection):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "preview.json"
                    write_json(
                        config_path,
                        preview_payload(selection, bind_host="127.0.0.1"),
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        load_vision_preview_config(str(config_path))


class CameraSelectionConfigStoreTests(unittest.TestCase):
    """Verify localstore scoping, identity checks, and atomic selection replacement."""

    @staticmethod
    def local_config(directory: str) -> Path:
        """Create a localstore-bound enabled selection document for one test."""

        localstore = Path(directory) / "localstore"
        localstore.mkdir()
        config_path = localstore / "preview.json"
        write_json(
            config_path,
            preview_payload(
                {
                    "enabled": True,
                    "selected_device_id": "device-a",
                    "calibration_ids": {"device-a": "calibration-a"},
                },
                bind_host="127.0.0.1",
                vision_name="multi-camera",
            ),
        )
        return config_path

    def test_store_requires_localstore_and_preserves_unrelated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            outside_path = Path(directory) / "preview.json"
            write_json(outside_path, preview_payload())
            with self.assertRaisesRegex(CameraSelectionConfigStoreError, "localstore"):
                CameraSelectionConfigStore(
                    str(outside_path), "sim-camera", "simulated-camera"
                )

            config_path = self.local_config(directory)
            store = CameraSelectionConfigStore(str(config_path), "sim-camera", "multi-camera")
            self.assertEqual("device-b", store.persist_selected_device_id("device-b"))
            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual("device-b", saved["camera_selection"]["selected_device_id"])
        self.assertEqual(
            {"device-a": "calibration-a"}, saved["camera_selection"]["calibration_ids"]
        )
        self.assertEqual(["unchanged"], saved["unrelated_project_setting"]["must_remain"])
        self.assertNotIn("selected_device_id", saved["components"])

    def test_store_rejects_dotdot_escape_from_localstore(self):
        """Canonicalize the explicit path before enforcing the localstore boundary."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "localstore").mkdir()
            configs = root / "configs"
            configs.mkdir()
            escaped_path = configs / "preview.json"
            write_json(escaped_path, preview_payload())
            disguised_path = root / "localstore" / ".." / "configs" / "preview.json"

            with self.assertRaisesRegex(CameraSelectionConfigStoreError, "localstore"):
                CameraSelectionConfigStore(
                    str(disguised_path), "sim-camera", "simulated-camera"
                )

    def test_store_rejects_identity_changes_and_disabled_selection_without_writing(self):
        mutations = (
            (
                lambda payload: payload["camera"].update(
                    {"camera_id": "other-camera"}
                ),
                "could not be saved",
            ),
            (
                lambda payload: payload["components"].update(
                    {"vision": "other-vision"}
                ),
                "could not be saved",
            ),
            (
                lambda payload: payload["components"].update(
                    {"vision_adapter_settings": {"camera_serial": "changed"}}
                ),
                "could not be saved",
            ),
            (lambda payload: payload["camera_selection"].update({"enabled": False}), "not enabled"),
        )
        for mutate, message in mutations:
            with self.subTest(message=message):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = self.local_config(directory)
                    store = CameraSelectionConfigStore(
                        str(config_path), "sim-camera", "multi-camera"
                    )
                    payload = json.loads(config_path.read_text(encoding="utf-8"))
                    mutate(payload)
                    write_json(config_path, payload)
                    changed_contents = config_path.read_bytes()

                    with self.assertRaisesRegex(CameraSelectionConfigStoreError, message):
                        store.persist_selected_device_id("device-b")
                    self.assertEqual(changed_contents, config_path.read_bytes())

    def test_failed_atomic_replace_preserves_original_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = self.local_config(directory)
            original = config_path.read_bytes()
            store = CameraSelectionConfigStore(str(config_path), "sim-camera", "multi-camera")

            with patch(
                "gripper_ai_controller.web.config_store.os.replace",
                side_effect=OSError("replacement denied"),
            ):
                with self.assertRaises(CameraSelectionConfigStoreError):
                    store.persist_selected_device_id("device-b")

            self.assertEqual(original, config_path.read_bytes())
            self.assertEqual([], list(config_path.parent.glob("preview.json.*.tmp")))


class CameraSelectionWebTests(unittest.TestCase):
    """Verify the public catalog and selection endpoint without physical camera access."""

    @staticmethod
    def service(
        adapter: MultiCameraAdapter,
        enabled: bool,
        store,
        plugin_host=None,
    ) -> CameraPreviewService:
        """Create one isolated preview service with an explicit selection policy."""

        return CameraPreviewService(
            "sim-camera",
            adapter,
            WebPreviewSettings(
                bind_host="127.0.0.1",
                stream_fps=30,
                capture_retry_seconds=0.01,
            ),
            plugin_host=plugin_host,
            camera_selection_settings=CameraSelectionSettings(
                enabled=enabled,
                selected_device_id="device-a",
            ),
            camera_selection_store=store,
        )

    def test_catalog_and_successful_selection_persist_the_opaque_device_id(self):
        """Exercise the real localstore writer through GET and PUT HTTP resources."""

        adapter = MultiCameraAdapter()
        with tempfile.TemporaryDirectory() as directory:
            config_path = CameraSelectionConfigStoreTests.local_config(directory)
            settings = CameraSelectionSettings(True, "device-a", {"device-a": "calibration-a"})
            preview = preview_config(adapter, settings, str(config_path))

            with TestClient(create_web_app(preview)) as client:
                catalog = client.get("/api/cameras")
                self.assertEqual(200, catalog.status_code)
                payload = catalog.json()
                self.assertTrue(payload["selection_enabled"])
                self.assertEqual("device-a", payload["selected_device_id"])
                self.assertEqual(["device-a", "device-b"], [item["device_id"] for item in payload["devices"]])
                self.assertNotIn("serial", json.dumps(payload).lower())

                selected = client.put(
                    "/api/cameras/sim-camera/selection",
                    json={"device_id": "device-b"},
                )
                self.assertEqual(200, selected.status_code)
                self.assertEqual("device-b", selected.json()["selected_device_id"])
                self.assertEqual("device-b", adapter.selected_camera_device_id)

            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual("device-b", saved["camera_selection"]["selected_device_id"])
        self.assertEqual(["unchanged"], saved["unrelated_project_setting"]["must_remain"])

    def test_disabled_and_unknown_selection_requests_have_stable_errors(self):
        disabled_adapter = MultiCameraAdapter()
        disabled_store = RecordingSelectionStore()
        disabled_service = self.service(disabled_adapter, False, disabled_store)
        disabled_preview = preview_config(
            disabled_adapter,
            CameraSelectionSettings(False, "device-a"),
        )
        with TestClient(
            create_web_app(disabled_preview, preview_service=disabled_service)
        ) as client:
            catalog = client.get("/api/cameras")
            self.assertEqual(200, catalog.status_code)
            self.assertFalse(catalog.json()["selection_enabled"])
            response = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": "device-b"},
            )
            self.assertEqual(403, response.status_code)
            self.assertEqual("camera_selection_disabled", response.json()["code"])
        self.assertEqual([], disabled_store.values)
        self.assertEqual("device-a", disabled_adapter.selected_camera_device_id)

        enabled_adapter = MultiCameraAdapter()
        enabled_store = RecordingSelectionStore()
        enabled_service = self.service(enabled_adapter, True, enabled_store)
        enabled_preview = preview_config(
            enabled_adapter,
            CameraSelectionSettings(True, "device-a"),
        )
        with TestClient(create_web_app(enabled_preview, preview_service=enabled_service)) as client:
            missing = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": "missing-device"},
            )
            self.assertEqual(404, missing.status_code)
            self.assertEqual("camera_device_not_found", missing.json()["code"])
            malformed = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": ""},
            )
            self.assertEqual(422, malformed.status_code)
            self.assertEqual("invalid_request", malformed.json()["code"])
            whitespace = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": "   "},
            )
            self.assertEqual(422, whitespace.status_code)
            self.assertEqual("invalid_request", whitespace.json()["code"])
        self.assertEqual([], enabled_store.values)
        self.assertEqual("device-a", enabled_adapter.selected_camera_device_id)

    def test_catalog_reports_discovery_failure_without_replacing_active_source(self):
        adapter = MultiCameraAdapter()
        adapter.discovery_error = RuntimeError("planned discovery failure")
        store = RecordingSelectionStore()
        service = self.service(adapter, True, store)
        preview = preview_config(adapter, CameraSelectionSettings(True, "device-a"))

        with TestClient(create_web_app(preview, preview_service=service)) as client:
            response = client.get("/api/cameras")
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual([], payload["devices"])
            self.assertEqual("device-a", payload["selected_device_id"])
            self.assertEqual("camera_discovery_failed", payload["discovery_error"]["code"])

        self.assertEqual("device-a", adapter.selected_camera_device_id)
        self.assertEqual([], store.values)

    def test_startup_failure_rolls_back_and_returns_normalized_operation_error(self):
        adapter = MultiCameraAdapter()
        adapter.fail_startup_ids.add("device-b")
        store = RecordingSelectionStore()
        service = self.service(adapter, True, store)
        preview = preview_config(adapter, CameraSelectionSettings(True, "device-a"))

        with TestClient(create_web_app(preview, preview_service=service)) as client:
            response = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": "device-b"},
            )
            self.assertEqual(503, response.status_code)
            self.assertEqual("camera_selection_failed", response.json()["code"])
            self.assertEqual("device-a", adapter.selected_camera_device_id)
            self.assertTrue(adapter.started)

        self.assertEqual(["device-b", "device-a"], adapter.configure_calls)
        self.assertEqual([], store.values)

    def test_persistence_failure_rolls_back_the_started_replacement(self):
        adapter = MultiCameraAdapter()
        store = RecordingSelectionStore(
            CameraSelectionConfigStoreError("planned local write failure")
        )
        service = self.service(adapter, True, store)
        preview = preview_config(adapter, CameraSelectionSettings(True, "device-a"))

        with TestClient(create_web_app(preview, preview_service=service)) as client:
            response = client.put(
                "/api/cameras/sim-camera/selection",
                json={"device_id": "device-b"},
            )
            self.assertEqual(503, response.status_code)
            self.assertEqual(
                "camera_selection_persistence_failed", response.json()["code"]
            )
            self.assertEqual("device-a", adapter.selected_camera_device_id)
            self.assertTrue(adapter.started)

        self.assertEqual(["device-b", "device-a"], adapter.configure_calls)
        self.assertEqual(["device-b"], store.values)

    def test_successful_switch_clears_old_frame_pose_and_plugin_state_in_place(self):
        """Commit a switch without a capture loop so reset caches remain observable."""

        async def scenario():
            adapter = MultiCameraAdapter()
            store = RecordingSelectionStore()
            plugin_host = ResetRecordingPluginHost()
            service = self.service(adapter, True, store, plugin_host)
            await adapter.startup()
            try:
                await service.hub.publish(b"old-jpeg", 1.0, retain_for_pose=True)
                self.assertIsNotNone(await service.hub.latest_frame())
                self.assertIsNotNone(await service.hub.pose_frame_at(1.0))

                catalog = await service.select_camera_device("device-b")

                self.assertEqual("device-b", catalog.selected_device_id)
                self.assertIsNone(await service.hub.latest_frame())
                self.assertIsNone(await service.hub.pose_frame_at(1.0))
                status = await service.hub.status()
                self.assertEqual("starting", status.state)
                self.assertIsNone(status.latest_frame_at)
                self.assertIsNone(status.error)
                self.assertEqual(1, plugin_host.reset_calls)
                self.assertTrue(adapter.started)
                self.assertEqual(["device-b"], store.values)
            finally:
                await service.shutdown()

        asyncio.run(scenario())

    def test_cancelled_request_waits_for_successful_persistence_and_keeps_new_source(self):
        """A completed executor write is the switch commit point even after cancellation."""

        async def scenario():
            adapter = MultiCameraAdapter()
            store = BlockingSelectionStore()
            service = self.service(adapter, True, store)
            await adapter.startup()
            selection_task = asyncio.create_task(
                service.select_camera_device("device-b")
            )
            try:
                for _ in range(100):
                    if store.started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(store.started.is_set())

                selection_task.cancel()
                store.allow_write.set()
                with self.assertRaises(asyncio.CancelledError):
                    await selection_task

                self.assertEqual("device-b", adapter.selected_camera_device_id)
                self.assertTrue(adapter.started)
                self.assertEqual(["device-b"], adapter.configure_calls)
                self.assertEqual(["device-b"], store.values)
            finally:
                store.allow_write.set()
                if not selection_task.done():
                    selection_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await selection_task
                await service.shutdown()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
