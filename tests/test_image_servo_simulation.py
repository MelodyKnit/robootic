"""Offline tests for the pure image-plane hand-centering simulation."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gripper_ai_controller.image_servo_simulation.configuration import (
    load_image_servo_simulation_config,
)
from gripper_ai_controller.image_servo_simulation.controller import ImageCenteringController
from gripper_ai_controller.image_servo_simulation.models import (
    ImageServoSimulationSettings,
    ImageTargetObservation,
)
from gripper_ai_controller.image_servo_simulation.runner import (
    ImageServoInputError,
    render_console_report,
    run_static_image_centering,
    select_image_target,
)
from gripper_ai_controller.image_servo_simulation.simulator import ImageServoSimulationSession
from gripper_ai_controller.domain.models import BoundingBox2D
from gripper_ai_controller.pose.models import COCO_KEYPOINT_NAMES, PoseCandidate, PoseJoint2D


def make_target(x=0.78, y=0.68, captured_at=10.0, confidence=0.95):
    """Create one deterministic source-image wrist observation for pure control tests."""

    return ImageTargetObservation("fixture-camera", captured_at, "right_wrist", x, y, confidence)


def make_candidate(target_confidence=0.95, person_confidence=0.9, x=0.78, y=0.68):
    """Build a complete COCO person candidate without loading Torch or camera dependencies."""

    joints = []
    for index, name in enumerate(COCO_KEYPOINT_NAMES):
        if name == "right_wrist":
            joints.append(PoseJoint2D(name, x * 100.0, y * 100.0, x, y, target_confidence))
        else:
            joints.append(PoseJoint2D(name, float(index), 1.0, 0.2, 0.2, 0.9))
    return PoseCandidate(BoundingBox2D(0.1, 0.1, 0.5, 0.7), person_confidence, tuple(joints))


class ImageCenteringControllerTests(unittest.TestCase):
    """Verify matrix direction, deadband, bounds, and simulated convergence behavior."""

    def test_centered_target_generates_no_virtual_joint_motion(self):
        settings = ImageServoSimulationSettings(center_deadband=0.03)
        plan = ImageCenteringController(settings).plan(
            make_target(0.51, 0.49), settings.initial_joint_positions_rad, now=10.0
        )

        self.assertTrue(plan.centered)
        self.assertFalse(plan.executable)
        self.assertEqual((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), plan.applied_joint_deltas_rad)

    def test_horizontal_and_vertical_error_use_documented_virtual_joint_directions(self):
        settings = ImageServoSimulationSettings()
        controller = ImageCenteringController(settings)
        right_and_lower = controller.plan(
            make_target(0.78, 0.68), settings.initial_joint_positions_rad, now=10.0
        )

        self.assertTrue(right_and_lower.executable)
        self.assertGreater(right_and_lower.applied_joint_deltas_rad[0], 0.0)
        self.assertGreater(right_and_lower.applied_joint_deltas_rad[1], 0.0)
        session = ImageServoSimulationSession(settings)
        session.start(make_target(0.78, 0.68))
        step = session.step(now=10.0)
        self.assertLess(step.target_after.normalized_x, step.target_before.normalized_x)
        self.assertLess(step.target_after.normalized_y, step.target_before.normalized_y)

    def test_stale_or_invalid_target_holds_all_virtual_joint_positions(self):
        settings = ImageServoSimulationSettings(maximum_pose_age_seconds=0.5)
        controller = ImageCenteringController(settings)
        stale = controller.plan(make_target(captured_at=1.0), settings.initial_joint_positions_rad, now=2.0)
        invalid = controller.plan(make_target(x=1.2), settings.initial_joint_positions_rad, now=10.0)

        self.assertFalse(stale.executable)
        self.assertIn("too old", stale.reason)
        self.assertFalse(invalid.executable)
        self.assertIn("invalid", invalid.reason)
        self.assertEqual(settings.initial_joint_positions_rad, stale.target_joint_positions_rad)
        self.assertEqual(settings.initial_joint_positions_rad, invalid.target_joint_positions_rad)

    def test_joint_limit_prevents_a_false_success(self):
        settings = ImageServoSimulationSettings(
            joint_lower_limits_rad=(0.0, -1.5, -2.0, -2.8, -2.0, -3.1),
            joint_upper_limits_rad=(0.01, 1.5, 2.0, 2.8, 2.0, 3.1),
            initial_joint_positions_rad=(0.01, 0.0, 0.0, 0.0, 0.0, 0.0),
            image_jacobian=((-0.52, 0.0, 0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        )
        plan = ImageCenteringController(settings).plan(
            make_target(0.78, 0.5), settings.initial_joint_positions_rad, now=10.0
        )

        self.assertFalse(plan.executable)
        self.assertFalse(plan.centered)
        self.assertIn("limits", plan.reason)
        self.assertIn("base_yaw", plan.limited_joint_names)

    def test_direct_settings_with_a_malformed_jacobian_are_rejected(self):
        with self.assertRaises(ValueError):
            ImageCenteringController(
                ImageServoSimulationSettings(image_jacobian=((0.0, 0.0, 0.0, 0.0, 0.0, 0.0),))
            )

    def test_static_image_converges_monotonically_without_a_robot_command(self):
        settings = ImageServoSimulationSettings(maximum_iterations=40)
        result = run_static_image_centering(settings, make_target())

        errors = [
            abs(step.target_before.normalized_x - settings.desired_normalized_x)
            + abs(step.target_before.normalized_y - settings.desired_normalized_y)
            for step in result.steps
        ]
        self.assertTrue(result.centered)
        self.assertGreater(len(result.steps), 1)
        self.assertTrue(all(errors[index + 1] <= errors[index] for index in range(len(errors) - 1)))
        self.assertLessEqual(
            abs(result.final_target.normalized_x - settings.desired_normalized_x),
            settings.center_deadband,
        )
        self.assertLessEqual(
            abs(result.final_target.normalized_y - settings.desired_normalized_y),
            settings.center_deadband,
        )
        report = render_console_report(result)
        self.assertIn("simulation_only=true", report)
        self.assertIn("result=centered", report)
        self.assertNotIn("RobotCommand", report)

    def test_newer_source_observation_moves_the_virtual_target_before_the_next_correction(self):
        settings = ImageServoSimulationSettings()
        session = ImageServoSimulationSession(settings)
        session.start(make_target(0.5, 0.5, captured_at=10.0))
        self.assertTrue(session.step(now=10.0).plan.centered)

        session.observe_source_target(make_target(0.64, 0.42, captured_at=11.0))
        step = session.step(now=11.0)

        self.assertAlmostEqual(0.64, step.target_before.normalized_x)
        self.assertAlmostEqual(0.42, step.target_before.normalized_y)
        self.assertLess(abs(step.target_after.normalized_x - 0.5), abs(step.target_before.normalized_x - 0.5))
        self.assertLess(abs(step.target_after.normalized_y - 0.5), abs(step.target_before.normalized_y - 0.5))

    def test_source_observations_reject_duplicate_times_and_target_identity_changes(self):
        session = ImageServoSimulationSession(ImageServoSimulationSettings())
        session.start(make_target(captured_at=10.0))
        with self.assertRaises(ValueError):
            session.observe_source_target(make_target(captured_at=10.0))
        with self.assertRaises(ValueError):
            session.observe_source_target(
                ImageTargetObservation("other-camera", 11.0, "right_wrist", 0.7, 0.6, 0.95)
            )


class FixtureTargetSelectionTests(unittest.TestCase):
    """Verify only a qualified visible wrist can initialize a fixture simulation."""

    def test_target_selection_uses_the_highest_confidence_person_and_requested_wrist(self):
        lower = make_candidate(person_confidence=0.81, x=0.20, y=0.20)
        higher = make_candidate(person_confidence=0.95, x=0.72, y=0.36)

        target = select_image_target((lower, higher), "fixture-camera", 0.0, "right_wrist", 0.8, 0.6)

        self.assertEqual(0.72, target.normalized_x)
        self.assertEqual(0.36, target.normalized_y)

    def test_target_selection_rejects_missing_or_low_confidence_wrist(self):
        with self.assertRaises(ImageServoInputError):
            select_image_target((make_candidate(target_confidence=0.2),), "fixture-camera", 0.0, "right_wrist", 0.8, 0.6)
        with self.assertRaises(ImageServoInputError):
            select_image_target((), "fixture-camera", 0.0, "right_wrist", 0.8, 0.6)


class ImageServoConfigurationTests(unittest.TestCase):
    """Verify the dedicated JSON contract rejects device and malformed simulation settings."""

    def test_configuration_accepts_the_minimum_hardware_free_contract(self):
        payload = self._payload()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulation.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            configuration = load_image_servo_simulation_config(str(path))

        self.assertEqual("full-body-front", configuration.simulation_settings.fixture_id)
        self.assertEqual("right_wrist", configuration.pose_settings.target_joint)
        self.assertEqual("mono8", configuration.simulation_settings.pixel_format)

    def test_configuration_rejects_runtime_and_bad_kinematic_shapes(self):
        invalid_root = self._payload()
        invalid_root["targets"] = []
        invalid_jacobian = self._payload()
        invalid_jacobian["simulation"]["image_jacobian"] = [[0.0] * 6]
        for payload in (invalid_root, invalid_jacobian):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "simulation.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_image_servo_simulation_config(str(path))

    def test_offline_cli_import_does_not_load_runtime_or_jaka_modules(self):
        """Keep the static-image entry point structurally isolated from hardware composition."""

        probe = (
            "import sys; import gripper_ai_controller.cli; "
            "blocked = [name for name in sys.modules if 'runtime_builder' in name "
            "or 'adapters.jaka' in name or 'core.runtime' in name]; "
            "print(blocked); raise SystemExit(1 if blocked else 0)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    @staticmethod
    def _payload():
        """Return a local JSON object whose weight location follows project storage rules."""

        return {
            "pose": {
                "enabled": True,
                "weights_path": "localstore/models/example.pth",
                "target_joint": "right_wrist",
            },
            "simulation": {
                "fixture_id": "full-body-front",
                "pixel_format": "mono8",
            },
        }


if __name__ == "__main__":
    unittest.main()
