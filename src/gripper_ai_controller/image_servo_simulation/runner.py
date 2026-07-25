"""Fixture-to-simulation orchestration and deterministic console rendering."""

from typing import Iterable, Optional, Tuple

from gripper_ai_controller.image_servo_simulation.models import (
    ImageCenteringResult,
    ImageServoSimulationSettings,
    ImageTargetObservation,
    SIMULATED_JOINT_NAMES,
)
from gripper_ai_controller.image_servo_simulation.simulator import ImageServoSimulationSession
from gripper_ai_controller.pose.models import PoseCandidate


class ImageServoInputError(ValueError):
    """Report a fixture pose that cannot safely initialize virtual image centering."""


def select_image_target(
    candidates: Iterable[PoseCandidate],
    camera_id: str,
    captured_at: float,
    target_joint: str,
    person_confidence_threshold: float,
    joint_confidence_threshold: float,
) -> ImageTargetObservation:
    """Select the highest-confidence qualified person and its visible target joint."""

    qualified = tuple(
        candidate for candidate in candidates if candidate.confidence >= person_confidence_threshold
    )
    if not qualified:
        raise ImageServoInputError("No sufficiently confident person was detected in the selected fixture.")
    selected = max(qualified, key=lambda candidate: candidate.confidence)
    joint = next((item for item in selected.joints if item.name == target_joint), None)
    if joint is None:
        raise ImageServoInputError("The selected fixture does not contain the requested target joint.")
    if joint.confidence < joint_confidence_threshold:
        raise ImageServoInputError("The selected fixture target joint has insufficient confidence.")
    if not (0.0 <= joint.normalized_x <= 1.0 and 0.0 <= joint.normalized_y <= 1.0):
        raise ImageServoInputError("The selected fixture target joint is outside the image bounds.")
    return ImageTargetObservation(
        camera_id,
        captured_at,
        target_joint,
        joint.normalized_x,
        joint.normalized_y,
        joint.confidence,
    )


def run_static_image_centering(
    settings: ImageServoSimulationSettings,
    target: ImageTargetObservation,
) -> ImageCenteringResult:
    """Run a deterministic in-memory convergence experiment from one static target."""

    session = ImageServoSimulationSession(settings)
    return session.run_to_completion(
        settings.fixture_id,
        settings.pixel_format,
        target,
        now=target.captured_at,
    )


def render_console_report(result: ImageCenteringResult) -> str:
    """Build a concise operator-facing report that documents the simulation-only boundary."""

    lines = [
        "simulation_only=true",
        "fixture_id={0} pixel_format={1} target_joint={2}".format(
            result.fixture_id,
            result.pixel_format,
            result.target_joint,
        ),
        "initial_target=({0:.4f}, {1:.4f})".format(
            result.initial_target.normalized_x,
            result.initial_target.normalized_y,
        ),
    ]
    for step in result.steps:
        delta_text = ", ".join(
            "{0}={1:+.4f}".format(SIMULATED_JOINT_NAMES[index], delta)
            for index, delta in enumerate(step.plan.applied_joint_deltas_rad)
            if abs(delta) > 1e-12
        )
        joint_text = ", ".join(
            "{0}={1:+.4f}".format(SIMULATED_JOINT_NAMES[index], value)
            for index, value in enumerate(step.joint_positions_rad)
        )
        lines.append(
            "step={0} target_before=({1:.4f}, {2:.4f}) target_after=({3:.4f}, {4:.4f}) "
            "error=({5:+.4f}, {6:+.4f}) deltas=[{7}] joints=[{8}]".format(
                step.step_index,
                step.target_before.normalized_x,
                step.target_before.normalized_y,
                step.target_after.normalized_x,
                step.target_after.normalized_y,
                step.plan.error_x,
                step.plan.error_y,
                delta_text or "none",
                joint_text,
            )
        )
    lines.append(
        "result={0} final_target=({1:.4f}, {2:.4f}) reason={3}".format(
            "centered" if result.centered else "not_centered",
            result.final_target.normalized_x,
            result.final_target.normalized_y,
            result.reason,
        )
    )
    return "\n".join(lines)
