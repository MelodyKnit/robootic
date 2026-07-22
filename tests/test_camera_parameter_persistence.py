"""Offline tests for explicit JSON persistence of browser camera parameters."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List, Mapping, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter
from gripper_ai_controller.bootstrap import preview_builder
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig, load_vision_preview_config
from gripper_ai_controller.configuration import CameraParameterConfigurationError, WebPreviewSettings
from gripper_ai_controller.domain.models import CameraParameterValue
from gripper_ai_controller.web import create_web_app
from gripper_ai_controller.web.config_store import (
    CameraParameterConfigStore,
    CameraParameterConfigStoreError,
)
from gripper_ai_controller.web.service import CameraPreviewService


def preview_payload(
    camera_id: str = "sim-camera",
    vision_name: str = "simulated-camera",
    camera_parameters: Optional[Dict[str, CameraParameterValue]] = None,
) -> Dict[str, object]:
    """Create a minimal explicit preview configuration with unrelated preserved fields."""

    return {
        "camera": {"camera_id": camera_id},
        "components": {"vision": vision_name},
        "web": {
            "stream_fps": 30,
            "capture_retry_seconds": 0.1,
            "camera_controls_enabled": True,
        },
        "camera_parameters": camera_parameters or {},
        "unrelated_project_setting": {"must_remain": ["unchanged"]},
    }


def write_json_config(path: Path, payload: Dict[str, object]) -> None:
    """Write one temporary test configuration without relying on project-relative paths."""

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parameter_values(response_payload: Dict[str, object]) -> Dict[str, CameraParameterValue]:
    """Convert the public parameter response into the persisted scalar mapping shape."""

    parameters = response_payload["parameters"]
    return {item["key"]: item["value"] for item in parameters}  # type: ignore[index]


def wait_for_http_frame(client: TestClient, camera_id: str) -> None:
    """Wait until an ASGI lifespan has published the first JPEG without counting frames."""

    for _ in range(100):
        response = client.get("/api/cameras/{0}/frame".format(camera_id))
        if response.status_code == 200:
            return
        time.sleep(0.01)
    raise AssertionError("The preview service did not publish a JPEG frame in time.")


def wait_for_http_status(client: TestClient, camera_id: str, expected_state: str):
    """Wait for one asynchronous preview state transition exposed by the public API."""

    for _ in range(100):
        response = client.get("/api/cameras/{0}/status".format(camera_id))
        if response.json().get("state") == expected_state:
            return response
        time.sleep(0.01)
    raise AssertionError("The preview service did not report the expected state in time.")


class RecordingSimulatedCameraAdapter(SimulatedCameraAdapter):
    """Capture the parameter state that was present before each synthetic image frame."""

    def __init__(self) -> None:
        """Create a simulation adapter with an in-memory first-capture observation list."""

        super().__init__()
        self.capture_parameter_snapshots = []  # type: List[Dict[str, CameraParameterValue]]

    async def capture(self):
        """Record values before returning the frame to prove restoration precedes capture."""

        parameters = await self.get_camera_parameters()
        self.capture_parameter_snapshots.append(
            {parameter.key: parameter.value for parameter in parameters}
        )
        return await super().capture()


class ReconnectAfterUpdateSimulatedCameraAdapter(RecordingSimulatedCameraAdapter):
    """Force one acquisition reset after a selected browser parameter update."""

    def __init__(self) -> None:
        """Create lifecycle counters and a one-shot update-triggered capture failure."""

        super().__init__()
        self.startup_calls = 0
        self.shutdown_calls = 0
        self._fail_after_next_parameter_update = False
        self._fail_next_capture = False

    def arm_capture_failure_after_next_parameter_update(self) -> None:
        """Arrange for the next successful device update to force a reconnect cycle."""

        self._fail_after_next_parameter_update = True

    async def on_startup(self) -> None:
        """Record each service-side reconnect without allocating any external resource."""

        self.startup_calls += 1

    async def on_shutdown(self) -> None:
        """Record each service-side reset without contacting real camera hardware."""

        self.shutdown_calls += 1

    async def update_camera_parameters(self, updates):
        """Arm a failure only after the simulated device has accepted the browser update."""

        result = await super().update_camera_parameters(updates)
        if self._fail_after_next_parameter_update:
            self._fail_after_next_parameter_update = False
            self._fail_next_capture = True
        return result

    async def capture(self):
        """Fail one post-update capture so the preview service exercises its reconnect path."""

        if self._fail_next_capture:
            self._fail_next_capture = False
            raise RuntimeError("simulated post-update capture failure")
        return await super().capture()


class FailingCameraParameterStore:
    """Fail after the adapter applies a value to exercise the partial-success HTTP boundary."""

    def __init__(self) -> None:
        """Create an empty observed-value record for one deterministic failure scenario."""

        self.observed_values = None  # type: Optional[Dict[str, CameraParameterValue]]

    def replace_values(
        self, observed_values: Mapping[str, CameraParameterValue]
    ) -> Dict[str, CameraParameterValue]:
        """Retain the managed mapping and then model an unavailable configuration file."""

        self.observed_values = dict(observed_values)
        raise CameraParameterConfigStoreError("The configured camera parameter file could not be saved.")


class CameraParameterConfigStoreTests(unittest.TestCase):
    """Verify the explicit JSON store is identity-bound, merge-safe, and atomic on failure."""

    def test_preview_builder_rejects_non_scalar_persisted_overrides(self):
        """Fail configuration loading before a malformed persisted value reaches an adapter."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(
                config_path,
                preview_payload(camera_parameters={"exposure_time_us": {"invalid": 12000.0}}),
            )

            with self.assertRaises(CameraParameterConfigurationError):
                load_vision_preview_config(str(config_path))

    def test_persisted_values_preserve_unrelated_configuration_and_existing_overrides(self):
        """Merge observed device values without removing unrelated user configuration fields."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(
                config_path,
                preview_payload(camera_parameters={"pixel_format": "RGB8"}),
            )
            store = CameraParameterConfigStore(str(config_path), "sim-camera", "simulated-camera")

            persisted = store.persist_values(
                {
                    "exposure_auto": "Off",
                    "exposure_time_us": 12345.0,
                    "frame_rate_enabled": True,
                }
            )
            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(
                {
                    "pixel_format": "RGB8",
                    "exposure_auto": "Off",
                    "exposure_time_us": 12345.0,
                    "frame_rate_enabled": True,
                },
                persisted,
            )
            self.assertEqual(persisted, saved_payload["camera_parameters"])
            self.assertEqual(["unchanged"], saved_payload["unrelated_project_setting"]["must_remain"])
            self.assertEqual("sim-camera", saved_payload["camera"]["camera_id"])
            self.assertEqual("simulated-camera", saved_payload["components"]["vision"])

    def test_rejects_a_configuration_that_no_longer_matches_bound_camera_or_vision(self):
        """Do not write values when an external edit points the active file at another device."""

        for camera_id, vision_name in (
            ("other-camera", "simulated-camera"),
            ("sim-camera", "other-vision"),
        ):
            with self.subTest(camera_id=camera_id, vision_name=vision_name):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "preview.json"
                    write_json_config(config_path, preview_payload(camera_id, vision_name))
                    store = CameraParameterConfigStore(
                        str(config_path), "sim-camera", "simulated-camera"
                    )

                    with self.assertRaisesRegex(CameraParameterConfigStoreError, "no longer matches"):
                        store.load_values()

    def test_rejects_writes_after_the_bound_camera_serial_changes(self):
        """Refuse both write paths when the active JSON selects another physical camera."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            payload = preview_payload()
            payload["components"]["vision_adapter_settings"] = {"camera_serial": "camera-a"}
            write_json_config(config_path, payload)
            store = CameraParameterConfigStore(
                str(config_path),
                "sim-camera",
                "simulated-camera",
                {"camera_serial": "camera-a"},
            )

            payload["components"]["vision_adapter_settings"] = {"camera_serial": "camera-b"}
            write_json_config(config_path, payload)
            changed_contents = config_path.read_bytes()

            for operation in (store.persist_values, store.replace_values):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaisesRegex(CameraParameterConfigStoreError, "device selection"):
                        operation({"exposure_time_us": 12000.0})
                    self.assertEqual(changed_contents, config_path.read_bytes())

    def test_rejects_non_scalar_camera_parameter_values(self):
        """Keep nested JSON out of the scalar adapter parameter contract."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(config_path, preview_payload())
            store = CameraParameterConfigStore(str(config_path), "sim-camera", "simulated-camera")

            with self.assertRaises(CameraParameterConfigStoreError):
                store.persist_values({"exposure_time_us": {"invalid": 12000.0}})

    def test_failed_atomic_replace_leaves_the_original_file_and_no_temporary_file(self):
        """Preserve the last known-good JSON when replacement fails after a full temp write."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(config_path, preview_payload())
            original_contents = config_path.read_bytes()
            store = CameraParameterConfigStore(str(config_path), "sim-camera", "simulated-camera")

            with patch(
                "gripper_ai_controller.web.config_store.os.replace",
                side_effect=OSError("replacement denied"),
            ):
                with self.assertRaisesRegex(CameraParameterConfigStoreError, "could not be saved"):
                    store.persist_values({"exposure_time_us": 12000.0})

            self.assertEqual(original_contents, config_path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("preview.json.*.tmp")))


