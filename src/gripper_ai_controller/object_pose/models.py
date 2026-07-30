"""Stable contracts for known-workpiece two-dimensional foreground analysis."""

from dataclasses import dataclass
import math
from typing import Optional, Tuple

try:
    from typing import Protocol
except ImportError:  # Python 3.7 keeps Protocol in typing_extensions.
    from typing_extensions import Protocol

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame


OBJECT_POSE_REASON_CODES = (
    "object_pose_available",
    "object_pose_disabled",
    "object_pose_waiting_for_frame",
    "object_pose_unstable",
    "object_pose_analysis_failed",
    "object_pose_projection_failed",
    "frame_unavailable",
    "frame_dimensions_invalid",
    "mono_payload_size_invalid",
    "rgb_payload_size_invalid",
    "pixel_format_unsupported",
    "background_unavailable",
    "background_reference_unavailable",
    "background_camera_mismatch",
    "background_frame_dimensions_invalid",
    "background_mono_payload_size_invalid",
    "background_rgb_payload_size_invalid",
    "background_pixel_format_unsupported",
    "background_dimensions_mismatch",
    "no_foreground",
    "foreground_area_excessive",
    "candidate_shape_mismatch",
    "multiple_candidates",
    "orientation_undefined",
    "directional_yaw_ambiguous",
    "planarity_suspected",
    "calibration_unavailable",
    "calibration_camera_mismatch",
    "calibration_id_mismatch",
    "calibration_reprojection_error_excessive",
    "calibration_invalid",
)
"""Stable analysis reason codes for safe status mapping by plugins and browsers."""


@dataclass(frozen=True)
class PixelPoint2D:
    """A two-dimensional point in source-image pixels."""

    x: float
    y: float


@dataclass(frozen=True)
class NormalizedPoint2D:
    """A two-dimensional point normalized by image width and height."""

    x: float
    y: float


@dataclass(frozen=True)
class NormalizedRect:
    """An ROI or static exclusion region strictly contained in the unit image plane."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Reject non-finite or out-of-bounds rectangles before config can alter the visible work area."""

        values = (self.x, self.y, self.width, self.height)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise ValueError("Normalized rectangle values must be numeric.")
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Normalized rectangle values must be finite.")
        if self.x < 0.0 or self.y < 0.0 or self.width <= 0.0 or self.height <= 0.0:
            raise ValueError("Normalized rectangles require a non-negative origin and positive size.")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("Normalized rectangles must stay inside the image bounds.")


def _require_finite_at_least(value: float, name: str, minimum: float) -> None:
    """Validate a finite numeric lower bound while rejecting implicit boolean conversion."""

    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("{0} must be a finite number.".format(name))
    if float(value) < minimum:
        raise ValueError("{0} must be at least {1}.".format(name, minimum))


def _require_unit_interval(value: float, name: str, allow_zero: bool = True) -> None:
    """Validate a normalized configuration threshold."""

    _require_finite_at_least(value, name, 0.0)
    if not allow_zero and float(value) <= 0.0:
        raise ValueError("{0} must be greater than 0.0.".format(name))
    if float(value) > 1.0:
        raise ValueError("{0} must be no greater than 1.0.".format(name))


