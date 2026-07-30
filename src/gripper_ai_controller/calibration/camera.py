"""Offline ChArUco camera-intrinsic calibration from already captured observations."""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

import numpy

from gripper_ai_controller.calibration.charuco import _create_charuco_board
from gripper_ai_controller.calibration.errors import (
    CalibrationComputationError,
    CalibrationValidationError,
)
from gripper_ai_controller.calibration.models import (
    CameraCalibrationResult,
    CameraIntrinsics,
    CharucoBoardSpec,
    CharucoObservation,
    PixelPoint,
    Point3D,
)
from gripper_ai_controller.calibration.opencv import require_opencv_aruco
from gripper_ai_controller.calibration.transform import RigidTransform


MINIMUM_CHARUCO_VIEWS = 25
"""The first-site acceptance workflow requires 25 varied ChArUco views."""


@dataclass(frozen=True)
class CameraCalibrationInput:
    """Validated observations for one offline intrinsic-calibration calculation."""

    calibration_id: str
    camera_id: str
    board_spec: CharucoBoardSpec
    image_width_px: int
    image_height_px: int
    observations: Tuple[CharucoObservation, ...]
    minimum_views: int = MINIMUM_CHARUCO_VIEWS

    def __post_init__(self) -> None:
        """Enforce consistent dimensions and the documented varied-view minimum."""

        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise CalibrationValidationError("calibration_id must be a non-empty string.")
        if not isinstance(self.camera_id, str) or not self.camera_id.strip():
            raise CalibrationValidationError("camera_id must be a non-empty string.")
        if not isinstance(self.board_spec, CharucoBoardSpec):
            raise CalibrationValidationError("board_spec must be a CharucoBoardSpec.")
        if type(self.image_width_px) is not int or self.image_width_px <= 0:
            raise CalibrationValidationError("image_width_px must be a positive integer.")
        if type(self.image_height_px) is not int or self.image_height_px <= 0:
            raise CalibrationValidationError("image_height_px must be a positive integer.")
        if type(self.minimum_views) is not int or self.minimum_views < 4:
            raise CalibrationValidationError("minimum_views must be an integer of at least 4.")
        observations = tuple(self.observations)
        if len(observations) < self.minimum_views:
            raise CalibrationValidationError(
                "At least {0} ChArUco observations are required; received {1}.".format(
                    self.minimum_views, len(observations)
                )
            )
        if not all(isinstance(observation, CharucoObservation) for observation in observations):
            raise CalibrationValidationError("observations must contain only CharucoObservation values.")
        object.__setattr__(self, "observations", observations)


def detect_charuco_observation(
    image: Any,
    board_spec: CharucoBoardSpec,
) -> Optional[CharucoObservation]:
    """Extract one ChArUco observation from already captured in-memory pixels.

    ``None`` means the frame does not contain enough reliable board corners and may
    be skipped by a capture CLI. This function never opens a camera or writes a
    frame. The caller must keep the source image dimensions consistent with the
    eventual :class:`CameraCalibrationInput`.
    """

    if not isinstance(board_spec, CharucoBoardSpec):
        raise CalibrationValidationError("board_spec must be a CharucoBoardSpec.")
    if not isinstance(image, numpy.ndarray):
        raise CalibrationValidationError("image must be a NumPy pixel array.")
    if image.dtype != numpy.uint8:
        raise CalibrationValidationError("image must use uint8 pixels.")
    if image.ndim not in (2, 3) or image.size == 0:
        raise CalibrationValidationError("image must be a non-empty 2D or 3D pixel array.")
    if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
        raise CalibrationValidationError("image color arrays must have 1, 3, or 4 channels.")
    cv2 = require_opencv_aruco()
    board = _create_charuco_board(board_spec, cv2)
    dictionary_id = getattr(cv2.aruco, board_spec.dictionary_name, None)
    if dictionary_id is None:
        raise CalibrationValidationError(
            "OpenCV does not expose requested ChArUco dictionary {0}.".format(
                board_spec.dictionary_name
            )
        )
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    try:
        if hasattr(cv2.aruco, "ArucoDetector"):
            marker_corners, marker_ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
        else:
            marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(image, dictionary)
        if marker_ids is None or len(marker_ids) == 0:
            return None
        interpolate = getattr(cv2.aruco, "interpolateCornersCharuco", None)
        if not callable(interpolate):
            raise CalibrationComputationError(
                "Installed OpenCV ArUco module lacks interpolateCornersCharuco."
            )
        count, charuco_corners, charuco_ids = interpolate(
            marker_corners,
            marker_ids,
            image,
            board,
        )
    except CalibrationComputationError:
        raise
    except Exception as error:
        raise CalibrationComputationError("OpenCV ChArUco corner detection failed.") from error
    if count is None or int(count) < 4 or charuco_corners is None or charuco_ids is None:
        return None
    corner_values = numpy.asarray(charuco_corners, dtype=numpy.float64).reshape((-1, 2))
    identifier_values = numpy.asarray(charuco_ids, dtype=numpy.int64).reshape(-1)
    if len(corner_values) != len(identifier_values) or len(corner_values) < 4:
        return None
    return CharucoObservation(
        tuple(PixelPoint(float(value[0]), float(value[1])) for value in corner_values),
        tuple(int(value) for value in identifier_values),
    )