class CameraParameterPersistenceHttpTests(unittest.TestCase):
    """Verify browser updates persist observed values and restoration occurs before capture."""

    def test_update_persists_observed_values_and_replays_before_rebuilt_service_first_frame(self):
        """Write device readback values, rebuild the service, and inspect its first capture state."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(config_path, preview_payload())

            first_preview = load_vision_preview_config(str(config_path))
            with TestClient(create_web_app(first_preview)) as client:
                wait_for_http_frame(client, "sim-camera")
                updated = client.patch(
                    "/api/cameras/sim-camera/parameters/exposure_time_us",
                    json={"value": 12000.0},
                )
                self.assertEqual(200, updated.status_code)
                observed_values = parameter_values(updated.json())
                persisted_values = {
                    "exposure_auto": observed_values["exposure_auto"],
                    "exposure_time_us": observed_values["exposure_time_us"],
                }

            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted_values, saved_payload["camera_parameters"])
            self.assertEqual(
                ["unchanged"],
                saved_payload["unrelated_project_setting"]["must_remain"],
            )

            with patch.dict(
                preview_builder.VISION_ADAPTERS,
                {"simulated-camera": RecordingSimulatedCameraAdapter},
            ):
                rebuilt_preview = load_vision_preview_config(str(config_path))
                self.assertEqual(persisted_values, rebuilt_preview.camera_parameter_overrides)
                rebuilt_adapter = rebuilt_preview.vision
                with TestClient(create_web_app(rebuilt_preview)) as client:
                    wait_for_http_frame(client, "sim-camera")
                    self.assertTrue(rebuilt_adapter.capture_parameter_snapshots)
                    first_capture_values = rebuilt_adapter.capture_parameter_snapshots[0]
                    for key, value in persisted_values.items():
                        self.assertEqual(value, first_capture_values[key])

    def test_disabled_camera_controls_do_not_replay_persisted_values_before_first_frame(self):
        """Keep acquisition read-only when a local configuration disables camera controls."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            payload = preview_payload(
                camera_parameters={"exposure_auto": "Off", "exposure_time_us": 12000.0}
            )
            payload["web"]["camera_controls_enabled"] = False
            write_json_config(config_path, payload)

            with patch.dict(
                preview_builder.VISION_ADAPTERS,
                {"simulated-camera": RecordingSimulatedCameraAdapter},
            ):
                preview = load_vision_preview_config(str(config_path))
                adapter = preview.vision
                with TestClient(create_web_app(preview)) as client:
                    wait_for_http_frame(client, "sim-camera")
                    self.assertTrue(adapter.capture_parameter_snapshots)
                    first_capture_values = adapter.capture_parameter_snapshots[0]
                    self.assertEqual(8000.0, first_capture_values["exposure_time_us"])
                    self.assertFalse(client.get("/api/cameras/sim-camera/parameters").json()["write_enabled"])

            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"exposure_auto": "Off", "exposure_time_us": 12000.0},
                saved_payload["camera_parameters"],
            )

    def test_switching_to_automatic_exposure_removes_stale_manual_override_before_rebuild(self):
        """Remove a manual exposure override once automatic exposure becomes authoritative."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(config_path, preview_payload())

            preview = load_vision_preview_config(str(config_path))
            with TestClient(create_web_app(preview)) as client:
                wait_for_http_frame(client, "sim-camera")
                manual_exposure = client.patch(
                    "/api/cameras/sim-camera/parameters/exposure_time_us",
                    json={"value": 12000.0},
                )
                self.assertEqual(200, manual_exposure.status_code)
                automatic_exposure = client.patch(
                    "/api/cameras/sim-camera/parameters/exposure_auto",
                    json={"value": "Continuous"},
                )
                self.assertEqual(200, automatic_exposure.status_code)
                self.assertEqual(
                    "Continuous",
                    parameter_values(automatic_exposure.json())["exposure_auto"],
                )

            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"exposure_auto": "Continuous"},
                saved_payload["camera_parameters"],
            )

            with patch.dict(
                preview_builder.VISION_ADAPTERS,
                {"simulated-camera": RecordingSimulatedCameraAdapter},
            ):
                rebuilt_preview = load_vision_preview_config(str(config_path))
                rebuilt_adapter = rebuilt_preview.vision
                with TestClient(create_web_app(rebuilt_preview)) as client:
                    wait_for_http_frame(client, "sim-camera")
                    self.assertTrue(rebuilt_adapter.capture_parameter_snapshots)
                    first_capture_values = rebuilt_adapter.capture_parameter_snapshots[0]
                    self.assertEqual("Continuous", first_capture_values["exposure_auto"])
                    self.assertEqual(8000.0, first_capture_values["exposure_time_us"])

    def test_unsupported_configured_parameters_degrade_before_capture_and_publish_no_jpeg(self):
        """Expose a failed pre-capture restore without allowing an unconfigured image through."""

        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "preview.json"
            write_json_config(
                config_path,
                preview_payload(camera_parameters={"pixel_format": "UnsupportedFormat"}),
            )

            with patch.dict(
                preview_builder.VISION_ADAPTERS,
                {"simulated-camera": RecordingSimulatedCameraAdapter},
            ):
                preview = load_vision_preview_config(str(config_path))
                adapter = preview.vision
                with TestClient(create_web_app(preview)) as client:
                    status = wait_for_http_status(client, "sim-camera", "degraded")
                    self.assertEqual("camera_parameter_restore_failed", status.json()["error"]["code"])
                    self.assertIsNone(status.json()["latest_frame_at"])
                    self.assertEqual([], adapter.capture_parameter_snapshots)

                    frame = client.get("/api/cameras/sim-camera/frame")
                    self.assertEqual(503, frame.status_code)
                    self.assertEqual("camera_unavailable", frame.json()["code"])

    def test_persistence_failure_is_distinct_from_adapter_failure_and_keeps_device_value(self):
        """Return a precise 503 after a device update succeeds but the JSON write fails."""

        adapter = SimulatedCameraAdapter()
        failing_store = FailingCameraParameterStore()
        service = CameraPreviewService(
            "sim-camera",
            adapter,
            WebPreviewSettings(
                stream_fps=30,
                capture_retry_seconds=0.01,
                camera_controls_enabled=True,
            ),
            parameter_store=failing_store,
        )
        preview = VisionPreviewConfig("sim-camera", adapter, service.settings)

        with TestClient(create_web_app(preview, preview_service=service)) as client:
            wait_for_http_frame(client, "sim-camera")
            response = client.patch(
                "/api/cameras/sim-camera/parameters/exposure_time_us",
                json={"value": 12000.0},
            )
            self.assertEqual(503, response.status_code)
            self.assertEqual("camera_parameter_persistence_failed", response.json()["code"])

            current = client.get("/api/cameras/sim-camera/parameters")
            self.assertEqual(200, current.status_code)
            values = parameter_values(current.json())
            self.assertEqual(12000.0, values["exposure_time_us"])
            self.assertIsNotNone(failing_store.observed_values)
            self.assertEqual(12000.0, failing_store.observed_values["exposure_time_us"])

    def test_persistence_failure_keeps_updated_overrides_for_same_service_reconnection(self):
        """Replay the applied value after a capture reset even though the JSON save failed."""

        adapter = ReconnectAfterUpdateSimulatedCameraAdapter()
        failing_store = FailingCameraParameterStore()
        service = CameraPreviewService(
            "sim-camera",
            adapter,
            WebPreviewSettings(
                stream_fps=30,
                capture_retry_seconds=0.01,
                camera_controls_enabled=True,
            ),
            parameter_store=failing_store,
        )
        preview = VisionPreviewConfig("sim-camera", adapter, service.settings)

        with TestClient(create_web_app(preview, preview_service=service)) as client:
            wait_for_http_frame(client, "sim-camera")
            capture_count_before_update = len(adapter.capture_parameter_snapshots)
            adapter.arm_capture_failure_after_next_parameter_update()

            response = client.patch(
                "/api/cameras/sim-camera/parameters/exposure_time_us",
                json={"value": 12000.0},
            )
            self.assertEqual(503, response.status_code)
            self.assertEqual("camera_parameter_persistence_failed", response.json()["code"])

            for _ in range(200):
                reconnected = (
                    adapter.shutdown_calls >= 1
                    and adapter.startup_calls >= 2
                    and len(adapter.capture_parameter_snapshots) > capture_count_before_update
                )
                if reconnected:
                    break
                time.sleep(0.01)
            else:
                self.fail("The preview service did not reconnect after the simulated capture failure.")

            reconnected_capture_values = adapter.capture_parameter_snapshots[
                capture_count_before_update:
            ]
            self.assertTrue(
                any(
                    values["exposure_time_us"] == 12000.0
                    for values in reconnected_capture_values
                )
            )
            self.assertEqual(
                "streaming",
                client.get("/api/cameras/sim-camera/status").json()["state"],
            )


if __name__ == "__main__":
    unittest.main()
