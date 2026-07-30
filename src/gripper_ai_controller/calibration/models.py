"""Hardware-neutral, JSON-safe contracts for offline camera calibration."""

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping, Sequence, Tuple

from gripper_ai_controller.calibration.errors import CalibrationValidationError


def _finite_float(value: Any, field_name: str) -> float:
    """Normalize one finite scalar while rejecting booleans and text."""

    if type(value) not in (int, float):
        raise CalibrationValidationError("{0} must be a finite number.".format(field_name))
    normalized = float(value)
    if not math.isfinite(normalized):
        raise CalibrationValidationError("{0} must be a finite number.".format(field_name))
    return normalized


def _positive_int(value: Any, field_name: str) -> int:
    """Normalize an integer dimension without allowing booleans or zero."""

    if type(value) is not int or value <= 0:
        raise CalibrationValidationError("{0} must be a positive integer.".format(field_name))
    return value


def _non_empty_identifier(value: Any, field_name: str) -> str:
    """Validate identifiers that become local calibration record keys."""

    if not isinstance(value, str) or not value.strip():
        raise CalibrationValidationError("{0} must be a non-empty string.".format(field_name))
    return value


@dataclass(frozen=True)
class Point3D:
    """A finite 3D point expressed in millimetres in a named caller context."""

    x_mm: float
    y_mm: float
    z_mm: float

    def __post_init__(self) -> None:
        """Reject non-finite coordinates before geometry code receives them."""

        object.__setattr__(self, "x_mm", _finite_float(self.x_mm, "x_mm"))
        object.__setattr__(self, "y_mm", _finite_float(self.y_mm, "y_mm"))
        object.__setattr__(self, "z_mm", _finite_float(self.z_mm, "z_mm"))

    @classmethod
    def from_sequence(cls, values: Sequence[Any]) -> "Point3D":
        """Build a point from exactly three JSON-compatible coordinate values."""

        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != 3:
            raise CalibrationValidationError("A 3D point must contain exactly three coordinates.")
        return cls(values[0], values[1], values[2])

    def as_tuple(self) -> Tuple[float, float, float]:
        """Return the point in NumPy-friendly coordinate order."""

        return self.x_mm, self.y_mm, self.z_mm

    def to_dict(self) -> Mapping[str, float]:
        """Return a JSON-serializable point representation."""

        return {"x_mm": self.x_mm, "y_mm": self.y_mm, "z_mm": self.z_mm}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Point3D":
        """Load a point from its stable JSON representation."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("A 3D point must be a JSON object.")
        return cls(value.get("x_mm"), value.get("y_mm"), value.get("z_mm"))


@dataclass(frozen=True)
class PixelPoint:
    """One finite pixel coordinate in the undistorted source image convention."""

    x_px: float
    y_px: float

    def __post_init__(self) -> None:
        """Reject invalid pixel coordinates before ray construction."""

        object.__setattr__(self, "x_px", _finite_float(self.x_px, "x_px"))
        object.__setattr__(self, "y_px", _finite_float(self.y_px, "y_px"))

    def to_dict(self) -> Mapping[str, float]:
        """Return a JSON-serializable pixel representation."""

        return {"x_px": self.x_px, "y_px": self.y_px}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PixelPoint":
        """Load a pixel coordinate from its stable JSON representation."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("A pixel coordinate must be a JSON object.")
        return cls(value.get("x_px"), value.get("y_px"))


