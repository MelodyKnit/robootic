"""Offline HTTP and facade tests for safety-gated manual JAKA joint control."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.jaka import JakaAdapter, JakaDryRunRobotAdapter
from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter
from gripper_ai_controller.bootstrap.preview_builder import (
    VisionPreviewConfig,
    load_vision_preview_config,
)
from gripper_ai_controller.configuration import JakaControlSettings, SafetyLimits, WebPreviewSettings
from gripper_ai_controller.services.safety import SafetyPolicy
from gripper_ai_controller.web.app import create_web_app
from gripper_ai_controller.web.jaka_api import install_jaka_routes
from gripper_ai_controller.web.jaka_service import (
    JakaBusyError,
    JakaControlConflictError,
    JakaControlsDisabledError,
    JakaNotArmedError,
    ManualJakaControlService,
)
from gripper_ai_controller.web.service import CameraPreviewService


class FakeJakaClient:
    """Provide only deterministic SDK calls for offline physical-adapter lifecycle tests."""

    def __init__(self) -> None:
        """Create a powered, enabled controller state without opening a connection."""

        self.calls = []
        self.joint_positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def login(self):
        """Record the SDK login call expected during adapter startup."""

        self.calls.append("login")
        return (0,)

    def logout(self):
        """Record adapter shutdown without changing the simulated controller state."""

        self.calls.append("logout")
        return (0,)

    def get_robot_status(self):
        """Return the minimal documented status layout consumed by ``JakaAdapter``."""

        self.calls.append("get_robot_status")
        status = [0] * 24
        status[1] = 1
        status[2] = 1
        status[3] = 1
        status[18] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        status[19] = list(self.joint_positions)
        status[22] = 1
        return (0, status)

    def power_on(self):
        """Expose a forbidden operation so tests can prove it was not invoked."""

        self.calls.append("power_on")
        return (0,)

    def enable_robot(self):
        """Expose a forbidden startup operation for explicit negative assertions."""

        self.calls.append("enable_robot")
        return (0,)

    def joint_move(self, *arguments):
        """Expose a forbidden startup operation for explicit negative assertions."""

        self.calls.append(("joint_move", arguments))
        return (0,)


class RecordingJakaDryRunRobotAdapter(JakaDryRunRobotAdapter):
    """Count manual-control lifecycle calls while retaining the real dry-run behavior."""

    def __init__(self) -> None:
        """Create a non-networked adapter and its observable offline counters."""

        super().__init__()
        self.startup_calls = 0
        self.initialize_calls = 0
        self.operator_joint_move_calls = 0

    async def startup(self) -> None:
        """Count lifecycle starts before delegating to the base in-memory adapter."""

        self.startup_calls += 1
        await super().startup()

    async def initialize(self):
        """Count non-moving status initialization calls."""

        self.initialize_calls += 1
        return await super().initialize()

    async def operator_joint_move(
        self,
        command,
        expected_source_joint_positions_rad,
        position_tolerance_rad,
    ):
        """Count only second-confirmed moves before updating predicted joint state."""

        self.operator_joint_move_calls += 1
        return await super().operator_joint_move(
            command,
            expected_source_joint_positions_rad,
            position_tolerance_rad,
        )


class BlockingJakaDryRunRobotAdapter(RecordingJakaDryRunRobotAdapter):
    """Hold one dry-run move so cancellation cannot hide an active operation."""

    def __init__(self) -> None:
        """Create loop-bound test events lazily inside the scenario that owns them."""

        super().__init__()
        self.move_entered = None
        self.move_release = None

    async def operator_joint_move(
        self,
        command,
        expected_source_joint_positions_rad,
        position_tolerance_rad,
    ):
        """Retain the service lock until the test explicitly permits completion."""

        self.operator_joint_move_calls += 1
        if self.move_entered is None or self.move_release is None:
            raise AssertionError("Blocking move events must be configured by the test scenario.")
        self.move_entered.set()
        await self.move_release.wait()
        return await JakaDryRunRobotAdapter.operator_joint_move(
            self,
            command,
            expected_source_joint_positions_rad,
            position_tolerance_rad,
        )


def make_settings(**overrides) -> JakaControlSettings:
    """Build a local operator policy for fake and dry-run tests only."""

    values = {
        "target_name": "jaka-primary",
        "arm_timeout_seconds": 60.0,
        "preview_timeout_seconds": 10.0,
        "source_position_tolerance_rad": 0.01,
        "idempotency_cache_size": 8,
        "idempotency_ttl_seconds": 120.0,
    }
    values.update(overrides)
    return JakaControlSettings(**values)


def make_service(
    adapter,
    controls_enabled: bool = True,
) -> ManualJakaControlService:
    """Create one non-hardware facade with deterministic token and preview identifiers."""

    return ManualJakaControlService(
        adapter,
        SafetyPolicy(SafetyLimits()),
        make_settings(),
        controls_enabled=controls_enabled,
        token_factory=lambda: "test-robot-token",
        preview_id_factory=lambda: "test-preview-id",
    )


def create_camera_app(adapter, controls_enabled: bool = True) -> FastAPI:
    """Build the real application factory around a simulated camera and JAKA dry run."""

    camera = SimulatedCameraAdapter()
    settings = WebPreviewSettings(
        bind_host="127.0.0.1" if controls_enabled else "0.0.0.0",
        stream_fps=30,
        capture_retry_seconds=0.01,
        jaka_controls_enabled=controls_enabled,
    )
    preview = VisionPreviewConfig(
        "camera-a",
        camera,
        settings,
        jaka=adapter,
        jaka_control_settings=make_settings(),
    )
    camera_service = CameraPreviewService("camera-a", camera, settings)
    return create_web_app(preview, preview_service=camera_service)


def create_standalone_jaka_app(service: ManualJakaControlService) -> FastAPI:
    """Install only the JAKA API to verify its independently reusable error contract."""

    @asynccontextmanager
    async def lifespan(application):
        """Start and stop only the supplied facade for the isolated route test."""

        del application
        await service.startup()
        try:
            yield
        finally:
            await service.shutdown()

    application = FastAPI(lifespan=lifespan)
    install_jaka_routes(application, service)
    return application


class ManualJakaControlServiceTests(unittest.TestCase):
    """Verify manual JAKA state transitions without a socket, SDK, or ASGI server."""

    def test_physical_adapter_startup_reads_status_without_enable_or_motion(self):
        """The manual facade lifecycle must remain read-only before browser authorization."""

        async def scenario():
            client = FakeJakaClient()
            adapter = JakaAdapter(
                "fake-controller.local",
                allow_enable=False,
                allow_manual_motion=False,
                client_factory=lambda _: client,
            )
            service = make_service(adapter, controls_enabled=False)
            await service.startup()
            status = await service.status()
            self.assertTrue(status.connected)
            self.assertFalse(status.controls_enabled)
            self.assertIn("login", client.calls)
            self.assertIn("get_robot_status", client.calls)
            self.assertNotIn("power_on", client.calls)
            self.assertNotIn("enable_robot", client.calls)
            self.assertFalse(any(call[0] == "joint_move" for call in client.calls if isinstance(call, tuple)))
            await service.shutdown()
            self.assertEqual("logout", client.calls[-1])

        run_async(scenario())

    def test_disabled_controls_cannot_arm_or_dispatch_a_dry_run_move(self):
        """A configured adapter remains status-only when the Web flag is false."""

        async def scenario():
            adapter = RecordingJakaDryRunRobotAdapter()
            service = make_service(adapter, controls_enabled=False)
            await service.startup()
            with self.assertRaises(JakaControlsDisabledError):
                await service.arm(True, True)
            self.assertEqual(1, adapter.startup_calls)
            self.assertEqual(1, adapter.initialize_calls)
            self.assertEqual(0, adapter.operator_joint_move_calls)
            await service.shutdown()

        run_async(scenario())

    def test_preview_requires_second_confirmation_and_idempotency_prevents_repeat_move(self):
        """Only a stored preview can advance the in-memory six-axis prediction once."""

        async def scenario():
            adapter = RecordingJakaDryRunRobotAdapter()
            service = make_service(adapter)
            target = (0.05, -0.05, 0.04, -0.04, 0.03, -0.03)
            await service.startup()
            session = await service.arm(True, True)
            preview = await service.preview_joint_move(session.token, target, 0.25)
            self.assertEqual((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), preview.source_joint_positions_rad)
            self.assertEqual(target, preview.target_joint_positions_rad)
            self.assertEqual(0, adapter.operator_joint_move_calls)

            first = await service.execute_preview(session.token, "move-once", preview.preview_id)
            replay = await service.execute_preview(session.token, "move-once", preview.preview_id)
            self.assertFalse(first.replayed)
            self.assertTrue(replay.replayed)
            self.assertEqual(target, replay.status.joint_positions_rad)
            self.assertEqual(1, adapter.operator_joint_move_calls)
            await service.shutdown()

        run_async(scenario())

    def test_cancelled_move_request_keeps_the_device_operation_exclusive(self):
        """HTTP cancellation must not release an in-flight move for another command."""

        async def scenario():
            adapter = BlockingJakaDryRunRobotAdapter()
            service = make_service(adapter)
            target = (0.05, -0.05, 0.04, -0.04, 0.03, -0.03)
            await service.startup()
            adapter.move_entered = asyncio.Event()
            adapter.move_release = asyncio.Event()
            try:
                session = await service.arm(True, True)
                preview = await service.preview_joint_move(session.token, target, 0.25)
                first = asyncio.ensure_future(
                    service.execute_preview(session.token, "cancelled-move", preview.preview_id)
                )
                await asyncio.wait_for(adapter.move_entered.wait(), timeout=0.5)

                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first

                with self.assertRaises(JakaBusyError):
                    await service.preview_joint_move(session.token, target, 0.25)

                adapter.move_release.set()
                replay = await service.execute_preview(
                    session.token,
                    "cancelled-move",
                    preview.preview_id,
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(1, adapter.operator_joint_move_calls)
            finally:
                if adapter.move_release is not None:
                    adapter.move_release.set()
                await service.shutdown()

        run_async(scenario())

    def test_joint_telemetry_drift_rejects_preview_before_dry_run_dispatch(self):
        """An operator must build a new preview when source joint telemetry changes."""

        async def scenario():
            adapter = RecordingJakaDryRunRobotAdapter()
            service = make_service(adapter)
            await service.startup()
            session = await service.arm(True, True)
            preview = await service.preview_joint_move(
                session.token,
                (0.05, 0.0, 0.0, 0.0, 0.0, 0.0),
                0.25,
            )
            adapter.status = replace(
                adapter.status,
                joint_positions_rad=(0.02, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
            with self.assertRaises(JakaControlConflictError):
                await service.execute_preview(session.token, "drifted-move", preview.preview_id)
            self.assertEqual(0, adapter.operator_joint_move_calls)
            await service.shutdown()

        run_async(scenario())

    def test_fault_or_emergency_stop_revokes_arm_and_preview_before_recovery(self):
        """Unsafe telemetry must require a fresh work-area confirmation after recovery."""

        async def scenario(unsafe_field: str):
            adapter = RecordingJakaDryRunRobotAdapter()
            service = make_service(adapter)
            await service.startup()
            try:
                session = await service.arm(True, True)
                preview = await service.preview_joint_move(
                    session.token,
                    (0.05, 0.0, 0.0, 0.0, 0.0, 0.0),
                    0.25,
                )
                adapter.status = replace(adapter.status, **{unsafe_field: True})
                unsafe_status = await service.status()
                self.assertIsNone(unsafe_status.armed_until)

                adapter.status = replace(adapter.status, **{unsafe_field: False})
                with self.assertRaises(JakaNotArmedError):
                    await service.execute_preview(
                        session.token,
                        "unsafe-state-recovery",
                        preview.preview_id,
                    )
                self.assertEqual(0, adapter.operator_joint_move_calls)
            finally:
                await service.shutdown()

        run_async(scenario("faulted"))
        run_async(scenario("emergency_stopped"))
        run_async(scenario("moving"))

    def test_power_or_servo_loss_revokes_arm_and_preview_before_recovery(self):
        """A power or servo state rollback must invalidate prior operator confirmation."""

        async def scenario(unsafe_field: str):
            adapter = RecordingJakaDryRunRobotAdapter()
            service = make_service(adapter)
            await service.startup()
            try:
                session = await service.arm(True, True)
                preview = await service.preview_joint_move(
                    session.token,
                    (0.05, 0.0, 0.0, 0.0, 0.0, 0.0),
                    0.25,
                )
                self.assertTrue(getattr(adapter.status, unsafe_field))
                adapter.status = replace(adapter.status, **{unsafe_field: False})
                lost_status = await service.status()
                self.assertIsNone(lost_status.armed_until)

                adapter.status = replace(adapter.status, **{unsafe_field: True})
                with self.assertRaises(JakaNotArmedError):
                    await service.execute_preview(
                        session.token,
                        "state-rollback-recovery",
                        preview.preview_id,
                    )
                self.assertEqual(0, adapter.operator_joint_move_calls)
            finally:
                await service.shutdown()

        run_async(scenario("powered"))
        run_async(scenario("enabled"))

    def test_physical_adapter_rechecks_source_after_service_status_check(self):
        """A source change between facade validation and SDK dispatch must block the move."""

        class DriftAfterFacadeCheckClient(FakeJakaClient):
            """Move fake telemetry on one selected read without opening a controller connection."""

            def __init__(self) -> None:
                """Start from the ordinary in-position fake controller state."""

                super().__init__()
                self.status_read_count = 0
                self.drift_on_status_read = None

            def get_robot_status(self):
                """Expose a deterministic external-position change between two safety reads."""

                self.status_read_count += 1
                if self.drift_on_status_read == self.status_read_count:
                    self.joint_positions[0] = 0.02
                return super().get_robot_status()

        async def scenario():
            client = DriftAfterFacadeCheckClient()
            adapter = JakaAdapter(
                "fake-controller.local",
                allow_manual_motion=True,
                robot_model="zu3",
                client_factory=lambda _: client,
            )
            service = make_service(adapter)
            await service.startup()
            try:
                session = await service.arm(True, True)
                preview = await service.preview_joint_move(
                    session.token,
                    (0.05, 0.0, 0.0, 0.0, 0.0, 0.0),
                    0.25,
                )
                # The facade consumes one status read; the adapter must reject on its own next read.
                client.drift_on_status_read = client.status_read_count + 2
                with self.assertRaises(JakaControlConflictError):
                    await service.execute_preview(session.token, "racing-move", preview.preview_id)
                self.assertFalse(
                    any(
                        isinstance(call, tuple) and call[0] == "joint_move"
                        for call in client.calls
                    )
                )
            finally:
                await service.shutdown()

        run_async(scenario())

    def test_local_web_config_constructs_model_confirmed_adapter_without_sdk_load(self):
        """A private physical configuration must be valid before any real client is created."""

        with open("configs/jaka-web-control.example.json", "r", encoding="utf-8") as source:
            payload = json.load(source)
        target = payload["targets"][0]
        target["robot_adapter"] = "jaka-robot"
        target["robot_adapter_settings"] = {
            "controller_ip": "198.51.100.10",
            "allow_enable": True,
            "allow_manual_motion": True,
            "robot_model": "zu3",
        }
        payload["web"]["jaka_controls_enabled"] = True

        config_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                json.dump(payload, temporary)
                config_file = temporary.name
            config = load_vision_preview_config(config_file)
            self.assertIsInstance(config.jaka, JakaAdapter)
            self.assertTrue(config.jaka.manual_motion_enabled)  # type: ignore[union-attr]
            self.assertFalse(config.jaka.started)  # type: ignore[union-attr]
        finally:
            if config_file is not None and os.path.exists(config_file):
                os.unlink(config_file)

    def test_injected_enabled_jaka_service_requires_loopback_host(self):
        """An injected facade must not bypass the application loopback control boundary."""

        camera = SimulatedCameraAdapter()
        settings = WebPreviewSettings(
            bind_host="0.0.0.0",
            stream_fps=30,
            capture_retry_seconds=0.01,
            jaka_controls_enabled=False,
        )
        preview = VisionPreviewConfig("camera-a", camera, settings)
        camera_service = CameraPreviewService("camera-a", camera, settings)
        jaka_service = make_service(RecordingJakaDryRunRobotAdapter(), controls_enabled=True)

        with self.assertRaises(ValueError):
            create_web_app(
                preview,
                preview_service=camera_service,
                jaka_control_service=jaka_service,
            )


class ManualJakaHttpTests(unittest.TestCase):
    """Exercise the browser resource contract through FastAPI without binding a port."""

    def test_camera_application_builds_jaka_facade_and_enforces_two_confirmations(self):
        """The application factory owns only the selected JAKA facade, never Runtime motion."""

        adapter = RecordingJakaDryRunRobotAdapter()
        application = create_camera_app(adapter)

        with TestClient(application) as client:
            listed = client.get("/api/robots")
            self.assertEqual(200, listed.status_code)
            robot = listed.json()["robots"][0]
            self.assertEqual("jaka-primary", robot["robot_id"])
            self.assertTrue(robot["manual_motion_enabled"])
            self.assertEqual(1, adapter.startup_calls)
            self.assertEqual(1, adapter.initialize_calls)
            self.assertEqual(0, adapter.operator_joint_move_calls)
            self.assertEqual(404, client.get("/api/robots/missing/status").status_code)

            invalid_request = client.post(
                "/api/robots/jaka-primary/arm",
                json={"work_area_clear": True},
            )
            self.assertEqual(422, invalid_request.status_code)
            self.assertEqual("invalid_request", invalid_request.json()["code"])

            unsafe_arm = client.post(
                "/api/robots/jaka-primary/arm",
                json={"work_area_clear": True, "emergency_stop_ready": False},
            )
            self.assertEqual(422, unsafe_arm.status_code)
            self.assertEqual("invalid_jaka_command", unsafe_arm.json()["code"])

            armed = client.post(
                "/api/robots/jaka-primary/arm",
                json={"work_area_clear": True, "emergency_stop_ready": True},
            )
            self.assertEqual(200, armed.status_code)
            token = armed.json()["token"]

            no_token_preview = client.post(
                "/api/robots/jaka-primary/joint-moves/preview",
                json={
                    "joint_positions_rad": [0.05, -0.05, 0.04, -0.04, 0.03, -0.03],
                    "speed_rad_per_second": 0.25,
                },
            )
            self.assertEqual(403, no_token_preview.status_code)
            self.assertEqual("jaka_not_armed", no_token_preview.json()["code"])

            headers = {"X-Robot-Control-Token": token}
            preview = client.post(
                "/api/robots/jaka-primary/joint-moves/preview",
                headers=headers,
                json={
                    "joint_positions_rad": [0.05, -0.05, 0.04, -0.04, 0.03, -0.03],
                    "speed_rad_per_second": 0.25,
                },
            )
            self.assertEqual(200, preview.status_code)
            preview_id = preview.json()["preview_id"]
            self.assertEqual(0, adapter.operator_joint_move_calls)

            missing_key = client.post(
                "/api/robots/jaka-primary/commands",
                headers=headers,
                json={"preview_id": preview_id},
            )
            self.assertEqual(422, missing_key.status_code)
            self.assertEqual("invalid_jaka_command", missing_key.json()["code"])

            command_headers = {
                "X-Robot-Control-Token": token,
                "Idempotency-Key": "http-move-once",
            }
            first = client.post(
                "/api/robots/jaka-primary/commands",
                headers=command_headers,
                json={"preview_id": preview_id},
            )
            replay = client.post(
                "/api/robots/jaka-primary/commands",
                headers=command_headers,
                json={"preview_id": preview_id},
            )
            self.assertEqual(200, first.status_code)
            self.assertEqual(200, replay.status_code)
            self.assertFalse(first.json()["replayed"])
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(1, adapter.operator_joint_move_calls)

        self.assertFalse(adapter.started)

    def test_standalone_router_normalizes_fastapi_validation_errors(self):
        """Installing JAKA routes without the camera app retains the documented error payload."""

        application = create_standalone_jaka_app(make_service(RecordingJakaDryRunRobotAdapter()))
        with TestClient(application) as client:
            response = client.post(
                "/api/robots/jaka-primary/arm",
                json={"work_area_clear": True},
            )
            self.assertEqual(422, response.status_code)
            self.assertEqual(
                {"code": "invalid_request", "message": "The request body is invalid."},
                response.json(),
            )


def run_async(coroutine):
    """Run one asynchronous scenario on an isolated Python 3.7-compatible event loop."""

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    unittest.main()
