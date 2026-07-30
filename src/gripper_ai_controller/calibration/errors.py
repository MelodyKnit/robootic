"""Stable errors raised by the offline calibration domain."""


class CalibrationError(ValueError):
    """Base class for calibration inputs and offline computation failures."""


class CalibrationValidationError(CalibrationError):
    """Raised when an operator-supplied calibration contract is incomplete or invalid."""


class CalibrationComputationError(CalibrationError):
    """Raised when a numerically valid request cannot produce a trustworthy result."""


class OpenCvUnavailableError(CalibrationError):
    """Raised only when an operation explicitly requiring OpenCV/ArUco is requested."""


class ProjectionError(CalibrationError):
    """Raised when a pixel ray cannot be safely intersected with the calibrated board plane."""
