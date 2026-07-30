"""Offline rigid-transform fitting for board reference points taught by an operator."""

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence, Tuple

import numpy

from gripper_ai_controller.calibration.errors import CalibrationComputationError, CalibrationValidationError
from gripper_ai_controller.calibration.models import Point3D


_ROTATION_TOLERANCE = 1e-6


@dataclass(frozen=True)
class RigidTransform:
    """A proper rigid transform that maps ``source_frame`` points into ``target_frame``."""

    source_frame: str
    target_frame: str
    rotation_matrix: Tuple[Tuple[float, float, float], ...]
    translation_mm: Point3D

    def __post_init__(self) -> None:
        """Reject reflections, scaling, and malformed transforms before projection uses them."""

        if not isinstance(self.source_frame, str) or not self.source_frame.strip():
            raise CalibrationValidationError("source_frame must be a non-empty string.")
        if not isinstance(self.target_frame, str) or not self.target_frame.strip():
            raise CalibrationValidationError("target_frame must be a non-empty string.")
        rows = tuple(tuple(row) for row in self.rotation_matrix)
        if len(rows) != 3 or any(len(row) != 3 for row in rows):
            raise CalibrationValidationError("rotation_matrix must be 3 by 3.")
        try:
            matrix = numpy.asarray(rows, dtype=numpy.float64)
        except (TypeError, ValueError) as error:
            raise CalibrationValidationError("rotation_matrix values must be finite numbers.") from error
        if not numpy.isfinite(matrix).all():
            raise CalibrationValidationError("rotation_matrix values must be finite numbers.")
        if not numpy.allclose(matrix.T.dot(matrix), numpy.identity(3), atol=_ROTATION_TOLERANCE):
            raise CalibrationValidationError("rotation_matrix must be orthonormal.")
        if not math.isclose(float(numpy.linalg.det(matrix)), 1.0, abs_tol=_ROTATION_TOLERANCE):
            raise CalibrationValidationError("rotation_matrix must have determinant +1.")
        if not isinstance(self.translation_mm, Point3D):
            raise CalibrationValidationError("translation_mm must be a Point3D.")
        object.__setattr__(
            self,
            "rotation_matrix",
            tuple(tuple(float(value) for value in row) for row in matrix.tolist()),
        )

    @classmethod
    def identity(cls, frame_id: str) -> "RigidTransform":
        """Create an explicit identity transform for one named frame."""

        return cls(
            frame_id,
            frame_id,
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(0.0, 0.0, 0.0),
        )

    def apply(self, point: Point3D) -> Point3D:
        """Map one source-frame point into the target frame."""

        if not isinstance(point, Point3D):
            raise CalibrationValidationError("point must be a Point3D.")
        rotated = numpy.asarray(self.rotation_matrix, dtype=numpy.float64).dot(
            numpy.asarray(point.as_tuple(), dtype=numpy.float64)
        )
        translated = rotated + numpy.asarray(self.translation_mm.as_tuple(), dtype=numpy.float64)
        return Point3D(float(translated[0]), float(translated[1]), float(translated[2]))

    def inverse(self) -> "RigidTransform":
        """Return the inverse frame mapping without recomputing a fit."""

        matrix = numpy.asarray(self.rotation_matrix, dtype=numpy.float64)
        inverse_matrix = matrix.T
        inverse_translation = -inverse_matrix.dot(
            numpy.asarray(self.translation_mm.as_tuple(), dtype=numpy.float64)
        )
        return RigidTransform(
            self.target_frame,
            self.source_frame,
            tuple(tuple(float(value) for value in row) for row in inverse_matrix.tolist()),
            Point3D(
                float(inverse_translation[0]),
                float(inverse_translation[1]),
                float(inverse_translation[2]),
            ),
        )

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-safe rigid transform record."""

        return {
            "source_frame": self.source_frame,
            "target_frame": self.target_frame,
            "rotation_matrix": [list(row) for row in self.rotation_matrix],
            "translation_mm": self.translation_mm.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RigidTransform":
        """Load a transform only after validating its rigid-body constraints."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("Rigid transform must be a JSON object.")
        return cls(
            source_frame=value.get("source_frame"),
            target_frame=value.get("target_frame"),
            rotation_matrix=value.get("rotation_matrix"),
            translation_mm=Point3D.from_dict(value.get("translation_mm")),
        )


