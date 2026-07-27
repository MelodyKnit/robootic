"""Async runtime tests that run on the project's Python 3.7 target."""

import asyncio
import time
import unittest
from dataclasses import replace

from gripper_ai_controller.adapters.jaka import JakaDryRunRobotAdapter
from gripper_ai_controller.adapters.simulation import SimulatedCameraAdapter, SimulatedGripperAdapter
from gripper_ai_controller.bootstrap.runtime_builder import build_runtime, load_runtime_config
from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.core.components import PlannerPlugin
from gripper_ai_controller.core.events import (
    AdapterFailed,
    CommandAuthorized,
    CommandCompleted,
    CommandRejected,
    FrameCaptured,
)
from gripper_ai_controller.core.runtime import Runtime
from gripper_ai_controller.domain.models import (
    BoundingBox2D,
    CameraMounting,
    ComponentManifest,
    CommandEnvelope,
    DetectedObject,
    GripperAction,
    GripperCommand,
    GraspCandidate,
    JointMoveMode,
    PerceptionResult,
    Pose3D,
    RobotAction,
    RobotCommand,
    RuntimeMode,
)
from gripper_ai_controller.services.safety import SafetyPolicy


DEVELOPMENT_CONFIG = "configs/development.json"
TOOL_CAMERA_CONFIG = "configs/tool-camera.json"
JAKA_DRY_RUN_CONFIG = "configs/jaka-joint-dry-run.example.json"


class UnsafePlanner(PlannerPlugin):
    """Produces an invalid force proposal to prove core safety cannot be bypassed."""

    manifest = ComponentManifest("unsafe-test-planner", "0.1.0", "planner", ("planner",), "UnsafePlanner")

    async def propose(self, objective, perception):
        return GripperCommand(GripperAction.CLOSE, target_position=300, force_percent=101, speed_percent=30)


class JointMovePlanner(PlannerPlugin):
    """Return one predetermined robot command for runtime safety-path tests."""

    manifest = ComponentManifest(
        "joint-move-test-planner", "0.1.0", "planner", ("planner",), "JointMovePlanner"
    )

    def __init__(self, command):
        """Store the normalized proposal without acquiring any adapter dependency."""

        self.command = command

    async def propose(self, objective, perception):
        """Return the fixed proposal so Runtime remains the authorization owner."""

        return self.command


class StaleCamera(SimulatedCameraAdapter):
    """Publishes an old but otherwise valid frame for stale-perception testing."""

    async def capture(self):
        frame = await super().capture()
        return replace(frame, captured_at=time.time() - 10.0)


