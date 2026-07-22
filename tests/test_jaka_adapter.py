"""Offline tests for the safety-gated JAKA adapter."""

import asyncio
import sys
import time
import unittest
from unittest.mock import patch

from gripper_ai_controller.adapters.base import BaseAdapter
from gripper_ai_controller.adapters.jaka import JakaAdapter, JakaAdapterError
from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config
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

    def __init__(self, login_code=0, powered=True, enabled=False, faulted=False, emergency=False):
        self.login_code = login_code
        self.powered = powered
        self.enabled = enabled
        self.faulted = faulted
        self.emergency = emergency
        self.calls = []

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
        status[2] = 1 if self.powered else 0
        status[3] = 1 if self.enabled else 0
        status[5] = 1 if self.faulted else 0
        status[7] = 0
        status[18] = [100.0, 200.0, 300.0, 0.1, 0.2, 0.3]
        status[19] = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        status[23] = 1 if self.emergency else 0
        return (0, status)

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

    def test_unenabled_robot_status_cannot_authorize_a_robot_command(self):
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
                self.assertFalse(decision.allowed)
                self.assertIn("enabled", decision.reason)
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_example_configuration_constructs_jaka_without_loading_the_sdk(self):
        config = load_runtime_config("configs/jaka-hardware.example.json")

        self.assertIsInstance(config.targets[0].robot, JakaAdapter)
        self.assertFalse(config.targets[0].robot.started)
        self.assertNotIn("gripper_ai_controller.adapters.jaka.jkrc", sys.modules)

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