@dataclass(frozen=True)
class RigidTransformFitResult:
    """A board-to-JAKA-base fit with operator-reviewable residual statistics."""

    transform: RigidTransform
    point_count: int
    rms_error_mm: float
    maximum_error_mm: float

    def __post_init__(self) -> None:
        """Keep fit metrics finite so a stored result cannot hide a failed calculation."""

        if not isinstance(self.transform, RigidTransform):
            raise CalibrationValidationError("transform must be a RigidTransform.")
        if type(self.point_count) is not int or self.point_count < 4:
            raise CalibrationValidationError("point_count must be an integer of at least 4.")
        rms = float(self.rms_error_mm)
        maximum = float(self.maximum_error_mm)
        if not math.isfinite(rms) or not math.isfinite(maximum) or rms < 0.0 or maximum < 0.0:
            raise CalibrationValidationError("Rigid transform residuals must be finite and non-negative.")
        object.__setattr__(self, "rms_error_mm", rms)
        object.__setattr__(self, "maximum_error_mm", maximum)

    def to_dict(self) -> Mapping[str, Any]:
        """Return transform and residual summary for a local calibration record."""

        return {
            "transform": self.transform.to_dict(),
            "point_count": self.point_count,
            "rms_error_mm": self.rms_error_mm,
            "maximum_error_mm": self.maximum_error_mm,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RigidTransformFitResult":
        """Load residual evidence together with the fitted transform from local JSON."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("Rigid transform fit result must be a JSON object.")
        return cls(
            transform=RigidTransform.from_dict(value.get("transform")),
            point_count=value.get("point_count"),
            rms_error_mm=value.get("rms_error_mm"),
            maximum_error_mm=value.get("maximum_error_mm"),
        )


def fit_board_to_jaka_base(
    board_points_mm: Sequence[Point3D],
    jaka_base_points_mm: Sequence[Point3D],
    board_frame: str = "board",
    jaka_base_frame: str = "jaka_base",
) -> RigidTransformFitResult:
    """Fit the proper rigid transform from taught board points to JAKA-base points.

    Four or more non-collinear correspondences are deliberately required.  The board
    is planar, so rank two is sufficient; rank one is rejected because it leaves its
    in-plane orientation underconstrained.
    """

    board_points = _validated_points(board_points_mm, "board_points_mm")
    base_points = _validated_points(jaka_base_points_mm, "jaka_base_points_mm")
    if len(board_points) != len(base_points):
        raise CalibrationValidationError("Board and JAKA-base point counts must match.")
    if len(board_points) < 4:
        raise CalibrationValidationError("At least four board-to-JAKA-base reference points are required.")
    if not isinstance(board_frame, str) or not board_frame.strip():
        raise CalibrationValidationError("board_frame must be a non-empty string.")
    if not isinstance(jaka_base_frame, str) or not jaka_base_frame.strip():
        raise CalibrationValidationError("jaka_base_frame must be a non-empty string.")

    source = numpy.asarray([point.as_tuple() for point in board_points], dtype=numpy.float64)
    target = numpy.asarray([point.as_tuple() for point in base_points], dtype=numpy.float64)
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    source_centered = source - source_center
    target_centered = target - target_center
    if numpy.linalg.matrix_rank(source_centered) < 2:
        raise CalibrationValidationError("Board reference points must not be collinear.")
    if numpy.linalg.matrix_rank(target_centered) < 2:
        raise CalibrationValidationError("JAKA-base reference points must not be collinear.")

    try:
        left, _, right_transpose = numpy.linalg.svd(source_centered.T.dot(target_centered))
    except numpy.linalg.LinAlgError as error:
        raise CalibrationComputationError("Rigid transform fit did not converge.") from error
    rotation = right_transpose.T.dot(left.T)
    if numpy.linalg.det(rotation) < 0.0:
        right_transpose[-1, :] *= -1.0
        rotation = right_transpose.T.dot(left.T)
    translation = target_center - rotation.dot(source_center)
    transform = RigidTransform(
        board_frame,
        jaka_base_frame,
        tuple(tuple(float(value) for value in row) for row in rotation.tolist()),
        Point3D(float(translation[0]), float(translation[1]), float(translation[2])),
    )
    predicted = source.dot(rotation.T) + translation
    residuals = numpy.linalg.norm(predicted - target, axis=1)
    return RigidTransformFitResult(
        transform=transform,
        point_count=len(board_points),
        rms_error_mm=float(numpy.sqrt(numpy.mean(numpy.square(residuals)))),
        maximum_error_mm=float(numpy.max(residuals)),
    )


def _validated_points(points: Sequence[Point3D], field_name: str) -> Tuple[Point3D, ...]:
    """Normalize point sequences while keeping their source order intact."""

    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        raise CalibrationValidationError("{0} must be a sequence of Point3D values.".format(field_name))
    normalized = tuple(points)
    if not all(isinstance(point, Point3D) for point in normalized):
        raise CalibrationValidationError("{0} must contain only Point3D values.".format(field_name))
    return normalized
