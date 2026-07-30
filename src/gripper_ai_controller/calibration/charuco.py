"""ChArUco board construction and rendering without any camera connection."""

from typing import Any

from gripper_ai_controller.calibration.errors import CalibrationComputationError, CalibrationValidationError
from gripper_ai_controller.calibration.models import CharucoBoardSpec
from gripper_ai_controller.calibration.opencv import require_opencv_aruco


def create_charuco_board(spec: CharucoBoardSpec) -> Any:
    """Create the OpenCV board object for a validated physical board specification."""

    if not isinstance(spec, CharucoBoardSpec):
        raise CalibrationValidationError("spec must be a CharucoBoardSpec.")
    cv2 = require_opencv_aruco()
    return _create_charuco_board(spec, cv2)


def generate_charuco_board_image(
    spec: CharucoBoardSpec,
    image_width_px: int,
    image_height_px: int,
    margin_px: int = 0,
    border_bits: int = 1,
) -> Any:
    """Render a printable grayscale board image for an explicit CLI output path.

    This function intentionally returns pixels only.  The caller owns file output so
    versioned source directories never receive unplanned calibration artefacts.
    """

    if type(image_width_px) is not int or image_width_px <= 0:
        raise CalibrationValidationError("image_width_px must be a positive integer.")
    if type(image_height_px) is not int or image_height_px <= 0:
        raise CalibrationValidationError("image_height_px must be a positive integer.")
    if type(margin_px) is not int or margin_px < 0:
        raise CalibrationValidationError("margin_px must be a non-negative integer.")
    if type(border_bits) is not int or border_bits <= 0:
        raise CalibrationValidationError("border_bits must be a positive integer.")
    if margin_px * 2 >= min(image_width_px, image_height_px):
        raise CalibrationValidationError("margin_px leaves no drawable ChArUco board area.")

    cv2 = require_opencv_aruco()
    board = _create_charuco_board(spec, cv2)
    try:
        if hasattr(board, "generateImage"):
            return board.generateImage(
                (image_width_px, image_height_px),
                marginSize=margin_px,
                borderBits=border_bits,
            )
        return board.draw((image_width_px, image_height_px), margin_px, border_bits)
    except Exception as error:
        raise CalibrationComputationError("OpenCV could not render the ChArUco board image.") from error


def _create_charuco_board(spec: CharucoBoardSpec, cv2: Any) -> Any:
    """Bridge the supported OpenCV 4.x ChArUco board constructor variants."""

    aruco = cv2.aruco
    dictionary_id = getattr(aruco, spec.dictionary_name, None)
    if dictionary_id is None:
        raise CalibrationValidationError(
            "OpenCV does not expose requested ChArUco dictionary {0}.".format(spec.dictionary_name)
        )
    dictionary = aruco.getPredefinedDictionary(dictionary_id)
    try:
        if hasattr(aruco, "CharucoBoard"):
            return aruco.CharucoBoard(
                (spec.squares_x, spec.squares_y),
                spec.square_length_mm,
                spec.marker_length_mm,
                dictionary,
            )
        return aruco.CharucoBoard_create(
            spec.squares_x,
            spec.squares_y,
            spec.square_length_mm,
            spec.marker_length_mm,
            dictionary,
        )
    except Exception as error:
        raise CalibrationComputationError("OpenCV could not construct the ChArUco board.") from error
