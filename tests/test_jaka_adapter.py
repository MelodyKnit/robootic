"""Offline tests for the safety-gated JAKA adapter."""

import asyncio
import json
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.adapters.jaka import (
    JakaAdapter,
    JakaAdapterError,
    JakaMotionCompletionError,
    JakaSourcePositionChangedError,
)
from gripper_ai_controller.adapters.simulation import SimulatedRobotAdapter
from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config
from gripper_ai_controller import cli
from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.domain.models import (
    CommandEnvelope,
    GripperStatus,
    PerceptionResult,
    RobotAction,
    RobotCommand,
)
from gripper_ai_controller.services.safety import SafetyPolicy


class RecordingAdapter(BaseAdapter):
    """Expose base lifecycle hooks for deterministic lifecycle tests."""

    def __init__(self):
        super().__init__()
        self.events = []

    async def on_startup(self):
        self.events.append("started")

    async def on_shutdown(self):
        self.events.append("stopped")


class FakeJakaClient:
    """Emulate the documented read-only and enable-related JAKA SDK methods."""

    def __init__(
        self,
        login_code=0,
        powered=True,
        enabled=False,
        faulted=False,
        emergency=False,
        socket_connected=True,
        in_position=True,
        drag_mode=False,
    ):
        self.login_code = login_code
        self.powered = powered
        self.enabled = enabled
        self.faulted = faulted
        self.emergency = emergency
        self.socket_connected = socket_connected
        self.in_position = in_position
        self.drag_mode = drag_mode
        self.calls = []
        self.joint_position_response = (0, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
        self.joint_positions = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        self.joint_move_calls = []

    def login(self):
        self.calls.append("login")
        return (self.login_code,)

    def logout(self):
        self.calls.append("logout")
        return (0,)

    def get_robot_status(self):
        self.calls.append("get_robot_status")
        status = [0] * 24
        status[0] = 1 if self.faulted else 0
        status[1] = 1 if self.in_position else 0
        status[2] = 1 if self.powered else 0
        status[3] = 1 if self.enabled else 0
        status[5] = 1 if self.faulted else 0
        status[6] = 1 if self.drag_mode else 0
        status[7] = 0
        status[18] = [100.0, 200.0, 300.0, 0.1, 0.2, 0.3]
        status[19] = list(self.joint_positions)
        status[22] = 1 if self.socket_connected else 0
        status[23] = 1 if self.emergency else 0
        return (0, status)

    def get_joint_position(self):
        self.calls.append("get_joint_position")
        return self.joint_position_response

    def get_sdk_version(self):
        self.calls.append("get_sdk_version")
        return (0, "2.1.2")

    def enable_robot(self):
        self.calls.append("enable_robot")
        self.enabled = True
        return (0,)

    def disable_robot(self):
        self.calls.append("disable_robot")
        self.enabled = False
        return (0,)

    def power_on(self):
        self.calls.append("power_on")
        self.powered = True
        return (0,)

    def joint_move(self, joint_positions, move_mode, is_blocking, speed):
        """Record one explicit motion call for offline manual-control adapter tests."""

        self.calls.append("joint_move")
        self.joint_move_calls.append((list(joint_positions), move_mode, is_blocking, speed))
        self.joint_positions = list(joint_positions)
        return (0,)


class JakaAdapterTests(unittest.TestCase):
    """Verify safe JAKA lifecycle behavior without importing or connecting to the real SDK."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_base_adapter_runs_each_lifecycle_hook_once(self):
        async def scenario():
            adapter = RecordingAdapter()
            await adapter.startup()
            await adapter.startup()
            self.assertTrue(adapter.started)
            await adapter.shutdown()
            await adapter.shutdown()
            self.assertFalse(adapter.started)
            self.assertEqual(["started", "stopped"], adapter.events)

        self.run_async(scenario())

    def test_startup_connects_and_reads_status_without_enabling_or_moving(self):
        async def scenario():
            client = FakeJakaClient()
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            status = await adapter.initialize()
            self.assertTrue(adapter.connected)
            self.assertTrue(status.initialized)
            self.assertTrue(status.connected)
            self.assertTrue(status.powered)
            self.assertFalse(status.enabled)
            self.assertFalse(adapter.enabled)
            self.assertNotIn("enable_robot", client.calls)
            self.assertNotIn("power_on", client.calls)
            await adapter.shutdown()
            self.assertEqual("logout", client.calls[-1])

        self.run_async(scenario())

    def test_joint_position_read_uses_only_the_documented_read_only_sdk_operation(self):
        async def scenario():
            client = FakeJakaClient(powered=False, enabled=False)
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            snapshot = await adapter.get_joint_positions()
            self.assertEqual((0.0, 0.1, 0.2, 0.3, 0.4, 0.5), snapshot.joint_positions_rad)
            self.assertGreater(snapshot.captured_at, 0.0)
            self.assertEqual(["login", "get_joint_position"], client.calls)
            for forbidden_call in ("power_on", "enable_robot", "disable_robot", "joint_move", "servo_j"):
                self.assertNotIn(forbidden_call, client.calls)
            await adapter.shutdown()
            self.assertEqual("logout", client.calls[-1])

        self.run_async(scenario())

    def test_joint_position_read_rejects_malformed_or_nonfinite_vendor_vectors(self):
        async def incomplete_scenario():
            client = FakeJakaClient()
            client.joint_position_response = (0, [0.0, 0.1, 0.2, 0.3, 0.4])
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            try:
                with self.assertRaisesRegex(JakaAdapterError, "exactly six values"):
                    await adapter.get_joint_positions()
            finally:
                await adapter.shutdown()

        async def nonfinite_scenario():
            client = FakeJakaClient()
            client.joint_position_response = (0, [0.0, 0.1, float("nan"), 0.3, 0.4, 0.5])
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            try:
                with self.assertRaisesRegex(JakaAdapterError, "finite values"):
                    await adapter.get_joint_positions()
            finally:
                await adapter.shutdown()

        self.run_async(incomplete_scenario())
        self.run_async(nonfinite_scenario())

    def test_simulated_robot_uses_the_generic_joint_snapshot_contract(self):
        async def scenario():
            adapter = SimulatedRobotAdapter()
            await adapter.startup()
            try:
                await adapter.initialize()
                snapshot = await adapter.get_joint_positions()
                self.assertEqual((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), snapshot.joint_positions_rad)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_jaka_joints_cli_prints_named_radian_and_degree_values(self):
        async def scenario():
            client = FakeJakaClient()
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            target = SimpleNamespace(name="jaka-primary", robot=adapter)
            configuration = SimpleNamespace(targets=(target,))
            output = StringIO()
            arguments = SimpleNamespace(
                config_file="localstore/jaka-hardware.local.json",
                target=None,
            )
            with patch(
                "gripper_ai_controller.bootstrap.runtime_builder.load_runtime_config",
                return_value=configuration,
            ):
                with redirect_stdout(output):
                    await cli._read_jaka_joint_positions(arguments)
            payload = json.loads(output.getvalue())
            self.assertEqual("jaka-primary", payload["target_name"])
            self.assertEqual(0.5, payload["joint_positions_rad"]["J6"])
            self.assertAlmostEqual(5.729577951, payload["joint_positions_deg"]["J2"])
            self.assertEqual(["login", "get_joint_position", "logout"], client.calls)

        self.run_async(scenario())

    def test_enable_requires_explicit_local_permission(self):
        async def scenario():
            client = FakeJakaClient(powered=True)
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            with self.assertRaises(PermissionError):
                await adapter.enable()
            self.assertNotIn("enable_robot", client.calls)
            await adapter.shutdown()

        self.run_async(scenario())

    def test_connected_robot_stop_bypasses_perception_and_enable_interlocks(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=False)
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            try:
                status = await adapter.initialize()
                decision = SafetyPolicy(SafetyLimits()).evaluate(
                    CommandEnvelope("command", time.time(), RobotCommand(RobotAction.STOP)),
                    status,
                    GripperStatus(initialized=True, position=1000, gripping=False),
                    PerceptionResult("camera", time.time(), True, "complete", ()),
                )
                self.assertTrue(decision.allowed)
                self.assertIn("bypasses perception", decision.reason)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_example_configuration_constructs_jaka_without_loading_the_sdk(self):
        config = load_runtime_config("configs/jaka-hardware.example.json")

        self.assertIsInstance(config.targets[0].robot, JakaAdapter)
        self.assertFalse(config.targets[0].robot.started)
        self.assertNotIn("gripper_ai_controller.adapters.jaka.jkrc", sys.modules)

    def test_enable_configuration_requires_a_strict_boolean(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            JakaAdapter("controller.test", allow_enable="true")

    def test_controller_endpoint_requires_a_non_empty_string(self):
        for invalid_endpoint in (None, "", "   ", 123):
            with self.assertRaisesRegex(ValueError, "controller_ip"):
                JakaAdapter(invalid_endpoint)  # type: ignore[arg-type]

    def test_manual_motion_requires_an_explicit_zu3_model_confirmation(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                client_factory=lambda _: client,
            )
            await adapter.startup()
            try:
                self.assertFalse(adapter.manual_motion_enabled)
                with self.assertRaisesRegex(PermissionError, "robot_model='zu3'"):
                    await adapter.operator_joint_move(
                        RobotCommand(
                            RobotAction.MOVE_JOINTS,
                            (0.01, 0.09, 0.21, 0.29, 0.41, 0.49),
                            speed=0.25,
                        ),
                        (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                        0.01,
                    )
                self.assertEqual([], client.joint_move_calls)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())
        with self.assertRaisesRegex(ValueError, "robot_model"):
            JakaAdapter("controller.test", robot_model="zu7")

    def test_missing_local_jaka_sdk_is_reported_without_leaking_import_error(self):
        with patch(
            "gripper_ai_controller.adapters.jaka.adapter.importlib.import_module",
            side_effect=ImportError("SDK is unavailable"),
        ):
            with self.assertRaisesRegex(JakaAdapterError, "project-local JAKA SDK"):
                JakaAdapter.create_vendor_client("controller.test")

    def test_enable_rejects_unpowered_or_faulted_controller_without_power_on(self):
        async def scenario():
            client = FakeJakaClient(powered=False)
            adapter = JakaAdapter("controller.test", allow_enable=True, client_factory=lambda _: client)
            await adapter.startup()
            with self.assertRaises(JakaAdapterError):
                await adapter.enable()
            self.assertNotIn("enable_robot", client.calls)
            self.assertNotIn("power_on", client.calls)
            await adapter.shutdown()

        self.run_async(scenario())

    def test_enable_rejects_a_controller_that_is_not_in_position(self):
        """Servo enable must not be issued while telemetry reports an active movement."""

        async def scenario():
            client = FakeJakaClient(powered=True, in_position=False)
            adapter = JakaAdapter("controller.test", allow_enable=True, client_factory=lambda _: client)
            await adapter.startup()
            try:
                with self.assertRaisesRegex(JakaAdapterError, "in-position"):
                    await adapter.enable()
                self.assertNotIn("enable_robot", client.calls)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_explicit_enable_and_disable_call_only_their_documented_sdk_operations(self):
        async def scenario():
            client = FakeJakaClient(powered=True)
            adapter = JakaAdapter("controller.test", allow_enable=True, client_factory=lambda _: client)
            await adapter.startup()
            enabled_status = await adapter.enable()
            self.assertTrue(enabled_status.initialized)
            self.assertTrue(adapter.enabled)
            self.assertIn("enable_robot", client.calls)
            self.assertNotIn("power_on", client.calls)
            disabled_status = await adapter.disable()
            self.assertTrue(disabled_status.initialized)
            self.assertFalse(adapter.enabled)
            self.assertIn("disable_robot", client.calls)
            await adapter.shutdown()

        self.run_async(scenario())

    def test_motion_commands_are_rejected_before_reaching_the_sdk(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter("controller.test", allow_enable=True, client_factory=lambda _: client)
            await adapter.startup()
            with self.assertRaises(JakaAdapterError):
                await adapter.execute(RobotCommand(RobotAction.STOP))
            self.assertNotIn("power_on", client.calls)
            self.assertNotIn("enable_robot", client.calls)
            await adapter.shutdown()

        self.run_async(scenario())

    def test_operator_joint_move_requires_explicit_manual_permission_before_sdk_call(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=False,
                client_factory=lambda _: client,
            )
            await adapter.startup()
            try:
                with self.assertRaises(PermissionError):
                    await adapter.operator_joint_move(
                        RobotCommand(
                            RobotAction.MOVE_JOINTS,
                            (0.01, -0.01, 0.02, -0.02, 0.03, -0.03),
                            speed=0.25,
                        ),
                        (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                        0.01,
                    )
                self.assertEqual([], client.joint_move_calls)
                self.assertNotIn("joint_move", client.calls)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_operator_joint_move_uses_exact_blocking_sdk_call_and_execute_stays_rejected(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                client_factory=lambda _: client,
                robot_model="zu3",
            )
            target = (0.01, 0.09, 0.21, 0.29, 0.41, 0.49)
            await adapter.startup()
            try:
                status = await adapter.operator_joint_move(
                    RobotCommand(RobotAction.MOVE_JOINTS, target, speed=0.25),
                    (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                    0.01,
                )
                self.assertEqual(target, status.joint_positions_rad)
                self.assertEqual([(list(target), 0, True, 0.25)], client.joint_move_calls)
                self.assertNotIn("power_on", client.calls)
                self.assertNotIn("enable_robot", client.calls)

                with self.assertRaises(JakaAdapterError):
                    await adapter.execute(RobotCommand(RobotAction.MOVE_JOINTS, target, speed=0.25))
                self.assertEqual(1, len(client.joint_move_calls))
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_cancelling_a_blocking_sdk_move_keeps_the_vendor_lock_until_return(self):
        """A cancelled caller cannot overlap another SDK call with an active joint move."""

        class BlockingJakaClient(FakeJakaClient):
            """Block the documented joint call without opening a real controller connection."""

            def __init__(self):
                super().__init__(powered=True, enabled=True)
                self.joint_move_entered = threading.Event()
                self.release_joint_move = threading.Event()

            def joint_move(self, joint_positions, move_mode, is_blocking, speed):
                self.calls.append("joint_move")
                self.joint_move_calls.append((list(joint_positions), move_mode, is_blocking, speed))
                self.joint_move_entered.set()
                if not self.release_joint_move.wait(1.0):
                    raise RuntimeError("The test did not release the blocking JAKA SDK call.")
                self.joint_positions = list(joint_positions)
                return (0,)

        async def scenario():
            client = BlockingJakaClient()
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                robot_model="zu3",
                client_factory=lambda _: client,
            )
            command = RobotCommand(
                RobotAction.MOVE_JOINTS,
                (0.01, 0.09, 0.21, 0.29, 0.41, 0.49),
                speed=0.25,
            )
            await adapter.startup()
            move_task = asyncio.create_task(
                adapter.operator_joint_move(command, (0.0, 0.1, 0.2, 0.3, 0.4, 0.5), 0.01)
            )
            try:
                for _ in range(50):
                    if client.joint_move_entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(client.joint_move_entered.is_set())

                move_task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(move_task.done())

                status_task = asyncio.create_task(adapter.get_status())
                shutdown_task = asyncio.create_task(adapter.shutdown())
                await asyncio.sleep(0.02)
                self.assertFalse(status_task.done())
                self.assertFalse(shutdown_task.done())
                self.assertNotIn("logout", client.calls)

                client.release_joint_move.set()
                with self.assertRaises(asyncio.CancelledError):
                    await move_task
                status = await asyncio.wait_for(status_task, timeout=0.5)
                self.assertEqual(command.joint_positions_rad, status.joint_positions_rad)
                await asyncio.wait_for(shutdown_task, timeout=0.5)
                self.assertEqual("logout", client.calls[-1])
                self.assertEqual(1, len(client.joint_move_calls))
            finally:
                client.release_joint_move.set()
                if not move_task.done():
                    move_task.cancel()
                    try:
                        await move_task
                    except asyncio.CancelledError:
                        pass
                if adapter.started:
                    await adapter.shutdown()

        self.run_async(scenario())

    def test_manual_joint_move_rejects_unavailable_dragging_or_moving_telemetry(self):
        async def scenario(client):
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                robot_model="zu3",
                client_factory=lambda _: client,
            )
            await adapter.startup()
            try:
                status = await adapter.initialize()
                self.assertTrue(not status.connected or status.faulted or status.moving)
                with self.assertRaises((JakaAdapterError, JakaMotionCompletionError)):
                    await adapter.operator_joint_move(
                        RobotCommand(
                            RobotAction.MOVE_JOINTS,
                            (0.01, 0.09, 0.21, 0.29, 0.41, 0.49),
                            speed=0.25,
                        ),
                        (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                        0.01,
                    )
                self.assertEqual([], client.joint_move_calls)
            finally:
                await adapter.shutdown()

        self.run_async(scenario(FakeJakaClient(socket_connected=False, powered=True, enabled=True)))
        self.run_async(scenario(FakeJakaClient(drag_mode=True, powered=True, enabled=True)))
        self.run_async(scenario(FakeJakaClient(in_position=False, powered=True, enabled=True)))

    def test_manual_joint_move_rechecks_preview_source_and_completion_telemetry(self):
        async def source_changed_scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                robot_model="zu3",
                client_factory=lambda _: client,
            )
            await adapter.startup()
            try:
                with self.assertRaises(JakaSourcePositionChangedError):
                    await adapter.operator_joint_move(
                        RobotCommand(
                            RobotAction.MOVE_JOINTS,
                            (0.01, 0.09, 0.21, 0.29, 0.41, 0.49),
                            speed=0.25,
                        ),
                        (0.02, 0.1, 0.2, 0.3, 0.4, 0.5),
                        0.01,
                    )
                self.assertEqual([], client.joint_move_calls)
            finally:
                await adapter.shutdown()

        async def incomplete_scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            original_joint_move = client.joint_move

            def incomplete_joint_move(*arguments):
                result = original_joint_move(*arguments)
                client.in_position = False
                return result

            client.joint_move = incomplete_joint_move
            adapter = JakaAdapter(
                "controller.test",
                allow_manual_motion=True,
                robot_model="zu3",
                client_factory=lambda _: client,
            )
            await adapter.startup()
            try:
                with self.assertRaises(JakaMotionCompletionError):
                    await adapter.operator_joint_move(
                        RobotCommand(
                            RobotAction.MOVE_JOINTS,
                            (0.01, 0.09, 0.21, 0.29, 0.41, 0.49),
                            speed=0.25,
                        ),
                        (0.0, 0.1, 0.2, 0.3, 0.4, 0.5),
                        0.01,
                    )
                self.assertEqual(1, len(client.joint_move_calls))
            finally:
                await adapter.shutdown()

        self.run_async(source_changed_scenario())
        self.run_async(incomplete_scenario())

    def test_real_adapter_exposes_the_same_pre_dispatch_jaka_motion_constraint(self):
        async def scenario():
            client = FakeJakaClient(powered=True, enabled=True)
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            await adapter.startup()
            try:
                status = await adapter.initialize()
                decision = adapter.motion_constraint.evaluate(
                    RobotCommand(
                        RobotAction.MOVE_JOINTS,
                        (0.20, 0.0, 0.0, 0.0, 0.0, 0.0),
                        speed=0.25,
                    ),
                    status,
                )
                self.assertFalse(decision.allowed)
                self.assertIn("JAKA motion constraint", decision.reason)
                self.assertEqual(["login", "get_robot_status"], client.calls)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_failed_login_keeps_adapter_stopped(self):
        async def scenario():
            client = FakeJakaClient(login_code=7)
            adapter = JakaAdapter("controller.test", client_factory=lambda _: client)
            with self.assertRaises(JakaAdapterError):
                await adapter.startup()
            self.assertFalse(adapter.started)
            self.assertFalse(adapter.connected)
            self.assertEqual(["login"], client.calls)

        self.run_async(scenario())


if __name__ == "__main__":
    unittest.main()
