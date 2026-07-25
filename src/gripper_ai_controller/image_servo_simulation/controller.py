"""Pure damped-least-squares controller for centering a target in a virtual image."""

import math
from typing import Optional, Tuple

from gripper_ai_controller.image_servo_simulation.models import (
    ImageCenteringPlan,
    ImageServoSimulationSettings,
    ImageTargetObservation,
    SIMULATED_JOINT_NAMES,
)


class ImageCenteringController:
    """Generate bounded virtual-joint changes without importing a device adapter.

    The controller receives only an image-plane observation and a virtual joint state.
    It has no mechanism to open an SDK, construct a runtime, emit a RobotCommand, or
    call an execution port. A future hardware integration must add calibrated planning
    and safety authorization outside this simulation package.
    """

    def __init__(self, settings: ImageServoSimulationSettings) -> None:
        """Store already-validated simulation constants for repeatable calculations."""

        self._validate_settings(settings)
        self.settings = settings

    def plan(
        self,
        target: ImageTargetObservation,
        current_joint_positions_rad: Tuple[float, ...],
        now: Optional[float] = None,
    ) -> ImageCenteringPlan:
        """Return one bounded image-centering plan or a reason to hold virtual state."""

        self._validate_joint_positions(current_joint_positions_rad)
        if now is not None and now - target.captured_at > self.settings.maximum_pose_age_seconds:
            return self._hold_plan(
                target,
                current_joint_positions_rad,
                "The pose observation is too old for image-centering simulation.",
            )
        if not self._target_coordinates_are_valid(target):
            return self._hold_plan(
                target,
                current_joint_positions_rad,
                "The image target coordinates are invalid for image-centering simulation.",
            )

        error_x = self.settings.desired_normalized_x - target.normalized_x
        error_y = self.settings.desired_normalized_y - target.normalized_y
        if (
            abs(error_x) <= self.settings.center_deadband
            and abs(error_y) <= self.settings.center_deadband
        ):
            return ImageCenteringPlan(
                target,
                False,
                True,
                "The virtual target is inside the configured image-center deadband.",
                error_x,
                error_y,
                self._zero_joint_vector(),
                self._zero_joint_vector(),
                current_joint_positions_rad,
                (),
            )

        requested_deltas = self._damped_least_squares(error_x, error_y)
        target_positions = []
        applied_deltas = []
        limited_names = []
        for index, requested_delta in enumerate(requested_deltas):
            current = current_joint_positions_rad[index]
            lower = self.settings.joint_lower_limits_rad[index]
            upper = self.settings.joint_upper_limits_rad[index]
            next_position = min(max(current + requested_delta, lower), upper)
            applied_delta = next_position - current
            target_positions.append(next_position)
            applied_deltas.append(applied_delta)
            if not math.isclose(applied_delta, requested_delta, abs_tol=1e-12):
                limited_names.append(SIMULATED_JOINT_NAMES[index])

        applied_deltas_tuple = tuple(applied_deltas)
        if all(math.isclose(delta, 0.0, abs_tol=1e-12) for delta in applied_deltas_tuple):
            return ImageCenteringPlan(
                target,
                False,
                False,
                "Virtual joint limits prevent the target from moving toward image center.",
                error_x,
                error_y,
                requested_deltas,
                applied_deltas_tuple,
                tuple(target_positions),
                tuple(limited_names),
            )
        return ImageCenteringPlan(
            target,
            True,
            False,
            "A bounded virtual image-centering step is available.",
            error_x,
            error_y,
            requested_deltas,
            applied_deltas_tuple,
            tuple(target_positions),
            tuple(limited_names),
        )

    def _damped_least_squares(self, error_x: float, error_y: float) -> Tuple[float, ...]:
        """Solve a two-row damped image Jacobian without a numerical-library dependency."""

        horizontal, vertical = self.settings.image_jacobian
        damping_squared = self.settings.damping * self.settings.damping
        a00 = damping_squared + sum(value * value for value in horizontal)
        a01 = sum(horizontal[index] * vertical[index] for index in range(len(horizontal)))
        a11 = damping_squared + sum(value * value for value in vertical)
        determinant = a00 * a11 - a01 * a01
        if determinant <= 0.0 or not math.isfinite(determinant):
            return self._zero_joint_vector()
        inverse_error_x = (a11 * error_x - a01 * error_y) / determinant
        inverse_error_y = (-a01 * error_x + a00 * error_y) / determinant
        deltas = []
        for index in range(len(horizontal)):
            raw_delta = self.settings.gain * (
                horizontal[index] * inverse_error_x + vertical[index] * inverse_error_y
            )
            deltas.append(
                min(
                    max(raw_delta, -self.settings.maximum_joint_step_rad),
                    self.settings.maximum_joint_step_rad,
                )
            )
        return tuple(deltas)

    def _hold_plan(
        self,
        target: ImageTargetObservation,
        current_joint_positions_rad: Tuple[float, ...],
        reason: str,
    ) -> ImageCenteringPlan:
        """Return a no-motion result while retaining the source observation for diagnostics."""

        return ImageCenteringPlan(
            target,
            False,
            False,
            reason,
            self.settings.desired_normalized_x - target.normalized_x,
            self.settings.desired_normalized_y - target.normalized_y,
            self._zero_joint_vector(),
            self._zero_joint_vector(),
            current_joint_positions_rad,
            (),
        )

    @staticmethod
    def _target_coordinates_are_valid(target: ImageTargetObservation) -> bool:
        """Reject non-finite and out-of-image values before matrix arithmetic starts."""

        return (
            math.isfinite(target.normalized_x)
            and math.isfinite(target.normalized_y)
            and 0.0 <= target.normalized_x <= 1.0
            and 0.0 <= target.normalized_y <= 1.0
        )

    def _validate_joint_positions(self, values: Tuple[float, ...]) -> None:
        """Protect the simulation state from malformed or non-finite vector input."""

        expected = len(SIMULATED_JOINT_NAMES)
        if len(values) != expected or any(not math.isfinite(value) for value in values):
            raise ValueError("Virtual joint positions must contain six finite values.")

    @staticmethod
    def _validate_settings(settings: ImageServoSimulationSettings) -> None:
        """Fail early when direct API callers bypass the strict JSON configuration loader."""

        expected = len(SIMULATED_JOINT_NAMES)
        vectors = (
            settings.initial_joint_positions_rad,
            settings.joint_lower_limits_rad,
            settings.joint_upper_limits_rad,
        )
        if any(len(vector) != expected for vector in vectors):
            raise ValueError("Virtual joint setting vectors must contain six values.")
        if len(settings.image_jacobian) != 2 or any(
            len(row) != expected for row in settings.image_jacobian
        ):
            raise ValueError("The virtual image Jacobian must contain two rows and six columns.")
        numeric_values = [
            settings.desired_normalized_x,
            settings.desired_normalized_y,
            settings.center_deadband,
            settings.maximum_pose_age_seconds,
            settings.maximum_joint_step_rad,
            settings.gain,
            settings.damping,
        ]
        numeric_values.extend(value for vector in vectors for value in vector)
        numeric_values.extend(value for row in settings.image_jacobian for value in row)
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("Virtual image-centering settings must be finite.")
        if not (0.0 <= settings.desired_normalized_x <= 1.0 and 0.0 <= settings.desired_normalized_y <= 1.0):
            raise ValueError("The desired virtual image target must lie inside normalized image bounds.")
        if settings.center_deadband < 0.0 or settings.maximum_joint_step_rad <= 0.0:
            raise ValueError("The virtual deadband and joint step limit must be non-negative and positive respectively.")
        if settings.maximum_pose_age_seconds <= 0.0 or settings.damping <= 0.0 or settings.gain <= 0.0:
            raise ValueError("The virtual controller timing, damping, and gain must be positive.")
        if settings.maximum_iterations < 1:
            raise ValueError("The virtual simulation must allow at least one iteration.")
        for index in range(expected):
            lower = settings.joint_lower_limits_rad[index]
            upper = settings.joint_upper_limits_rad[index]
            initial = settings.initial_joint_positions_rad[index]
            if lower >= upper or initial < lower or initial > upper:
                raise ValueError("Virtual joint limits and initial positions are inconsistent.")
        if all(math.isclose(value, 0.0, abs_tol=1e-12) for row in settings.image_jacobian for value in row):
            raise ValueError("The virtual image Jacobian must contain at least one non-zero value.")

    @staticmethod
    def _zero_joint_vector() -> Tuple[float, ...]:
        """Return a fresh immutable six-axis zero vector used by hold and centered plans."""

        return tuple(0.0 for _ in SIMULATED_JOINT_NAMES)
