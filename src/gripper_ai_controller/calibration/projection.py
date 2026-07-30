"""Pixel-ray intersection with a fixed ChArUco board plane and JAKA-base mapping."""

from dataclasses import dataclass
import math
from typing import Tuple

import numpy

from gripper_ai_controller.calibration.errors import ProjectionError
from gripper_ai_controller.calibration.models import CameraIntrinsics, PixelPoint, Point3D
from gripper_ai_controller.calibration.opencv import require_opencv
from gripper_ai_controller.calibration.transform import RigidTransform


@dataclass(frozen=True)
class ProjectedPlanePoint:
    """A pixel's board-plane and JAKA-base position, with no claimed 6D orientation."""

    pixel: PixelPoint
    board_point_mm: Point3D
    jaka_base_point_mm: Point3D
    ray_distance_mm: float

    def __post_init__(self) -> None:
        """Reject a non-physical ray length from a caller-supplied projector."""

        if not isinstance(self.pixel, PixelPoint):
            raise ProjectionError("pixel must be a PixelPoint.")
        if not isinstance(self.board_point_mm, Point3D):
            raise ProjectionError("board_point_mm must be a Point3D.")
        if not isinstance(self.jaka_base_point_mm, Point3D):
            raise ProjectionError("jaka_base_point_mm must be a Point3D.")
        distance = float(self.ray_distance_mm)
        if not math.isfinite(distance) or distance <= 0.0:
            raise ProjectionError("ray_distance_mm must be finite and greater than zero.")
        object.__setattr__(self, "ray_distance_mm", distance)

    def to_dict(self):
        """Return a JSON-safe position-only result for a browser/API serializer."""

        return {
            "pixel": self.pixel.to_dict(),
            "board_point_mm": self.board_point_mm.to_dict(),
            "jaka_base_point_mm": self.jaka_base_point_mm.to_dict(),
            "ray_distance_mm": self.ray_distance_mm,
        }


class PixelPlaneProjector:
    """Project pixels through calibrated intrinsics onto a fixed board ``z=0`` plane.

    ``board_to_camera`` maps physical board coordinates to the OpenCV camera frame;
    ``board_to_jaka_base`` maps the same board coordinates to the robot base.  The
    constructor rejects frame-name mismatches so image data cannot be silently mixed
    with a calibration produced for another board.
    """

    def __init__(
        self,
        intrinsics: CameraIntrinsics,
        board_to_camera: RigidTransform,
        board_to_jaka_base: RigidTransform,
    ) -> None:
        """Bind immutable calibration records without opening hardware or files."""

        if not isinstance(intrinsics, CameraIntrinsics):
            raise ProjectionError("intrinsics must be a CameraIntrinsics value.")
        if not isinstance(board_to_camera, RigidTransform):
            raise ProjectionError("board_to_camera must be a RigidTransform.")
        if not isinstance(board_to_jaka_base, RigidTransform):
            raise ProjectionError("board_to_jaka_base must be a RigidTransform.")
        if board_to_camera.source_frame != board_to_jaka_base.source_frame:
            raise ProjectionError(
                "board_to_camera and board_to_jaka_base must share the same board source frame."
            )
        self.intrinsics = intrinsics
        self.board_to_camera = board_to_camera
        self.board_to_jaka_base = board_to_jaka_base
        self._camera_to_board = board_to_camera.inverse()

    def project(self, pixel: PixelPoint) -> ProjectedPlanePoint:
        """Intersect one in-bounds image ray with board ``z=0`` and map it to JAKA base."""

        if not isinstance(pixel, PixelPoint):
            raise ProjectionError("pixel must be a PixelPoint.")
        if (
            pixel.x_px < 0.0
            or pixel.y_px < 0.0
            or pixel.x_px >= self.intrinsics.image_width_px
            or pixel.y_px >= self.intrinsics.image_height_px
        ):
            raise ProjectionError("pixel lies outside the calibrated image dimensions.")
        normalized_x, normalized_y = self._normalized_camera_ray(pixel)
        direction_camera = numpy.asarray([normalized_x, normalized_y, 1.0], dtype=numpy.float64)
        inverse_rotation = numpy.asarray(self._camera_to_board.rotation_matrix, dtype=numpy.float64)
        ray_origin = numpy.asarray(self._camera_to_board.translation_mm.as_tuple(), dtype=numpy.float64)
        direction_board = inverse_rotation.dot(direction_camera)
        if abs(float(direction_board[2])) < 1e-9:
            raise ProjectionError("Pixel ray is parallel to the calibrated board plane.")
        travel_parameter = -float(ray_origin[2]) / float(direction_board[2])
        if travel_parameter <= 0.0:
            raise ProjectionError("Calibrated board plane lies behind the camera ray.")
        board_coordinate = ray_origin + travel_parameter * direction_board
        board_point = Point3D(
            float(board_coordinate[0]),
            float(board_coordinate[1]),
            float(board_coordinate[2]),
        )
        return ProjectedPlanePoint(
            pixel=pixel,
            board_point_mm=board_point,
            jaka_base_point_mm=self.board_to_jaka_base.apply(board_point),
            ray_distance_mm=float(travel_parameter * numpy.linalg.norm(direction_camera)),
        )

    def _normalized_camera_ray(self, pixel: PixelPoint) -> Tuple[float, float]:
        """Return an undistorted camera ray; zero-distortion math remains dependency-free."""

        if not self.intrinsics.has_distortion:
            return (
                (pixel.x_px - self.intrinsics.cx_px) / self.intrinsics.fx_px,
                (pixel.y_px - self.intrinsics.cy_px) / self.intrinsics.fy_px,
            )
        try:
            cv2 = require_opencv()
            undistorted = cv2.undistortPoints(
                numpy.asarray([[[pixel.x_px, pixel.y_px]]], dtype=numpy.float64),
                numpy.asarray(self.intrinsics.camera_matrix, dtype=numpy.float64),
                numpy.asarray(self.intrinsics.distortion_coefficients, dtype=numpy.float64),
            )
        except Exception as error:
            raise ProjectionError(
                "OpenCV is required to project pixels with non-zero distortion coefficients."
            ) from error
        values = numpy.asarray(undistorted, dtype=numpy.float64).reshape(-1)
        if values.shape[0] != 2 or not numpy.isfinite(values).all():
            raise ProjectionError("OpenCV returned an invalid undistorted pixel ray.")
        return float(values[0]), float(values[1])


def project_pixel_to_jaka_base(
    pixel: PixelPoint,
    intrinsics: CameraIntrinsics,
    board_to_camera: RigidTransform,
    board_to_jaka_base: RigidTransform,
) -> ProjectedPlanePoint:
    """Convenience facade for a one-off read-only pixel-to-base projection."""

    return PixelPlaneProjector(intrinsics, board_to_camera, board_to_jaka_base).project(pixel)
