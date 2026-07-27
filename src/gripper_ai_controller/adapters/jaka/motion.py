"""Pure JAKA Zu 3 joint-motion validation and dry-run preview primitives."""

import math
from dataclasses import dataclass
from numbers import Real
from typing import Optional, Tuple, Union

from gripper_ai_controller.domain.models import (
    JointMoveMode,
    RobotAction,
    RobotCommand,
    RobotStatus,
    SafetyDecision,
)
from gripper_ai_controller.domain.ports import RobotMotionConstraint


JAKA_ZU3_HARD_JOINT_LOWER_LIMITS_RAD = (
    -2.0 * math.pi,
    math.radians(-85.0),
    math.radians(-175.0),
    math.radians(-85.0),
    -2.0 * math.pi,
    -2.0 * math.pi,
)
"""Documented physical lower joint bounds for JAKA Zu 3, ordered J1 through J6."""

JAKA_ZU3_HARD_JOINT_UPPER_LIMITS_RAD = (
    2.0 * math.pi,
    math.radians(265.0),
    math.radians(175.0),
    math.radians(265.0),
    2.0 * math.pi,
    2.0 * math.pi,
)
"""Documented physical upper joint bounds for JAKA Zu 3, ordered J1 through J6."""

JAKA_ZU3_HARD_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND = math.pi
"""Physical maximum JAKA Zu 3 joint speed accepted by this motion boundary."""

JAKA_ZU3_DEFAULT_SOFT_LIMIT_MARGIN_RAD = math.radians(10.0)
"""Safety margin between physical and default software joint limits."""

JAKA_ZU3_DEFAULT_JOINT_LOWER_LIMITS_RAD = tuple(
    limit + JAKA_ZU3_DEFAULT_SOFT_LIMIT_MARGIN_RAD
    for limit in JAKA_ZU3_HARD_JOINT_LOWER_LIMITS_RAD
)
"""Default lower software joint limits, inset 10 degrees from physical limits."""

JAKA_ZU3_DEFAULT_JOINT_UPPER_LIMITS_RAD = tuple(
    limit - JAKA_ZU3_DEFAULT_SOFT_LIMIT_MARGIN_RAD
    for limit in JAKA_ZU3_HARD_JOINT_UPPER_LIMITS_RAD
)
"""Default upper software joint limits, inset 10 degrees from physical limits."""

JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND = 0.5
"""Conservative default maximum joint speed for offline command approval."""

JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_STEP_RAD = math.pi / 18.0
"""Conservative default maximum per-axis joint change of 10 degrees."""


class JakaMotionValidationError(ValueError):
    """Report one invalid or unsafe JAKA dry-run motion proposal."""


class JakaSourcePositionChangedError(JakaMotionValidationError):
    """Report telemetry that no longer matches a human-confirmed motion preview."""


class JakaMotionCompletionError(JakaMotionValidationError):
    """Report a blocking joint move that did not reach its verified final state."""


@dataclass(frozen=True)
class JakaJointMoveRequest:
    """One validated, blocking JAKA ``joint_move`` request without an SDK dependency."""

    joint_positions_rad: Tuple[float, float, float, float, float, float]
    move_mode: JointMoveMode
    is_blocking: bool
    speed_rad_per_second: float

    @property
    def sdk_method(self) -> str:
        """Return the documented JAKA SDK operation name for this request."""

        return "joint_move"

    @property
    def sdk_move_mode(self) -> int:
        """Return the numeric JAKA SDK mode used by the official ``joint_move`` call."""

        if self.move_mode == JointMoveMode.ABSOLUTE:
            return 0
        return 1

    @property
    def sdk_move_mode_label(self) -> str:
        """Return a symbolic display label for the numeric JAKA SDK mode."""

        if self.move_mode == JointMoveMode.ABSOLUTE:
            return "ABS"
        return "INCR"

    @property
    def sdk_arguments(self) -> Tuple[object, ...]:
        """Return the SDK-shaped ``joint_move`` arguments for inspection only."""

        return (
            self.joint_positions_rad,
            self.sdk_move_mode,
            self.is_blocking,
            self.speed_rad_per_second,
        )


@dataclass(frozen=True)
class JakaMotionAbortRequest:
    """One logical JAKA ``motion_abort`` request without an SDK dependency."""

    @property
    def sdk_method(self) -> str:
        """Return the documented JAKA SDK operation name for this request."""

        return "motion_abort"

    @property
    def sdk_arguments(self) -> Tuple[object, ...]:
        """Return the SDK-shaped ``motion_abort`` arguments for inspection only."""

        return ()


