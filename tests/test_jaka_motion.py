"""Offline unit tests for JAKA joint-motion compilation and dry-run prediction."""

import asyncio
import json
import math
import socket
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from gripper_ai_controller import cli
from gripper_ai_controller.adapters.jaka import (
    JAKA_ZU3_DEFAULT_JOINT_UPPER_LIMITS_RAD,
    JakaAdapter,
    JakaDryRunRobotAdapter,
    JakaJointMotionCompiler,
    JakaJointMoveRequest,
    JakaMotionAbortRequest,
    JakaMotionValidationError,
)
from gripper_ai_controller.domain.models import (
    JointMoveMode,
    Pose3D,
    RobotAction,
    RobotCommand,
    RobotStatus,
)


def make_status(joint_positions_rad=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)):
    """Build a connected, enabled in-memory status for pure compiler tests."""

    return RobotStatus(
        initialized=True,
        joint_positions_rad=tuple(joint_positions_rad),
        tcp_pose=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "robot_base"),
        connected=True,
        powered=True,
        enabled=True,
    )


def assert_joint_positions_almost_equal(test_case, expected, actual):
    """Compare six radian values without treating binary float rounding as motion drift."""

    test_case.assertEqual(6, len(expected))
    test_case.assertEqual(6, len(actual))
    for axis, (expected_value, actual_value) in enumerate(zip(expected, actual), start=1):
        test_case.assertAlmostEqual(expected_value, actual_value, msg="J{0}".format(axis))