class RuntimeTests(unittest.TestCase):
    """Exercise lifecycle, safety, visual transforms, primary, and mirror behavior."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_primary_command_is_mirrored_and_telemetry_is_authoritative(self):
        async def scenario():
            runtime = build_runtime(DEVELOPMENT_CONFIG)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick the detected workpiece")
                primary, mirror = runtime.config.targets
                audit = runtime.config.observer_plugins[0]
                self.assertTrue(result.decision.allowed)
                self.assertEqual(2, len(result.reports))
                self.assertTrue(all(report.succeeded for report in result.reports))
                self.assertEqual(result.reports[0].command_id, result.reports[1].command_id)
                self.assertEqual(300, (await primary.gripper.get_status()).position)
                self.assertEqual(300, (await mirror.gripper.get_status()).position)
                self.assertIn("sim-primary.robot", runtime.registry.components)
                self.assertIn("simulated-camera", runtime.registry.components)
                self.assertTrue(any(isinstance(event, CommandAuthorized) for event in audit.events))
                self.assertTrue(any(isinstance(event, CommandCompleted) for event in audit.events))
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_jaka_dry_run_joint_move_is_authorized_then_mirrored_with_one_command_id(self):
        async def scenario():
            config = load_runtime_config(JAKA_DRY_RUN_CONFIG)
            command = RobotCommand(
                RobotAction.MOVE_JOINTS,
                (0.05, -0.05, 0.02, -0.02, 0.01, -0.01),
                speed=0.25,
                joint_move_mode=JointMoveMode.ABSOLUTE,
            )
            config.planner_plugins = (JointMovePlanner(command),)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Dry-run JAKA joint move")
                primary, mirror = runtime.config.targets
                self.assertTrue(result.decision.allowed)
                self.assertEqual(2, len(result.reports))
                self.assertTrue(all(report.succeeded for report in result.reports))
                self.assertEqual(result.reports[0].command_id, result.reports[1].command_id)
                self.assertIsInstance(primary.robot, JakaDryRunRobotAdapter)
                self.assertEqual(command.joint_positions_rad, (await primary.robot.get_status()).joint_positions_rad)
                self.assertEqual(command.joint_positions_rad, (await mirror.robot.get_status()).joint_positions_rad)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_jaka_dry_run_constraint_rejects_invalid_joint_step_before_all_dispatch(self):
        async def scenario():
            config = load_runtime_config(JAKA_DRY_RUN_CONFIG)
            config.planner_plugins = (
                JointMovePlanner(
                    RobotCommand(
                        RobotAction.MOVE_JOINTS,
                        (0.20, 0.0, 0.0, 0.0, 0.0, 0.0),
                        speed=0.25,
                    )
                ),
            )
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Reject excessive JAKA joint step")
                primary, mirror = runtime.config.targets
                audit = runtime.config.observer_plugins[0]
                self.assertFalse(result.decision.allowed)
                self.assertIn("JAKA motion constraint", result.decision.reason)
                self.assertEqual(0, len(result.reports))
                self.assertEqual((0.0,) * 6, (await primary.robot.get_status()).joint_positions_rad)
                self.assertEqual((0.0,) * 6, (await mirror.robot.get_status()).joint_positions_rad)
                self.assertTrue(any(isinstance(event, CommandRejected) for event in audit.events))
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_jaka_dry_run_stop_bypasses_stale_perception_when_connected(self):
        async def scenario():
            config = load_runtime_config(JAKA_DRY_RUN_CONFIG)
            config.vision = StaleCamera()
            config.planner_plugins = (JointMovePlanner(RobotCommand(RobotAction.STOP)),)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Stop offline JAKA target")
                self.assertTrue(result.decision.allowed)
                self.assertEqual(2, len(result.reports))
                self.assertTrue(all(report.succeeded for report in result.reports))
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_frame_observer_and_runtime_event_receive_one_shared_frame(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            received = []
            on_frame = config.vision.on_frame

            @on_frame()
            async def record_frame(frame):
                received.append(frame)

            runtime = Runtime(config)
            await runtime.startup()
            try:
                await runtime.run_objective("Pick")
                audit = runtime.config.observer_plugins[0]
                frame_events = [event for event in audit.events if isinstance(event, FrameCaptured)]
                self.assertEqual(1, len(received))
                self.assertEqual(1, len(frame_events))
                self.assertIs(received[0], frame_events[0].frame)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_missing_calibration_prevents_dispatch(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.calibration = replace(config.calibration, valid=False)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                self.assertFalse(result.decision.allowed)
                self.assertEqual(0, len(result.reports))
                self.assertIn("calibration", result.perception.reason)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_invalid_camera_frame_prevents_dispatch(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.vision = SimulatedCameraAdapter(healthy=False)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                self.assertFalse(result.decision.allowed)
                self.assertEqual(0, len(result.reports))
                self.assertFalse(result.perception.valid)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_stale_perception_is_rejected_before_target_execution(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.vision = StaleCamera()
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                self.assertFalse(result.decision.allowed)
                self.assertIn("stale", result.decision.reason)
                self.assertEqual(0, len(result.reports))
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_unsafe_planner_proposal_is_rejected_without_motion(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.planner_plugins = (UnsafePlanner(),)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                primary = runtime.config.targets[0]
                self.assertFalse(result.decision.allowed)
                self.assertIn("Force", result.decision.reason)
                self.assertEqual(0, len(result.reports))
                self.assertEqual(1000, (await primary.gripper.get_status()).position)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_primary_failure_does_not_dispatch_to_mirror(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.targets[0].gripper = SimulatedGripperAdapter(fail_on_execute=True)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                mirror = runtime.config.targets[1]
                self.assertTrue(result.decision.allowed)
                self.assertEqual(1, len(result.reports))
                self.assertFalse(result.reports[0].succeeded)
                self.assertEqual(1000, (await mirror.gripper.get_status()).position)
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_mirror_failure_is_reported_without_repeating_primary(self):
        async def scenario():
            config = load_runtime_config(DEVELOPMENT_CONFIG)
            config.targets[1].gripper = SimulatedGripperAdapter(fail_on_execute=True)
            runtime = Runtime(config)
            await runtime.startup()
            try:
                result = await runtime.run_objective("Pick")
                audit = runtime.config.observer_plugins[0]
                primary = runtime.config.targets[0]
                self.assertTrue(result.reports[0].succeeded)
                self.assertFalse(result.reports[1].succeeded)
                self.assertEqual(300, (await primary.gripper.get_status()).position)
                self.assertTrue(any(isinstance(event, AdapterFailed) for event in audit.events))
            finally:
                await runtime.shutdown()

        self.run_async(scenario())

    def test_fixed_and_tool_camera_modes_both_resolve_robot_base_grasps(self):
        async def scenario():
            for config_file in (DEVELOPMENT_CONFIG, TOOL_CAMERA_CONFIG):
                runtime = build_runtime(config_file)
                await runtime.startup()
                try:
                    result = await runtime.run_objective("Pick")
                    candidate = result.perception.objects[0].grasp_candidates[0]
                    self.assertTrue(result.perception.valid)
                    self.assertEqual("robot_base", candidate.pose.frame_id)
                finally:
                    await runtime.shutdown()

        self.run_async(scenario())

    def test_low_confidence_perception_is_rejected_by_safety_policy(self):
        pose = Pose3D(1.0, 1.0, 1.0, 0.0, 0.0, 0.0, "robot_base")
        perception = PerceptionResult(
            "camera",
            time.time(),
            True,
            "complete",
            (DetectedObject("part", BoundingBox2D(0.0, 0.0, 1.0, 1.0), 0.2, pose, (GraspCandidate(pose, 0.2),)),),
        )
        config = load_runtime_config(DEVELOPMENT_CONFIG)
        primary = config.targets[0]

        async def statuses():
            await primary.startup()
            return await primary.robot.get_status(), await primary.gripper.get_status()

        robot, gripper = self.run_async(statuses())
        decision = SafetyPolicy(SafetyLimits()).evaluate(
            command=CommandEnvelope("test", time.time(), GripperCommand(GripperAction.CLOSE, 300, 30, 30)),
            robot_status=robot,
            gripper_status=gripper,
            perception=perception,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("confidence", decision.reason)

    def test_development_reload_rebuilds_components_and_production_rejects_reload(self):
        async def scenario():
            runtime = build_runtime(DEVELOPMENT_CONFIG)
            await runtime.startup()
            original_audit = runtime.config.observer_plugins[0]
            await runtime.reload_modules(["gripper_ai_controller.plugins.audit"])
            self.assertTrue(runtime.started)
            self.assertIsNot(original_audit, runtime.config.observer_plugins[0])
            await runtime.shutdown()

            production = load_runtime_config(DEVELOPMENT_CONFIG)
            production.mode = RuntimeMode.PRODUCTION
            production_runtime = Runtime(production, lambda: production)
            with self.assertRaises(RuntimeError):
                await production_runtime.reload_modules(["gripper_ai_controller.plugins.audit"])

        self.run_async(scenario())

    def test_root_json_configuration_rejects_unknown_component_identifiers(self):
        with self.assertRaisesRegex(ValueError, "Unknown vision adapter"):
            load_runtime_config("configs/invalid-component.fixture.json")


if __name__ == "__main__":
    unittest.main()