@dataclass(frozen=True)
class CharucoBoardSpec:
    """Physical ChArUco board dimensions used by the first fixed-camera workflow."""

    squares_x: int = 7
    squares_y: int = 5
    square_length_mm: float = 25.0
    marker_length_mm: float = 18.0
    dictionary_name: str = "DICT_5X5_100"

    def __post_init__(self) -> None:
        """Verify that the board can encode ChArUco corners with physical scale."""

        if type(self.squares_x) is not int or self.squares_x < 2:
            raise CalibrationValidationError("squares_x must be an integer of at least 2.")
        if type(self.squares_y) is not int or self.squares_y < 2:
            raise CalibrationValidationError("squares_y must be an integer of at least 2.")
        square_length = _finite_float(self.square_length_mm, "square_length_mm")
        marker_length = _finite_float(self.marker_length_mm, "marker_length_mm")
        if square_length <= 0.0:
            raise CalibrationValidationError("square_length_mm must be greater than zero.")
        if marker_length <= 0.0 or marker_length >= square_length:
            raise CalibrationValidationError(
                "marker_length_mm must be greater than zero and smaller than square_length_mm."
            )
        if not isinstance(self.dictionary_name, str) or not self.dictionary_name.strip():
            raise CalibrationValidationError("dictionary_name must be a non-empty string.")
        object.__setattr__(self, "square_length_mm", square_length)
        object.__setattr__(self, "marker_length_mm", marker_length)

    def to_dict(self) -> Mapping[str, Any]:
        """Return the board as a stable JSON-compatible object."""

        return {
            "dictionary_name": self.dictionary_name,
            "squares_x": self.squares_x,
            "squares_y": self.squares_y,
            "square_length_mm": self.square_length_mm,
            "marker_length_mm": self.marker_length_mm,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CharucoBoardSpec":
        """Load a board specification from a local JSON calibration record."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("ChArUco board settings must be a JSON object.")
        return cls(
            squares_x=value.get("squares_x"),
            squares_y=value.get("squares_y"),
            square_length_mm=value.get("square_length_mm"),
            marker_length_mm=value.get("marker_length_mm"),
            dictionary_name=value.get("dictionary_name"),
        )


DEFAULT_CHARUCO_BOARD = CharucoBoardSpec()
"""The specified DICT_5X5_100, 7 x 5, 25 mm / 18 mm first-site board."""


@dataclass(frozen=True)
class CharucoObservation:
    """Detected ChArUco corners for one already captured image, without image bytes."""

    corners: Tuple[PixelPoint, ...]
    ids: Tuple[int, ...]

    def __post_init__(self) -> None:
        """Reject malformed detector output before passing it to native OpenCV code."""

        corners = tuple(self.corners)
        ids = tuple(self.ids)
        if len(corners) < 4:
            raise CalibrationValidationError("Each ChArUco observation requires at least four corners.")
        if len(corners) != len(ids):
            raise CalibrationValidationError("ChArUco corner and ID counts must match.")
        if not all(isinstance(corner, PixelPoint) for corner in corners):
            raise CalibrationValidationError("ChArUco corners must be PixelPoint values.")
        if any(type(marker_id) is not int or marker_id < 0 for marker_id in ids):
            raise CalibrationValidationError("ChArUco IDs must be non-negative integers.")
        if len(set(ids)) != len(ids):
            raise CalibrationValidationError("ChArUco IDs must not repeat within one image.")
        object.__setattr__(self, "corners", corners)
        object.__setattr__(self, "ids", ids)

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-safe observation for an explicit offline calibration run."""

        return {"corners": [corner.to_dict() for corner in self.corners], "ids": list(self.ids)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CharucoObservation":
        """Load a detector observation from a JSON-compatible object."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("A ChArUco observation must be a JSON object.")
        corners = value.get("corners")
        ids = value.get("ids")
        if not isinstance(corners, Sequence) or isinstance(corners, (str, bytes)):
            raise CalibrationValidationError("ChArUco corners must be a JSON array.")
        if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
            raise CalibrationValidationError("ChArUco IDs must be a JSON array.")
        return cls(tuple(PixelPoint.from_dict(corner) for corner in corners), tuple(ids))


@dataclass(frozen=True)
class CameraIntrinsics:
    """Pinhole intrinsics and distortion coefficients for one image size."""

    image_width_px: int
    image_height_px: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float
    distortion_coefficients: Tuple[float, ...] = ()

    def __post_init__(self) -> None:
        """Validate a finite, non-singular pinhole model."""

        object.__setattr__(self, "image_width_px", _positive_int(self.image_width_px, "image_width_px"))
        object.__setattr__(self, "image_height_px", _positive_int(self.image_height_px, "image_height_px"))
        fx = _finite_float(self.fx_px, "fx_px")
        fy = _finite_float(self.fy_px, "fy_px")
        if fx <= 0.0 or fy <= 0.0:
            raise CalibrationValidationError("fx_px and fy_px must be greater than zero.")
        object.__setattr__(self, "fx_px", fx)
        object.__setattr__(self, "fy_px", fy)
        object.__setattr__(self, "cx_px", _finite_float(self.cx_px, "cx_px"))
        object.__setattr__(self, "cy_px", _finite_float(self.cy_px, "cy_px"))
        coefficients = tuple(
            _finite_float(value, "distortion_coefficients") for value in self.distortion_coefficients
        )
        object.__setattr__(self, "distortion_coefficients", coefficients)

    @property
    def camera_matrix(self) -> Tuple[Tuple[float, float, float], ...]:
        """Return the matrix in OpenCV's pixel-coordinate convention."""

        return (
            (self.fx_px, 0.0, self.cx_px),
            (0.0, self.fy_px, self.cy_px),
            (0.0, 0.0, 1.0),
        )

    @property
    def has_distortion(self) -> bool:
        """Return whether projection must use OpenCV's undistortion implementation."""

        return any(abs(value) > 1e-12 for value in self.distortion_coefficients)

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-compatible intrinsic record without NumPy scalar values."""

        return {
            "image_size_px": {"width": self.image_width_px, "height": self.image_height_px},
            "camera_matrix": [list(row) for row in self.camera_matrix],
            "distortion_coefficients": list(self.distortion_coefficients),
        }

    @classmethod
    def from_camera_matrix(
        cls,
        image_width_px: int,
        image_height_px: int,
        camera_matrix: Sequence[Sequence[Any]],
        distortion_coefficients: Sequence[Any],
    ) -> "CameraIntrinsics":
        """Build intrinsics from an OpenCV matrix while retaining plain Python values."""

        if not isinstance(camera_matrix, Sequence) or len(camera_matrix) != 3:
            raise CalibrationValidationError("camera_matrix must contain three rows.")
        rows = []
        for row in camera_matrix:
            if not isinstance(row, Sequence) or len(row) != 3:
                raise CalibrationValidationError("camera_matrix rows must contain three values.")
            rows.append(tuple(row))
        if not isinstance(distortion_coefficients, Sequence) or isinstance(
            distortion_coefficients, (str, bytes)
        ):
            raise CalibrationValidationError("distortion_coefficients must be an array.")
        if abs(_finite_float(rows[2][0], "camera_matrix[2][0]")) > 1e-9 or abs(
            _finite_float(rows[2][1], "camera_matrix[2][1]")
        ) > 1e-9 or abs(_finite_float(rows[2][2], "camera_matrix[2][2]") - 1.0) > 1e-9:
            raise CalibrationValidationError("camera_matrix last row must be [0, 0, 1].")
        return cls(
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            fx_px=rows[0][0],
            fy_px=rows[1][1],
            cx_px=rows[0][2],
            cy_px=rows[1][2],
            distortion_coefficients=tuple(distortion_coefficients),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CameraIntrinsics":
        """Load an intrinsic record written by :meth:`to_dict`."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("Camera intrinsics must be a JSON object.")
        image_size = value.get("image_size_px")
        if not isinstance(image_size, Mapping):
            raise CalibrationValidationError("image_size_px must be a JSON object.")
        return cls.from_camera_matrix(
            image_width_px=image_size.get("width"),
            image_height_px=image_size.get("height"),
            camera_matrix=value.get("camera_matrix"),
            distortion_coefficients=value.get("distortion_coefficients", ()),
        )


@dataclass(frozen=True)
class CameraCalibrationResult:
    """One JSON-safe intrinsic calibration result, intended for local storage only."""

    calibration_id: str
    camera_id: str
    board_spec: CharucoBoardSpec
    intrinsics: CameraIntrinsics
    reprojection_error_px: float
    views_used: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Keep result files self-describing and safe to reload before use."""

        object.__setattr__(self, "calibration_id", _non_empty_identifier(self.calibration_id, "calibration_id"))
        object.__setattr__(self, "camera_id", _non_empty_identifier(self.camera_id, "camera_id"))
        if not isinstance(self.board_spec, CharucoBoardSpec):
            raise CalibrationValidationError("board_spec must be a CharucoBoardSpec.")
        if not isinstance(self.intrinsics, CameraIntrinsics):
            raise CalibrationValidationError("intrinsics must be a CameraIntrinsics value.")
        error = _finite_float(self.reprojection_error_px, "reprojection_error_px")
        if error < 0.0:
            raise CalibrationValidationError("reprojection_error_px must be non-negative.")
        if type(self.views_used) is not int or self.views_used <= 0:
            raise CalibrationValidationError("views_used must be a positive integer.")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CalibrationValidationError("schema_version must be 1.")
        object.__setattr__(self, "reprojection_error_px", error)

    def to_dict(self) -> Mapping[str, Any]:
        """Return only built-in JSON values; no NumPy scalars leak into persistence."""

        return {
            "schema_version": self.schema_version,
            "calibration_id": self.calibration_id,
            "camera_id": self.camera_id,
            "charuco_board": self.board_spec.to_dict(),
            "intrinsics": self.intrinsics.to_dict(),
            "reprojection_error_px": self.reprojection_error_px,
            "views_used": self.views_used,
        }

    def to_json(self) -> str:
        """Serialize a deterministic local calibration document."""

        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CameraCalibrationResult":
        """Reload a local result only after validating all geometric fields."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("Camera calibration result must be a JSON object.")
        return cls(
            calibration_id=value.get("calibration_id"),
            camera_id=value.get("camera_id"),
            board_spec=CharucoBoardSpec.from_dict(value.get("charuco_board")),
            intrinsics=CameraIntrinsics.from_dict(value.get("intrinsics")),
            reprojection_error_px=value.get("reprojection_error_px"),
            views_used=value.get("views_used"),
            schema_version=value.get("schema_version"),
        )
