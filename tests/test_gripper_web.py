"""Offline tests for manual gripper authorization, safety, idempotency, and HTTP contracts."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import unittest
from typing import Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter
from gripper_ai_controller.bootstrap.preview_builder import VisionPreviewConfig
from gripper_ai_controller.configuration import (
    GripperControlSettings,
    SafetyLimits,
    WebPreviewSettings,
)
from gripper_ai_controller.domain.models import GripperCommand, GripperStatus
from gripper_ai_controller.services.safety import SafetyPolicy
from gripper_ai_controller.web.gripper_api import install_gripper_routes
from gripper_ai_controller.web.gripper_service import (
    GripperBusyError,
    GripperCapabilityError,
    GripperControlConflictError,
    GripperControlsDisabledError,
    GripperNotArmedError,
    ManualGripperControlService,
)
from gripper_ai_controller.web.app import create_web_app
from gripper_ai_controller.web.service import CameraPreviewService


class FakeClock:
    """Provide deterministic monotonic and wall-clock values for token expiry tests."""

    def __init__(self) -> None:
        self.monotonic_value = 10.0
        self.wall_value = 1000.0

    def monotonic(self) -> float:
        """Return the controlled monotonic timestamp."""

        return self.monotonic_value

    def wall(self) -> float:
        """Return the controlled Unix timestamp."""

        return self.wall_value

    def advance(self, seconds: float) -> None:
        """Move both clocks forward without sleeping the test process."""

        self.monotonic_value += seconds
        self.wall_value += seconds


class FakeOperatorGripper:
    """Model operator capabilities entirely in memory without sockets or device SDKs."""

    def __init__(
        self,
        mode: str = "simulation",
        supports_speed: bool = True,
        supports_stop: bool = True,
        startup_error: Optional[BaseException] = None,
    ) -> None:
        self._control_mode = mode
        self._supports_speed = supports_speed
        self._supports_stop = supports_stop
        self.startup_error = startup_error
        self.started = False
        self.startup_calls = 0
        self.shutdown_calls = 0
        self.reconnect_calls = 0
        self.initialize_calls = 0
        self.execute_calls = 0
        self.last_command = None  # type: Optional[GripperCommand]
        self.execute_entered = None  # type: Optional[asyncio.Event]
        self.execute_release = None  # type: Optional[asyncio.Event]
        self.status_value = GripperStatus(
            initialized=False,
            position=500,
            gripping=False,
            connected=False,
            grip_state="reached",
        )

    @property
    def control_mode(self) -> str:
        """Return the configured fake operating mode."""

        return self._control_mode

    @property
    def supports_speed(self) -> bool:
        """Return whether speed proposals are accepted by this fake."""

        return self._supports_speed

    @property
    def supports_stop(self) -> bool:
        """Return whether logical stop is accepted by this fake."""

        return self._supports_stop

    async def startup(self) -> None:
        """Connect without initialization or movement unless failure is injected."""

        self.startup_calls += 1
        self.started = True
        if self.startup_error is not None:
            self.status_value = replace(
                self.status_value,
                connected=False,
                last_error=str(self.startup_error),
            )
            raise self.startup_error
        self.status_value = replace(self.status_value, connected=True, last_error=None)

    async def shutdown(self) -> None:
        """Disconnect and retain the final in-memory position."""

        self.shutdown_calls += 1
        self.started = False
        self.status_value = replace(
            self.status_value,
            connected=False,
            moving=False,
            initializing=False,
        )

    async def initialize(self) -> GripperStatus:
        """Return state without performing operator initialization."""

        return self.status_value

    async def operator_initialize(self) -> GripperStatus:
        """Record one explicitly authorized initialization."""

        self.initialize_calls += 1
        self.status_value = replace(
            self.status_value,
            initialized=True,
            initializing=False,
            moving=False,
            last_error=None,
        )
        return self.status_value

    async def reconnect(self) -> GripperStatus:
        """Restore connection without changing initialization or position."""

        self.reconnect_calls += 1
        self.startup_error = None
        self.status_value = replace(self.status_value, connected=True, last_error=None)
        return self.status_value

    async def get_status(self) -> GripperStatus:
        """Return the latest in-memory status without external I/O."""

        return self.status_value

    async def execute(self, command: GripperCommand) -> GripperStatus:
        """Record one command and optionally hold it for concurrency tests."""

        self.execute_calls += 1
        self.last_command = command
        if self.execute_entered is not None:
            self.execute_entered.set()
        if self.execute_release is not None:
            await self.execute_release.wait()
        position = self.status_value.position
        if command.target_position is not None:
            position = command.target_position
        self.status_value = replace(
            self.status_value,
            position=position,
            gripping=command.action.value == "close",
            moving=False,
            grip_state="stopped" if command.action.value == "stop" else "reached",
        )
        return self.status_value

    async def synchronize(self, status: GripperStatus) -> None:
        """Replace in-memory state for abstract-port completeness."""

        self.status_value = status


def make_settings(**overrides) -> GripperControlSettings:
    """Build one conservative local control configuration for offline tests."""

    values = {
        "target_name": "primary-gripper",
        "open_position": 900,
        "close_position": 100,
        "arm_timeout_seconds": 60.0,
        "initialization_timeout_seconds": 1.0,
        "minimum_position": 0,
        "maximum_position": 1000,
        "minimum_force_percent": 20,
        "maximum_force_percent": 30,
        "minimum_speed_percent": 1,
        "maximum_speed_percent": 20,
        "idempotency_cache_size": 8,
        "idempotency_ttl_seconds": 120.0,
    }
    values.update(overrides)
    return GripperControlSettings(**values)


def make_service(
    adapter: FakeOperatorGripper,
    controls_enabled: bool = True,
    clock: Optional[FakeClock] = None,
) -> ManualGripperControlService:
    """Build the facade with limits matching the stricter browser configuration."""

    clock = clock or FakeClock()
    return ManualGripperControlService(
        adapter,
        SafetyPolicy(
            SafetyLimits(
                minimum_force_percent=20,
                maximum_force_percent=30,
                maximum_position=1000,
                minimum_speed_percent=1,
                maximum_speed_percent=20,
            )
        ),
        make_settings(),
        controls_enabled=controls_enabled,
        monotonic_clock=clock.monotonic,
        wall_clock=clock.wall,
        token_factory=lambda: "test-arm-token",
    )


def create_test_app(service: ManualGripperControlService) -> FastAPI:
    """Own the fake facade in one ASGI lifecycle without camera or runtime resources."""

    @asynccontextmanager
    async def lifespan(application):
        del application
        await service.startup()
        try:
            yield
        finally:
            await service.shutdown()

    application = FastAPI(lifespan=lifespan)
    install_gripper_routes(application, service)
    return application


class ManualGripperControlServiceTests(unittest.TestCase):
    """Verify facade state transitions without an HTTP or hardware dependency."""

    def test_disabled_service_keeps_read_only_adapter_status_without_authorization(self):
        async def scenario():
            adapter = FakeOperatorGripper()
            service = make_service(adapter, controls_enabled=False)
            await service.startup()
            status = await service.status()
            self.assertFalse(status.controls_enabled)
            self.assertTrue(status.connected)
            self.assertEqual(1, adapter.startup_calls)
            with self.assertRaises(GripperControlsDisabledError):
                await service.arm(True, True)
            await service.shutdown()
            self.assertEqual(1, adapter.shutdown_calls)

        run_async(scenario())

    def test_arm_initialize_move_and_idempotent_replay_dispatch_once(self):
        async def scenario():
            adapter = FakeOperatorGripper()
            service = make_service(adapter)
            await service.startup()
            session = await service.arm(True, True)
            initialized = await service.initialize(session.token, "init-1")
            self.assertTrue(initialized.status.initialized)
            self.assertEqual(1, adapter.initialize_calls)

            first = await service.execute_command(
                session.token,
                "move-1",
                "move_to_position",
                target_position=420,
                force_percent=25,
                speed_percent=10,
            )
            replay = await service.execute_command(
                session.token,
                "move-1",
                "move_to_position",
                target_position=420,
                force_percent=25,
                speed_percent=10,
            )
            self.assertFalse(first.replayed)
            self.assertTrue(replay.replayed)
            self.assertEqual(420, replay.status.position)
            self.assertEqual(1, adapter.execute_calls)
            self.assertEqual(420, adapter.last_command.target_position)
            await service.shutdown()

        run_async(scenario())

    def test_token_expires_after_inactivity_and_disconnect_revokes_it(self):
        async def scenario():
            clock = FakeClock()
            adapter = FakeOperatorGripper()
            service = make_service(adapter, clock=clock)
            await service.startup()
            session = await service.arm(True, True)
            clock.advance(61.0)
            with self.assertRaises(GripperNotArmedError):
                await service.initialize(session.token, "expired-init")

            session = await service.arm(True, True)
            adapter.status_value = replace(adapter.status_value, connected=False)
            status = await service.status()
            self.assertIsNone(status.armed_until)
            with self.assertRaises(GripperNotArmedError):
                await service.initialize(session.token, "disconnected-init")
            await service.shutdown()

        run_async(scenario())

    def test_physical_capability_rejections_do_not_dispatch(self):
        async def scenario():
            adapter = FakeOperatorGripper(
                mode="physical",
                supports_speed=False,
                supports_stop=False,
            )
            adapter.status_value = replace(adapter.status_value, initialized=True)
            service = make_service(adapter)
            await service.startup()
            session = await service.arm(True, True)
            with self.assertRaises(GripperCapabilityError):
                await service.execute_command(
                    session.token,
                    "speed-1",
                    "position",
                    target_position=400,
                    speed_percent=10,
                )
            with self.assertRaises(GripperCapabilityError):
                await service.execute_command(session.token, "stop-1", "stop")
            self.assertEqual(0, adapter.execute_calls)
            await service.shutdown()

        run_async(scenario())

    def test_independent_concurrent_command_is_rejected_as_busy(self):
        async def scenario():
            adapter = FakeOperatorGripper()
            adapter.status_value = replace(adapter.status_value, initialized=True)
            adapter.execute_entered = asyncio.Event()
            adapter.execute_release = asyncio.Event()
            service = make_service(adapter)
            await service.startup()
            session = await service.arm(True, True)
            first = asyncio.create_task(
                service.execute_command(
                    session.token,
                    "move-a",
                    "position",
                    target_position=300,
                )
            )
            await adapter.execute_entered.wait()
            with self.assertRaises(GripperBusyError):
                await service.execute_command(
                    session.token,
                    "move-b",
                    "position",
                    target_position=400,
                )
            adapter.execute_release.set()
            await first
            self.assertEqual(1, adapter.execute_calls)
            await service.shutdown()

        run_async(scenario())

    def test_cancelled_command_keeps_the_gripper_operation_exclusive(self):
        """A disconnected HTTP caller cannot make a blocked device action repeatable."""

        async def scenario():
            adapter = FakeOperatorGripper()
            adapter.status_value = replace(adapter.status_value, initialized=True)
            adapter.execute_entered = asyncio.Event()
            adapter.execute_release = asyncio.Event()
            service = make_service(adapter)
            await service.startup()
            try:
                session = await service.arm(True, True)
                first = asyncio.ensure_future(
                    service.execute_command(
                        session.token,
                        "cancelled-command",
                        "position",
                        target_position=300,
                    )
                )
                await asyncio.wait_for(adapter.execute_entered.wait(), timeout=0.5)

                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first

                with self.assertRaises(GripperBusyError):
                    await service.execute_command(
                        session.token,
                        "second-command",
                        "position",
                        target_position=400,
                    )

                adapter.execute_release.set()
                replay = await service.execute_command(
                    session.token,
                    "cancelled-command",
                    "position",
                    target_position=300,
                )
                self.assertTrue(replay.replayed)
                self.assertEqual(1, adapter.execute_calls)
            finally:
                adapter.execute_release.set()
                await service.shutdown()

        run_async(scenario())

    def test_moving_feedback_is_rejected_before_adapter_dispatch(self):
        async def scenario():
            adapter = FakeOperatorGripper()
            adapter.status_value = replace(adapter.status_value, initialized=True)
            service = make_service(adapter)
            await service.startup()
            session = await service.arm(True, True)
            adapter.status_value = replace(adapter.status_value, moving=True, grip_state="moving")

            with self.assertRaisesRegex(GripperControlConflictError, "already moving"):
                await service.execute_command(
                    session.token,
                    "moving-command",
                    "move_to_position",
                    target_position=300,
                )

            self.assertEqual(0, adapter.execute_calls)
            await service.shutdown()

        run_async(scenario())


class ManualGripperHttpTests(unittest.TestCase):
    """Exercise the documented resource and error contracts through FastAPI."""

    def test_full_simulated_operator_flow_and_normalized_errors(self):
        adapter = FakeOperatorGripper()
        application = create_test_app(make_service(adapter))

        with TestClient(application) as client:
            listed = client.get("/api/grippers")
            self.assertEqual(200, listed.status_code)
            self.assertEqual("primary-gripper", listed.json()["grippers"][0]["gripper_id"])
            self.assertEqual(404, client.get("/api/grippers/missing/status").status_code)

            rejected_arm = client.post(
                "/api/grippers/primary-gripper/arm",
                json={"work_area_clear": True, "emergency_stop_ready": False},
            )
            self.assertEqual(422, rejected_arm.status_code)
            self.assertEqual("invalid_gripper_command", rejected_arm.json()["code"])

            armed = client.post(
                "/api/grippers/primary-gripper/arm",
                json={"work_area_clear": True, "emergency_stop_ready": True},
            )
            self.assertEqual(200, armed.status_code)
            token = armed.json()["token"]
            headers = {
                "X-Gripper-Control-Token": token,
                "Idempotency-Key": "init-http-1",
            }
            initialized = client.post(
                "/api/grippers/primary-gripper/initialization",
                headers=headers,
            )
            self.assertEqual(200, initialized.status_code)
            self.assertTrue(initialized.json()["status"]["initialized"])

            command_headers = {
                "X-Gripper-Control-Token": token,
                "Idempotency-Key": "move-http-1",
            }
            body = {
                "action": "move_to_position",
                "target_position": 360,
                "force_percent": 25,
                "speed_percent": 10,
            }
            moved = client.post(
                "/api/grippers/primary-gripper/commands",
                headers=command_headers,
                json=body,
            )
            replay = client.post(
                "/api/grippers/primary-gripper/commands",
                headers=command_headers,
                json=body,
            )
            self.assertEqual(200, moved.status_code)
            self.assertEqual(200, replay.status_code)
            self.assertFalse(moved.json()["replayed"])
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(1, adapter.execute_calls)

            disarmed = client.delete(
                "/api/grippers/primary-gripper/arm",
                headers={"X-Gripper-Control-Token": token},
            )
            self.assertEqual(204, disarmed.status_code)
            forbidden = client.post(
                "/api/grippers/primary-gripper/commands",
                headers={"Idempotency-Key": "move-http-2"},
                json={"action": "open"},
            )
            self.assertEqual(403, forbidden.status_code)
            self.assertEqual("gripper_not_armed", forbidden.json()["code"])

    def test_physical_mode_returns_capability_conflicts_and_reconnect_disarms(self):
        adapter = FakeOperatorGripper(
            mode="physical",
            supports_speed=False,
            supports_stop=False,
        )
        adapter.status_value = replace(adapter.status_value, initialized=True)
        application = create_test_app(make_service(adapter))

        with TestClient(application) as client:
            armed = client.post(
                "/api/grippers/primary-gripper/arm",
                json={"work_area_clear": True, "emergency_stop_ready": True},
            ).json()
            token = armed["token"]
            speed = client.post(
                "/api/grippers/primary-gripper/commands",
                headers={
                    "X-Gripper-Control-Token": token,
                    "Idempotency-Key": "physical-speed",
                },
                json={"action": "position", "target_position": 300, "speed_percent": 10},
            )
            self.assertEqual(409, speed.status_code)
            self.assertEqual("gripper_capability_unavailable", speed.json()["code"])

            reconnect = client.post("/api/grippers/primary-gripper/reconnect")
            self.assertEqual(200, reconnect.status_code)
            self.assertIsNone(reconnect.json()["armed_until"])
            stopped = client.post(
                "/api/grippers/primary-gripper/commands",
                headers={
                    "X-Gripper-Control-Token": token,
                    "Idempotency-Key": "physical-stop",
                },
                json={"action": "stop"},
            )
            self.assertEqual(403, stopped.status_code)
            self.assertEqual("gripper_not_armed", stopped.json()["code"])

    def test_disabled_http_surface_exposes_read_only_status_without_authorization(self):
        adapter = FakeOperatorGripper()
        application = create_test_app(make_service(adapter, controls_enabled=False))

        with TestClient(application) as client:
            listed = client.get("/api/grippers")
            self.assertEqual(200, listed.status_code)
            self.assertFalse(listed.json()["grippers"][0]["controls_enabled"])
            arm = client.post(
                "/api/grippers/primary-gripper/arm",
                json={"work_area_clear": True, "emergency_stop_ready": True},
            )
            self.assertEqual(403, arm.status_code)
            self.assertEqual("gripper_controls_disabled", arm.json()["code"])
        self.assertEqual(1, adapter.startup_calls)
        self.assertEqual(1, adapter.shutdown_calls)

    def test_startup_connection_failure_keeps_disconnected_status_and_reconnect_route(self):
        adapter = FakeOperatorGripper(startup_error=RuntimeError("offline"))
        application = create_test_app(make_service(adapter))

        with TestClient(application) as client:
            listed = client.get("/api/grippers")
            self.assertEqual(200, listed.status_code)
            self.assertFalse(listed.json()["grippers"][0]["connected"])
            self.assertIsNotNone(listed.json()["grippers"][0]["last_error"])
            reconnected = client.post("/api/grippers/primary-gripper/reconnect")
            self.assertEqual(200, reconnected.status_code)
            self.assertTrue(reconnected.json()["connected"])
            self.assertEqual(1, adapter.reconnect_calls)

    def test_camera_app_supports_empty_and_injected_gripper_lifecycles(self):
        camera = SimulatedCameraAdapter()
        web_settings = WebPreviewSettings(stream_fps=30, capture_retry_seconds=0.01)
        preview = VisionPreviewConfig("camera-a", camera, web_settings)
        camera_service = CameraPreviewService("camera-a", camera, web_settings)
        camera_only = create_web_app(preview, preview_service=camera_service)
        with TestClient(camera_only) as client:
            response = client.get("/api/grippers")
            self.assertEqual(200, response.status_code)
            self.assertEqual([], response.json()["grippers"])

        adapter = FakeOperatorGripper()
        gripper_service = make_service(adapter)
        second_camera = SimulatedCameraAdapter()
        injected_web_settings = WebPreviewSettings(
            bind_host="127.0.0.1",
            stream_fps=30,
            capture_retry_seconds=0.01,
        )
        second_preview = VisionPreviewConfig("camera-b", second_camera, injected_web_settings)
        second_camera_service = CameraPreviewService(
            "camera-b", second_camera, injected_web_settings
        )
        combined = create_web_app(
            second_preview,
            preview_service=second_camera_service,
            gripper_control_service=gripper_service,
        )
        with TestClient(combined) as client:
            response = client.get("/api/grippers")
            self.assertEqual(200, response.status_code)
            self.assertEqual("primary-gripper", response.json()["grippers"][0]["gripper_id"])
            self.assertEqual(1, adapter.startup_calls)
        self.assertEqual(1, adapter.shutdown_calls)

    def test_camera_app_builds_manual_service_from_preview_configuration(self):
        camera = SimulatedCameraAdapter()
        adapter = FakeOperatorGripper()
        web_settings = WebPreviewSettings(
            stream_fps=30,
            capture_retry_seconds=0.01,
            gripper_controls_enabled=True,
            bind_host="127.0.0.1",
        )
        preview = VisionPreviewConfig(
            "camera-configured",
            camera,
            web_settings,
            gripper=adapter,
            gripper_control_settings=make_settings(),
        )
        camera_service = CameraPreviewService("camera-configured", camera, web_settings)
        application = create_web_app(preview, preview_service=camera_service)
        with TestClient(application) as client:
            response = client.get("/api/grippers")
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.json()["grippers"][0]["controls_enabled"])
            self.assertEqual(1, adapter.startup_calls)
        self.assertEqual(1, adapter.shutdown_calls)


def run_async(coroutine):
    """Run one asynchronous scenario on an isolated Python 3.7-compatible loop."""

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    unittest.main()
