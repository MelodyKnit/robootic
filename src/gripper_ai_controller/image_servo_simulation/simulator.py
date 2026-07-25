"""In-memory image-plane model that predicts target movement from virtual joints."""

from typing import Optional, Tuple

from gripper_ai_controller.image_servo_simulation.controller import ImageCenteringController
from gripper_ai_controller.image_servo_simulation.models import (
    ImageCenteringPlan,
    ImageCenteringResult,
    ImageServoSimulationSettings,
    ImageTargetObservation,
    SimulationStep,
)


class ImageServoSimulationSession:
    """Run a finite virtual camera-arm experiment without adapter or network access.

    The session starts from a 2D joint location detected in a static image. Its image
    Jacobian predicts how the target would move if a virtual six-axis arm repositioned
    a tool-mounted camera. No physical motion, camera capture, persistent storage, or
    `RobotAdapter` call exists in this class.
    """

    def __init__(self, settings: ImageServoSimulationSettings) -> None:
        """Create an idle virtual arm at its configured in-memory joint state."""

        self.settings = settings
        self.controller = ImageCenteringController(settings)
        self._joint_positions_rad = settings.initial_joint_positions_rad
        self._target = None  # type: Optional[ImageTargetObservation]
        self._source_target = None  # type: Optional[ImageTargetObservation]
        self._step_index = 0

    @property
    def joint_positions_rad(self) -> Tuple[float, ...]:
        """Return the latest virtual six-axis state without exposing mutable storage."""

        return self._joint_positions_rad

    @property
    def target(self) -> Optional[ImageTargetObservation]:
        """Return the predicted target location in the virtual image plane."""

        return self._target

    def start(self, source_target: ImageTargetObservation) -> None:
        """Reset the simulation around one image observation without touching a device."""

        self._joint_positions_rad = self.settings.initial_joint_positions_rad
        self._target = source_target
        self._source_target = source_target
        self._step_index = 0

    def observe_source_target(self, source_target: ImageTargetObservation) -> None:
        """Incorporate one newer tracked joint observation into the virtual image plane.

        The physical camera is deliberately fixed during this simulation. A person's
        observed image displacement therefore shifts the current virtual projection by
        the same amount before the next simulated arm step. This exposes a small,
        testable input boundary for future live pose integration without starting a
        camera or treating a virtual correction as an actual robot movement.
        """

        previous_source = self._source_target
        if previous_source is None or self._target is None:
            self.start(source_target)
            return
        if (
            source_target.camera_id != previous_source.camera_id
            or source_target.joint_name != previous_source.joint_name
        ):
            raise ValueError("A simulation session cannot mix camera identifiers or target joints.")
        if source_target.captured_at <= previous_source.captured_at:
            raise ValueError("Source target observations must have strictly increasing capture times.")
        displacement_x = source_target.normalized_x - previous_source.normalized_x
        displacement_y = source_target.normalized_y - previous_source.normalized_y
        self._target = ImageTargetObservation(
            source_target.camera_id,
            source_target.captured_at,
            source_target.joint_name,
            self._target.normalized_x + displacement_x,
            self._target.normalized_y + displacement_y,
            source_target.confidence,
        )
        self._source_target = source_target

    def step(self, now: Optional[float] = None) -> SimulationStep:
        """Advance one virtual control step from the latest predicted image target."""

        if self._target is None:
            raise RuntimeError("A source target must be provided before image-centering simulation starts.")
        target_before = self._target
        plan = self.controller.plan(target_before, self._joint_positions_rad, now)
        target_after = target_before
        if plan.executable:
            target_after = self._project_target_after_joint_update(target_before, plan)
            self._joint_positions_rad = plan.target_joint_positions_rad
        self._target = target_after
        self._step_index += 1
        return SimulationStep(
            self._step_index,
            target_before,
            target_after,
            plan,
            self._joint_positions_rad,
        )

    def run_to_completion(
        self,
        fixture_id: str,
        pixel_format: str,
        source_target: ImageTargetObservation,
        now: Optional[float] = None,
    ) -> ImageCenteringResult:
        """Run a bounded static-image experiment until centered, blocked, or exhausted."""

        self.start(source_target)
        steps = []
        last_plan = None  # type: Optional[ImageCenteringPlan]
        reason = "The configured maximum simulation iterations were reached."
        centered = False
        for _ in range(self.settings.maximum_iterations):
            step = self.step(now)
            steps.append(step)
            last_plan = step.plan
            if step.plan.centered:
                centered = True
                reason = step.plan.reason
                break
            if not step.plan.executable:
                reason = step.plan.reason
                break
        if self._target is None:
            raise RuntimeError("Image-centering simulation terminated without a target state.")
        return ImageCenteringResult(
            fixture_id,
            pixel_format,
            source_target.joint_name,
            source_target,
            self._target,
            self._joint_positions_rad,
            centered,
            reason,
            tuple(steps),
            last_plan,
        )

    def _project_target_after_joint_update(
        self, target: ImageTargetObservation, plan: ImageCenteringPlan
    ) -> ImageTargetObservation:
        """Apply the configured image Jacobian to predict one virtual camera view update."""

        horizontal, vertical = self.settings.image_jacobian
        delta_x = sum(
            horizontal[index] * plan.applied_joint_deltas_rad[index]
            for index in range(len(horizontal))
        )
        delta_y = sum(
            vertical[index] * plan.applied_joint_deltas_rad[index]
            for index in range(len(vertical))
        )
        return ImageTargetObservation(
            target.camera_id,
            target.captured_at,
            target.joint_name,
            target.normalized_x + delta_x,
            target.normalized_y + delta_y,
            target.confidence,
        )
