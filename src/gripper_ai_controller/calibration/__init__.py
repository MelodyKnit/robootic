"""Offline calibration contracts for the fixed single-camera workcell."""

from gripper_ai_controller.calibration.camera import (
    MINIMUM_CHARUCO_VIEWS,
    CameraCalibrationInput,
    calibrate_charuco_camera,
    detect_charuco_observation,
    estimate_board_to_camera,
)
from gripper_ai_controller.calibration.charuco import create_charuco_board, generate_charuco_board_image
from gripper_ai_controller.calibration.errors import (
    CalibrationComputationError,
    CalibrationError,
    CalibrationValidationError,
    OpenCvUnavailableError,
    ProjectionError,
)
from gripper_ai_controller.calibration.models import (
    DEFAULT_CHARUCO_BOARD,
    CameraCalibrationResult,
    CameraIntrinsics,
    CharucoBoardSpec,
    CharucoObservation,
    PixelPoint,
    Point3D,
)
from gripper_ai_controller.calibration.projection import (
    PixelPlaneProjector,
    ProjectedPlanePoint,
    project_pixel_to_jaka_base,
)
from gripper_ai_controller.calibration.transform import (
    RigidTransform,
    RigidTransformFitResult,
    fit_board_to_jaka_base,
)
from gripper_ai_controller.calibration.workcell import WorkcellCalibration

__all__ = [
    "MINIMUM_CHARUCO_VIEWS",
    "CameraCalibrationInput",
    "calibrate_charuco_camera",
    "detect_charuco_observation",
    "estimate_board_to_camera",
    "create_charuco_board",
    "generate_charuco_board_image",
    "CalibrationComputationError",
    "CalibrationError",
    "CalibrationValidationError",
    "OpenCvUnavailableError",
    "ProjectionError",
    "DEFAULT_CHARUCO_BOARD",
    "CameraCalibrationResult",
    "CameraIntrinsics",
    "CharucoBoardSpec",
    "CharucoObservation",
    "PixelPoint",
    "Point3D",
    "PixelPlaneProjector",
    "ProjectedPlanePoint",
    "project_pixel_to_jaka_base",
    "RigidTransform",
    "RigidTransformFitResult",
    "fit_board_to_jaka_base",
    "WorkcellCalibration",
]