class JakaJointMotionCompilerTests(unittest.TestCase):
    """Verify the pure JAKA command compiler against its offline safety contract."""

    def test_absolute_joint_move_builds_exact_blocking_sdk_preview(self):
        target = (0.10, -0.10, 0.05, -0.05, 0.02, -0.02)
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=target,
            speed=0.25,
            joint_move_mode=JointMoveMode.ABSOLUTE,
        )

        preview = JakaJointMotionCompiler().compile(command, make_status())

        self.assertIsInstance(preview.request, JakaJointMoveRequest)
        self.assertEqual("joint_move", preview.sdk_method)
        self.assertEqual((target, 0, True, 0.25), preview.sdk_arguments)
        self.assertEqual(target, preview.target_joint_positions_rad)
        self.assertEqual(target, preview.joint_deltas_rad)
        self.assertAlmostEqual(0.4, preview.estimated_duration_seconds)

    def test_relative_joint_move_keeps_delta_and_uses_incremental_sdk_mode(self):
        source = (0.10, -0.10, 0.05, -0.05, 0.02, -0.02)
        delta = (0.05, 0.04, -0.03, -0.02, 0.01, -0.01)
        expected_target = (0.15, -0.06, 0.02, -0.07, 0.03, -0.03)
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=delta,
            speed=0.25,
            joint_move_mode=JointMoveMode.RELATIVE,
        )

        preview = JakaJointMotionCompiler().compile(command, make_status(source))

        self.assertIsInstance(preview.request, JakaJointMoveRequest)
        self.assertEqual("joint_move", preview.sdk_method)
        self.assertEqual((delta, 1, True, 0.25), preview.sdk_arguments)
        self.assertEqual(source, preview.source_joint_positions_rad)
        assert_joint_positions_almost_equal(self, expected_target, preview.target_joint_positions_rad)
        assert_joint_positions_almost_equal(self, delta, preview.joint_deltas_rad)

    def test_stop_compiles_to_abort_without_changing_source_state(self):
        source = (100.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        preview = JakaJointMotionCompiler().compile(
            RobotCommand(action=RobotAction.STOP), make_status(source)
        )

        self.assertIsInstance(preview.request, JakaMotionAbortRequest)
        self.assertEqual("motion_abort", preview.sdk_method)
        self.assertEqual((), preview.sdk_arguments)
        self.assertEqual(source, preview.source_joint_positions_rad)
        self.assertEqual(source, preview.target_joint_positions_rad)
        self.assertEqual((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), preview.joint_deltas_rad)
        self.assertEqual(0.0, preview.estimated_duration_seconds)

    def test_joint_move_rejects_wrong_dimension_and_non_finite_values(self):
        compiler = JakaJointMotionCompiler()
        for values in (
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ):
            with self.subTest(values=values):
                command = RobotCommand(RobotAction.MOVE_JOINTS, values, speed=0.25)
                with self.assertRaisesRegex(JakaMotionValidationError, "exactly six"):
                    compiler.compile(command, make_status())

        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid=invalid):
                command = RobotCommand(
                    RobotAction.MOVE_JOINTS,
                    (invalid, 0.0, 0.0, 0.0, 0.0, 0.0),
                    speed=0.25,
                )
                with self.assertRaisesRegex(JakaMotionValidationError, "finite"):
                    compiler.compile(command, make_status())

    def test_joint_move_rejects_speed_step_and_soft_limit_failures(self):
        compiler = JakaJointMotionCompiler()

        with self.assertRaisesRegex(JakaMotionValidationError, "configured maximum"):
            compiler.compile(
                RobotCommand(RobotAction.MOVE_JOINTS, (0.01,) * 6, speed=0.51),
                make_status(),
            )

        with self.assertRaisesRegex(JakaMotionValidationError, "per-axis joint step"):
            compiler.compile(
                RobotCommand(RobotAction.MOVE_JOINTS, (0.20, 0.0, 0.0, 0.0, 0.0, 0.0), speed=0.25),
                make_status(),
            )

        outside_limit = JAKA_ZU3_DEFAULT_JOINT_UPPER_LIMITS_RAD[0] + 0.01
        with self.assertRaisesRegex(JakaMotionValidationError, "configured JAKA soft limits"):
            compiler.compile(
                RobotCommand(
                    RobotAction.MOVE_JOINTS,
                    (outside_limit, 0.0, 0.0, 0.0, 0.0, 0.0),
                    speed=0.25,
                ),
                make_status(),
            )

    def test_joint_move_rejects_unsupported_actions_and_modes(self):
        compiler = JakaJointMotionCompiler()

        with self.assertRaisesRegex(JakaMotionValidationError, "supports only MOVE_JOINTS and STOP"):
            compiler.compile(
                RobotCommand(RobotAction.MOVE_LINEAR, speed=0.25), make_status()
            )

        with self.assertRaisesRegex(JakaMotionValidationError, "joint_move_mode"):
            compiler.compile(
                RobotCommand(
                    RobotAction.MOVE_JOINTS,
                    (0.01,) * 6,
                    speed=0.25,
                    joint_move_mode="servo_j",
                ),
                make_status(),
            )

    def test_configuration_cannot_relax_default_speed_or_step_limits(self):
        with self.assertRaisesRegex(JakaMotionValidationError, "cannot relax"):
            JakaJointMotionCompiler(maximum_joint_speed_rad_per_second=0.51)
        with self.assertRaisesRegex(JakaMotionValidationError, "cannot relax"):
            JakaJointMotionCompiler(maximum_joint_step_rad=(math.pi / 18.0) + 0.001)