def calibrate_charuco_camera(request: CameraCalibrationInput) -> CameraCalibrationResult:
    """Calibrate camera intrinsics without opening a camera or writing local files."""

    if not isinstance(request, CameraCalibrationInput):
        raise CalibrationValidationError("request must be a CameraCalibrationInput.")
    cv2 = require_opencv_aruco()
    board = _create_charuco_board(request.board_spec, cv2)
    corners = []
    identifiers = []
    for observation in request.observations:
        corners.append(
            numpy.asarray(
                [[point.x_px, point.y_px] for point in observation.corners], dtype=numpy.float32
            ).reshape((-1, 1, 2))
        )
        identifiers.append(numpy.asarray(observation.ids, dtype=numpy.int32).reshape((-1, 1)))

    calibrate = getattr(cv2.aruco, "calibrateCameraCharuco", None)
    if not callable(calibrate):
        raise CalibrationComputationError("Installed OpenCV ArUco module lacks calibrateCameraCharuco.")
    try:
        output = calibrate(
            corners,
            identifiers,
            board,
            (request.image_width_px, request.image_height_px),
            None,
            None,
        )
        reprojection_error, matrix, distortion = output[:3]
    except Exception as error:
        raise CalibrationComputationError("OpenCV ChArUco camera calibration failed.") from error
    matrix_values = numpy.asarray(matrix, dtype=numpy.float64).tolist()
    distortion_values = numpy.asarray(distortion, dtype=numpy.float64).reshape(-1).tolist()
    intrinsics = CameraIntrinsics.from_camera_matrix(
        request.image_width_px,
        request.image_height_px,
        matrix_values,
        distortion_values,
    )
    return CameraCalibrationResult(
        calibration_id=request.calibration_id,
        camera_id=request.camera_id,
        board_spec=request.board_spec,
        intrinsics=intrinsics,
        reprojection_error_px=float(reprojection_error),
        views_used=len(request.observations),
    )


def estimate_board_to_camera(
    observation: CharucoObservation,
    board_spec: CharucoBoardSpec,
    intrinsics: CameraIntrinsics,
    board_frame: str = "board",
    camera_frame: str = "camera",
) -> RigidTransform:
    """Estimate the fixed board-to-camera pose from one validated ChArUco observation."""

    if not isinstance(observation, CharucoObservation):
        raise CalibrationValidationError("observation must be a CharucoObservation.")
    if not isinstance(board_spec, CharucoBoardSpec):
        raise CalibrationValidationError("board_spec must be a CharucoBoardSpec.")
    if not isinstance(intrinsics, CameraIntrinsics):
        raise CalibrationValidationError("intrinsics must be a CameraIntrinsics.")
    cv2 = require_opencv_aruco()
    board = _create_charuco_board(board_spec, cv2)
    corners = numpy.asarray(
        [[point.x_px, point.y_px] for point in observation.corners], dtype=numpy.float32
    ).reshape((-1, 1, 2))
    identifiers = numpy.asarray(observation.ids, dtype=numpy.int32).reshape((-1, 1))
    try:
        estimated = cv2.aruco.estimatePoseCharucoBoard(
            corners,
            identifiers,
            board,
            numpy.asarray(intrinsics.camera_matrix, dtype=numpy.float64),
            numpy.asarray(intrinsics.distortion_coefficients, dtype=numpy.float64),
            None,
            None,
        )
        valid, rotation_vector, translation_vector = estimated[:3]
        if not valid:
            raise CalibrationComputationError("ChArUco board pose could not be estimated.")
        rotation, _ = cv2.Rodrigues(rotation_vector)
    except CalibrationComputationError:
        raise
    except Exception as error:
        raise CalibrationComputationError("OpenCV ChArUco board pose estimation failed.") from error
    translation = numpy.asarray(translation_vector, dtype=numpy.float64).reshape(-1)
    if translation.shape[0] != 3:
        raise CalibrationComputationError("OpenCV returned an invalid board translation vector.")
    return RigidTransform(
        board_frame,
        camera_frame,
        tuple(tuple(float(value) for value in row) for row in numpy.asarray(rotation).tolist()),
        Point3D(float(translation[0]), float(translation[1]), float(translation[2])),
    )
