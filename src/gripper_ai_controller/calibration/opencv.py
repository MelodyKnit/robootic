"""Late-bound OpenCV access so importing calibration contracts remains lightweight."""

import importlib
from typing import Any

from gripper_ai_controller.calibration.errors import OpenCvUnavailableError


def require_opencv() -> Any:
    """Return OpenCV only for an operation that actually needs it.

    The controller can still serve preview and 2D analysis when the optional local
    calibration dependency is absent.  Callers receive an actionable error instead
    of an import-time process failure.
    """

    try:
        return importlib.import_module("cv2")
    except ImportError as error:
        raise OpenCvUnavailableError(
            "OpenCV is required for this calibration operation. Install the project "
            "OpenCV contribution package through Poetry before retrying."
        ) from error


def require_opencv_aruco() -> Any:
    """Return OpenCV after verifying that the contrib ArUco module is present."""

    cv2 = require_opencv()
    if getattr(cv2, "aruco", None) is None:
        raise OpenCvUnavailableError(
            "OpenCV ArUco support is unavailable. Install opencv-contrib-python-headless "
            "through Poetry before using ChArUco calibration."
        )
    return cv2