class JakaDryRunRobotAdapterTests(unittest.TestCase):
    """Verify lifecycle and state prediction without a vendor SDK or network session."""

    def run_async(self, coroutine):
        """Run one isolated asyncio scenario under the Python 3.7 test runner."""

        return asyncio.run(coroutine)

    def test_lifecycle_prediction_and_stop_are_fully_in_memory(self):
        async def scenario():
            adapter = JakaDryRunRobotAdapter()
            self.assertFalse(adapter.started)
            self.assertFalse((await adapter.get_status()).connected)

            await adapter.startup()
            initialized = await adapter.initialize()
            self.assertTrue(adapter.started)
            self.assertTrue(initialized.initialized)
            self.assertTrue(initialized.connected)
            self.assertTrue(initialized.powered)
            self.assertTrue(initialized.enabled)

            target = (0.10, -0.10, 0.05, -0.05, 0.02, -0.02)
            predicted = await adapter.execute(
                RobotCommand(RobotAction.MOVE_JOINTS, target, speed=0.25)
            )
            self.assertEqual(target, predicted.joint_positions_rad)

            stopped = await adapter.execute(RobotCommand(RobotAction.STOP))
            self.assertEqual(target, stopped.joint_positions_rad)

            await adapter.shutdown()
            shutdown_status = await adapter.get_status()
            self.assertFalse(adapter.started)
            self.assertFalse(shutdown_status.initialized)
            self.assertFalse(shutdown_status.connected)
            self.assertFalse(shutdown_status.powered)
            self.assertFalse(shutdown_status.enabled)
            self.assertEqual(target, shutdown_status.joint_positions_rad)

        self.run_async(scenario())

    def test_relative_execution_and_authoritative_synchronization_update_prediction(self):
        async def scenario():
            adapter = JakaDryRunRobotAdapter(initial_joint_positions_rad=(0.05,) * 6)
            await adapter.startup()
            await adapter.initialize()
            try:
                predicted = await adapter.execute(
                    RobotCommand(
                        RobotAction.MOVE_JOINTS,
                        (0.05, -0.05, 0.02, -0.02, 0.01, -0.01),
                        speed=0.25,
                        joint_move_mode=JointMoveMode.RELATIVE,
                    )
                )
                assert_joint_positions_almost_equal(
                    self,
                    (0.10, 0.0, 0.07, 0.03, 0.06, 0.04),
                    predicted.joint_positions_rad,
                )

                authoritative = make_status((0.01, 0.02, 0.03, 0.04, 0.05, 0.06))
                await adapter.synchronize(authoritative)
                self.assertIs(authoritative, await adapter.get_status())
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_dry_run_never_loads_vendor_sdk_or_opens_a_connection(self):
        async def scenario():
            vendor_module_name = "gripper_ai_controller.adapters.jaka.jkrc"
            self.assertNotIn(vendor_module_name, sys.modules)
            with patch.object(JakaAdapter, "create_vendor_client") as create_vendor_client:
                with patch.object(socket, "create_connection") as create_connection:
                    adapter = JakaDryRunRobotAdapter()
                    await adapter.startup()
                    try:
                        await adapter.initialize()
                        await adapter.execute(
                            RobotCommand(RobotAction.MOVE_JOINTS, (0.01,) * 6, speed=0.25)
                        )
                        await adapter.execute(RobotCommand(RobotAction.STOP))
                    finally:
                        await adapter.shutdown()
            create_vendor_client.assert_not_called()
            create_connection.assert_not_called()
            self.assertNotIn(vendor_module_name, sys.modules)

        self.run_async(scenario())

    def test_motion_constraint_rejects_invalid_command_without_mutating_prediction(self):
        async def scenario():
            adapter = JakaDryRunRobotAdapter()
            await adapter.startup()
            await adapter.initialize()
            try:
                before = await adapter.get_status()
                decision = adapter.motion_constraint.evaluate(
                    RobotCommand(RobotAction.MOVE_JOINTS, (0.20, 0.0, 0.0, 0.0, 0.0, 0.0), speed=0.25),
                    before,
                )
                self.assertFalse(decision.allowed)
                self.assertIn("rejected", decision.reason)
                self.assertEqual(before, await adapter.get_status())
            finally:
                await adapter.shutdown()

        self.run_async(scenario())

    def test_dry_run_cli_reads_only_the_selected_offline_target(self):
        async def scenario():
            arguments = SimpleNamespace(
                config_file="configs/jaka-joint-dry-run.example.json",
                target="jaka-dry-run-primary",
                stop=False,
                mode="absolute",
                speed_rad_per_second=0.25,
                joint_positions_rad="[0.01, -0.01, 0.02, -0.02, 0.03, -0.03]",
            )
            output = StringIO()
            with patch.object(JakaAdapter, "create_vendor_client") as create_vendor_client:
                with patch.object(socket, "create_connection") as create_connection:
                    with redirect_stdout(output):
                        await cli._jaka_joint_dry_run(arguments)
            payload = json.loads(output.getvalue())
            self.assertEqual("jaka-dry-run-primary", payload["target_name"])
            self.assertFalse(payload["sent_to_hardware"])
            self.assertEqual("joint_move", payload["sdk_preview"]["method"])
            self.assertEqual(
                [[0.01, -0.01, 0.02, -0.02, 0.03, -0.03], 0, True, 0.25],
                payload["sdk_preview"]["arguments"],
            )
            self.assertEqual(
                [0.01, -0.01, 0.02, -0.02, 0.03, -0.03],
                payload["predicted_joint_positions_rad"],
            )
            create_vendor_client.assert_not_called()
            create_connection.assert_not_called()

        self.run_async(scenario())


if __name__ == "__main__":
    unittest.main()