@dataclass(frozen=True)
class KnownWorkpieceProfile:
    """Known-workpiece shape and site-measured geometry without live frames or calibration data.

    The grasp origin is measured from the detected contour's geometric center:
    ``x`` follows the directed end-to-end axis and ``y`` points left of that
    direction. A non-zero offset may be used only with a full ``2*pi`` yaw to
    avoid treating a ``pi``-periodic major axis as a directed grasp point.
    """

    profile_id: str = "known-workpiece-v1"
    minimum_area_px: int = 400
    maximum_area_px: Optional[int] = None
    minimum_aspect_ratio: float = 1.0
    maximum_aspect_ratio: Optional[float] = None
    minimum_fill_ratio: float = 0.0
    maximum_fill_ratio: float = 1.0
    minimum_solidity: float = 0.0
    require_directional_yaw: bool = False
    directional_feature: str = "none"
    minimum_directional_asymmetry: float = 0.15
    nominal_length_mm: Optional[float] = None
    nominal_width_mm: Optional[float] = None
    object_thickness_mm: Optional[float] = None
    grasp_origin_offset_x_mm: float = 0.0
    grasp_origin_offset_y_mm: float = 0.0
    maximum_planar_dimension_error_ratio: float = 0.15

    def __post_init__(self) -> None:
        """Validate image-space constraints before an incomplete profile can confirm a workpiece."""

        if not isinstance(self.profile_id, str) or not self.profile_id:
            raise ValueError("Workpiece profile_id must be a non-empty string.")
        if type(self.minimum_area_px) is not int or self.minimum_area_px < 1:
            raise ValueError("minimum_area_px must be a positive integer.")
        if self.maximum_area_px is not None and (
            type(self.maximum_area_px) is not int or self.maximum_area_px < self.minimum_area_px
        ):
            raise ValueError("maximum_area_px must be an integer no smaller than minimum_area_px.")
        _require_finite_at_least(self.minimum_aspect_ratio, "minimum_aspect_ratio", 1.0)
        if self.maximum_aspect_ratio is not None:
            _require_finite_at_least(self.maximum_aspect_ratio, "maximum_aspect_ratio", self.minimum_aspect_ratio)
        _require_unit_interval(self.minimum_fill_ratio, "minimum_fill_ratio")
        _require_unit_interval(self.maximum_fill_ratio, "maximum_fill_ratio")
        if self.maximum_fill_ratio < self.minimum_fill_ratio:
            raise ValueError("maximum_fill_ratio must be no smaller than minimum_fill_ratio.")
        _require_unit_interval(self.minimum_solidity, "minimum_solidity")
        if not isinstance(self.require_directional_yaw, bool):
            raise ValueError("require_directional_yaw must be a boolean.")
        if self.directional_feature not in ("none", "larger_end"):
            raise ValueError("directional_feature must be none or larger_end.")
        _require_unit_interval(
            self.minimum_directional_asymmetry,
            "minimum_directional_asymmetry",
        )
        if (self.nominal_length_mm is None) != (self.nominal_width_mm is None):
            raise ValueError(
                "nominal_length_mm and nominal_width_mm must be provided together."
            )
        if self.nominal_length_mm is not None:
            _require_finite_at_least(self.nominal_length_mm, "nominal_length_mm", 0.0)
            _require_finite_at_least(self.nominal_width_mm, "nominal_width_mm", 0.0)
            if self.nominal_length_mm <= 0.0 or self.nominal_width_mm <= 0.0:
                raise ValueError("Nominal workpiece dimensions must be greater than 0.0 mm.")
            if self.nominal_length_mm < self.nominal_width_mm:
                raise ValueError("nominal_length_mm must be no smaller than nominal_width_mm.")
        if self.object_thickness_mm is not None:
            _require_finite_at_least(self.object_thickness_mm, "object_thickness_mm", 0.0)
            if self.object_thickness_mm <= 0.0:
                raise ValueError("object_thickness_mm must be greater than 0.0 mm.")
        for value, name in (
            (self.grasp_origin_offset_x_mm, "grasp_origin_offset_x_mm"),
            (self.grasp_origin_offset_y_mm, "grasp_origin_offset_y_mm"),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError("{0} must be a finite number.".format(name))
            if abs(float(value)) > 1000.0:
                raise ValueError("{0} must be no greater than 1000 mm in magnitude.".format(name))
        _require_unit_interval(
            self.maximum_planar_dimension_error_ratio,
            "maximum_planar_dimension_error_ratio",
            allow_zero=False,
        )
        if (
            abs(float(self.grasp_origin_offset_x_mm)) > 1e-9
            or abs(float(self.grasp_origin_offset_y_mm)) > 1e-9
        ) and not self.require_directional_yaw:
            raise ValueError(
                "A non-zero grasp origin offset requires require_directional_yaw to be true."
            )

    @property
    def has_measured_operational_geometry(self) -> bool:
        """Return whether a profile has the required physical evidence for live use."""

        return (
            self.nominal_length_mm is not None
            and self.nominal_width_mm is not None
            and self.object_thickness_mm is not None
        )


@dataclass(frozen=True)
class ObjectPoseSettings:
    """In-memory settings for background subtraction and contour filtering."""

    profile: KnownWorkpieceProfile = KnownWorkpieceProfile()
    difference_threshold: int = 25
    roi: Optional[NormalizedRect] = None
    excluded_regions: Tuple[NormalizedRect, ...] = ()
    morphology_kernel_size: int = 3
    opening_iterations: int = 1
    closing_iterations: int = 1
    maximum_foreground_ratio: float = 0.45
    minimum_orientation_eccentricity: float = 0.15
    maximum_contour_points: int = 96

    def __post_init__(self) -> None:
        """Validate detection settings that the configuration layer can construct directly."""

        if not isinstance(self.profile, KnownWorkpieceProfile):
            raise ValueError("profile must be a KnownWorkpieceProfile.")
        if type(self.difference_threshold) is not int or not 1 <= self.difference_threshold <= 255:
            raise ValueError("difference_threshold must be an integer from 1 to 255.")
        if self.roi is not None and not isinstance(self.roi, NormalizedRect):
            raise ValueError("roi must be a NormalizedRect or None.")
        if not isinstance(self.excluded_regions, tuple) or not all(
            isinstance(region, NormalizedRect) for region in self.excluded_regions
        ):
            raise ValueError("excluded_regions must be a tuple of NormalizedRect values.")
        if (
            type(self.morphology_kernel_size) is not int
            or self.morphology_kernel_size < 1
            or self.morphology_kernel_size > 31
            or self.morphology_kernel_size % 2 == 0
        ):
            raise ValueError("morphology_kernel_size must be an odd integer from 1 to 31.")
        for value, name in (
            (self.opening_iterations, "opening_iterations"),
            (self.closing_iterations, "closing_iterations"),
        ):
            if type(value) is not int or not 0 <= value <= 8:
                raise ValueError("{0} must be an integer from 0 to 8.".format(name))
        _require_unit_interval(self.maximum_foreground_ratio, "maximum_foreground_ratio", allow_zero=False)
        _require_unit_interval(self.minimum_orientation_eccentricity, "minimum_orientation_eccentricity")
        if type(self.maximum_contour_points) is not int or not 3 <= self.maximum_contour_points <= 2048:
            raise ValueError("maximum_contour_points must be an integer from 3 to 2048.")


@dataclass(frozen=True)
class ObjectPoseCandidate:
    """One two-dimensional foreground candidate filtered by a known-workpiece shape."""

    profile_id: str
    contour: Tuple[NormalizedPoint2D, ...]
    pixel_center: PixelPoint2D
    normalized_center: NormalizedPoint2D
    bounding_box: BoundingBox2D
    confidence: float
    orientation_defined: bool
    yaw_rad: Optional[float]
    yaw_period_rad: Optional[float]
    warning: Optional[str]
    area_px: int
    fill_ratio: float
    solidity: float


@dataclass(frozen=True)
class ObjectPoseAnalysis:
    """Read-only two-dimensional workpiece analysis for one frame."""

    camera_id: str
    captured_at: float
    valid: bool
    reason: str
    candidates: Tuple[ObjectPoseCandidate, ...]
    foreground_ratio: Optional[float]
    frame_width: Optional[int]
    frame_height: Optional[int]


class ObjectPoseEstimator(Protocol):
    """Replaceable synchronous two-dimensional workpiece analysis boundary."""

    def analyze(self, frame: ImageFrame) -> ObjectPoseAnalysis:
        """Return an analysis snapshot with no hardware side effects."""