@dataclass(frozen=True)
class JakaJointMovePreview:
    """Immutable compilation result for a JAKA joint movement or motion abort."""

    request: Union[JakaJointMoveRequest, JakaMotionAbortRequest]
    source_joint_positions_rad: Tuple[float, float, float, float, float, float]
    target_joint_positions_rad: Tuple[float, float, float, float, float, float]
    joint_deltas_rad: Tuple[float, float, float, float, float, float]
    estimated_duration_seconds: float
    sdk_method: str
    sdk_arguments: Tuple[object, ...]


class JakaJointMotionCompiler:
    """Compile normalized robot commands against conservative JAKA Zu 3 limits.

    The compiler is deliberately pure. It performs no SDK import, network I/O, or
    state mutation, so the same validation is usable by an execution target and by
    the dry-run adapter that predicts an approved command's resulting joint state.
    """

    def __init__(
        self,
        joint_lower_limits_rad: Optional[object] = None,
        joint_upper_limits_rad: Optional[object] = None,
        maximum_joint_speed_rad_per_second: Optional[object] = None,
        maximum_joint_step_rad: Optional[object] = None,
    ) -> None:
        """Configure soft limits that may tighten, but never relax, safe defaults."""

        lower_limits = self._normalize_joint_vector(
            "joint_lower_limits_rad",
            JAKA_ZU3_DEFAULT_JOINT_LOWER_LIMITS_RAD
            if joint_lower_limits_rad is None
            else joint_lower_limits_rad,
        )
        upper_limits = self._normalize_joint_vector(
            "joint_upper_limits_rad",
            JAKA_ZU3_DEFAULT_JOINT_UPPER_LIMITS_RAD
            if joint_upper_limits_rad is None
            else joint_upper_limits_rad,
        )
        self._validate_soft_limits(lower_limits, upper_limits)
        self.joint_lower_limits_rad = lower_limits
        self.joint_upper_limits_rad = upper_limits

        self.maximum_joint_speed_rad_per_second = self._normalize_positive_scalar(
            "maximum_joint_speed_rad_per_second",
            JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND
            if maximum_joint_speed_rad_per_second is None
            else maximum_joint_speed_rad_per_second,
        )
        if self.maximum_joint_speed_rad_per_second > JAKA_ZU3_HARD_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND:
            raise JakaMotionValidationError(
                "maximum_joint_speed_rad_per_second cannot exceed the JAKA Zu 3 hard limit of {0} rad/s.".format(
                    JAKA_ZU3_HARD_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND
                )
            )
        if self.maximum_joint_speed_rad_per_second > JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND:
            raise JakaMotionValidationError(
                "maximum_joint_speed_rad_per_second cannot relax the conservative default of {0} rad/s.".format(
                    JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_SPEED_RAD_PER_SECOND
                )
            )

        self.maximum_joint_step_rad = self._normalize_positive_scalar(
            "maximum_joint_step_rad",
            JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_STEP_RAD
            if maximum_joint_step_rad is None
            else maximum_joint_step_rad,
        )
        if self.maximum_joint_step_rad > JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_STEP_RAD:
            raise JakaMotionValidationError(
                "maximum_joint_step_rad cannot relax the conservative default of {0} rad.".format(
                    JAKA_ZU3_DEFAULT_MAXIMUM_JOINT_STEP_RAD
                )
            )

    def validate_initial_joint_positions(
        self, values: object
    ) -> Tuple[float, float, float, float, float, float]:
        """Validate an adapter's initial predicted joints against configured soft limits."""

        positions = self._normalize_joint_vector("initial_joint_positions_rad", values)
        self._ensure_within_soft_limits(positions, "Initial joint position")
        return positions

    def preview(self, command: RobotCommand, status: RobotStatus) -> JakaJointMovePreview:
        """Return the immutable dry-run result for one normalized robot command."""

        return self.compile(command, status)

    def compile(self, command: RobotCommand, status: RobotStatus) -> JakaJointMovePreview:
        """Compile one ``MOVE_JOINTS`` or ``STOP`` command without performing I/O."""

        if not isinstance(command, RobotCommand):
            raise JakaMotionValidationError("JAKA motion commands must use RobotCommand.")
        if not isinstance(status, RobotStatus):
            raise JakaMotionValidationError("JAKA motion previews require RobotStatus.")
        if not isinstance(command.action, RobotAction):
            raise JakaMotionValidationError("Robot command action must use RobotAction.")

        source_positions = self._normalize_joint_vector(
            "Robot status joint_positions_rad", status.joint_positions_rad
        )

        if command.action == RobotAction.STOP:
            request = JakaMotionAbortRequest()
            return JakaJointMovePreview(
                request=request,
                source_joint_positions_rad=source_positions,
                target_joint_positions_rad=source_positions,
                joint_deltas_rad=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                estimated_duration_seconds=0.0,
                sdk_method=request.sdk_method,
                sdk_arguments=request.sdk_arguments,
            )

        if command.action != RobotAction.MOVE_JOINTS:
            raise JakaMotionValidationError(
                "JAKA dry-run motion supports only MOVE_JOINTS and STOP commands."
            )
        if not isinstance(command.joint_move_mode, JointMoveMode):
            raise JakaMotionValidationError("JAKA joint_move_mode must use JointMoveMode.")
        self._ensure_within_hard_limits(source_positions, "Robot status joint position")

        requested_positions = self._normalize_joint_vector(
            "Robot command joint_positions_rad", command.joint_positions_rad
        )
        speed = self._normalize_positive_scalar("Robot command speed", command.speed)
        if speed > self.maximum_joint_speed_rad_per_second:
            raise JakaMotionValidationError(
                "Robot command speed exceeds the configured maximum of {0} rad/s.".format(
                    self.maximum_joint_speed_rad_per_second
                )
            )

        if command.joint_move_mode == JointMoveMode.ABSOLUTE:
            target_positions = requested_positions
        else:
            target_positions = tuple(
                source + delta for source, delta in zip(source_positions, requested_positions)
            )
        target_positions = self._normalize_joint_vector("Resolved target joint positions", target_positions)
        self._ensure_within_soft_limits(target_positions, "Resolved target joint position")

        deltas = tuple(
            target - source for source, target in zip(source_positions, target_positions)
        )
        if any(abs(delta) > self.maximum_joint_step_rad for delta in deltas):
            raise JakaMotionValidationError(
                "Robot command exceeds the configured per-axis joint step of {0} rad.".format(
                    self.maximum_joint_step_rad
                )
            )

        request = JakaJointMoveRequest(
            joint_positions_rad=requested_positions,
            move_mode=command.joint_move_mode,
            is_blocking=True,
            speed_rad_per_second=speed,
        )
        return JakaJointMovePreview(
            request=request,
            source_joint_positions_rad=source_positions,
            target_joint_positions_rad=target_positions,
            joint_deltas_rad=deltas,
            estimated_duration_seconds=max(abs(delta) for delta in deltas) / speed,
            sdk_method=request.sdk_method,
            sdk_arguments=request.sdk_arguments,
        )

    def require_source_position_match(
        self,
        expected_positions: object,
        actual_positions: object,
        tolerance_rad: object,
    ) -> None:
        """Reject a move when final pre-dispatch telemetry differs from its preview source."""

        self._require_positions_within_tolerance(
            expected_positions,
            actual_positions,
            tolerance_rad,
            "JAKA joint telemetry changed after the preview",
            JakaSourcePositionChangedError,
        )

    def require_completed_position_match(
        self,
        expected_positions: object,
        actual_positions: object,
        tolerance_rad: object,
    ) -> None:
        """Reject a blocking move that does not report every target within tolerance."""

        self._require_positions_within_tolerance(
            expected_positions,
            actual_positions,
            tolerance_rad,
            "JAKA joint_move did not reach the requested target",
            JakaMotionCompletionError,
        )

    @classmethod
    def _require_positions_within_tolerance(
        cls,
        expected_positions: object,
        actual_positions: object,
        tolerance_rad: object,
        message: str,
        error_type,
    ) -> None:
        """Compare finite six-axis vectors without applying target soft-limit rules."""

        expected = cls._normalize_joint_vector("Expected joint positions", expected_positions)
        actual = cls._normalize_joint_vector("Actual joint positions", actual_positions)
        tolerance = cls._normalize_positive_scalar("Joint position tolerance", tolerance_rad)
        for index, (expected_value, actual_value) in enumerate(zip(expected, actual), start=1):
            if abs(expected_value - actual_value) > tolerance:
                raise error_type(
                    "{0}: J{1} differs by more than {2} rad.".format(
                        message,
                        index,
                        tolerance,
                    )
                )

    @classmethod
    def _normalize_joint_vector(
        cls, name: str, values: object
    ) -> Tuple[float, float, float, float, float, float]:
        """Normalize one JSON-compatible six-axis numeric array into an immutable tuple."""

        if not isinstance(values, (list, tuple)):
            raise JakaMotionValidationError("{0} must be a JSON array with exactly six values.".format(name))
        if len(values) != 6:
            raise JakaMotionValidationError("{0} must contain exactly six values.".format(name))
        normalized = tuple(cls._normalize_finite_number("{0}[{1}]".format(name, index), value) for index, value in enumerate(values))
        return normalized  # type: ignore

    @staticmethod
    def _normalize_positive_scalar(name: str, value: object) -> float:
        """Normalize one strictly positive, finite JSON-compatible number."""

        normalized = JakaJointMotionCompiler._normalize_finite_number(name, value)
        if normalized <= 0.0:
            raise JakaMotionValidationError("{0} must be greater than zero.".format(name))
        return normalized

    @staticmethod
    def _normalize_finite_number(name: str, value: object) -> float:
        """Reject booleans, non-numeric values, and non-finite numeric values."""

        if isinstance(value, bool) or not isinstance(value, Real):
            raise JakaMotionValidationError("{0} must be a finite numeric value.".format(name))
        normalized = float(value)
        if not math.isfinite(normalized):
            raise JakaMotionValidationError("{0} must be a finite numeric value.".format(name))
        return normalized

    @staticmethod
    def _validate_soft_limits(
        lower_limits: Tuple[float, float, float, float, float, float],
        upper_limits: Tuple[float, float, float, float, float, float],
    ) -> None:
        """Reject soft-limit configurations that exceed physical or default safety bounds."""

        for index, (lower, upper, hard_lower, hard_upper, default_lower, default_upper) in enumerate(
            zip(
                lower_limits,
                upper_limits,
                JAKA_ZU3_HARD_JOINT_LOWER_LIMITS_RAD,
                JAKA_ZU3_HARD_JOINT_UPPER_LIMITS_RAD,
                JAKA_ZU3_DEFAULT_JOINT_LOWER_LIMITS_RAD,
                JAKA_ZU3_DEFAULT_JOINT_UPPER_LIMITS_RAD,
            ),
            start=1,
        ):
            if lower < hard_lower or upper > hard_upper:
                raise JakaMotionValidationError(
                    "J{0} soft limits must stay within JAKA Zu 3 physical bounds.".format(index)
                )
            if lower < default_lower or upper > default_upper:
                raise JakaMotionValidationError(
                    "J{0} soft limits cannot relax the default 10-degree safety margin.".format(index)
                )
            if lower >= upper:
                raise JakaMotionValidationError("J{0} lower soft limit must be less than its upper limit.".format(index))

    @staticmethod
    def _ensure_within_hard_limits(
        positions: Tuple[float, float, float, float, float, float], name: str
    ) -> None:
        """Reject an impossible current state while retaining the ability to preview STOP."""

        for index, (position, lower, upper) in enumerate(
            zip(
                positions,
                JAKA_ZU3_HARD_JOINT_LOWER_LIMITS_RAD,
                JAKA_ZU3_HARD_JOINT_UPPER_LIMITS_RAD,
            ),
            start=1,
        ):
            if position < lower or position > upper:
                raise JakaMotionValidationError(
                    "{0} J{1} is outside JAKA Zu 3 physical bounds.".format(name, index)
                )

    def _ensure_within_soft_limits(
        self, positions: Tuple[float, float, float, float, float, float], name: str
    ) -> None:
        """Reject a target that would leave configured software safety bounds."""

        for index, (position, lower, upper) in enumerate(
            zip(positions, self.joint_lower_limits_rad, self.joint_upper_limits_rad), start=1
        ):
            if position < lower or position > upper:
                raise JakaMotionValidationError(
                    "{0} J{1} is outside configured JAKA soft limits.".format(name, index)
                )


class JakaMotionConstraint(RobotMotionConstraint):
    """Expose JAKA-specific pure motion validation through the target constraint port."""

    def __init__(self, compiler: JakaJointMotionCompiler) -> None:
        """Bind the one compiler shared with the dry-run adapter's execution path."""

        if not isinstance(compiler, JakaJointMotionCompiler):
            raise TypeError("JakaMotionConstraint requires a JakaJointMotionCompiler.")
        self.compiler = compiler

    def evaluate(self, command: RobotCommand, status: RobotStatus) -> SafetyDecision:
        """Return whether the compiler can safely preview one normalized command."""

        try:
            self.compiler.compile(command, status)
        except (TypeError, ValueError) as error:
            return SafetyDecision(False, "JAKA motion constraint rejected command: {0}".format(error))
        return SafetyDecision(True, "JAKA motion command passed configured dry-run limits.")
