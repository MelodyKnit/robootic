"""JSON-safe composition of one camera calibration and one fixed workcell mapping."""

from dataclasses import dataclass
import math
from typing import Any, Mapping

from gripper_ai_controller.calibration.errors import CalibrationValidationError
from gripper_ai_controller.calibration.models import CameraCalibrationResult
from gripper_ai_controller.calibration.projection import PixelPlaneProjector
from gripper_ai_controller.calibration.transform import RigidTransform, RigidTransformFitResult


@dataclass(frozen=True)
class WorkcellCalibration:
    """One complete fixed-camera calibration record loaded by a passive analysis plugin.

    The record binds camera intrinsics, the board pose in camera coordinates, and the
    board pose in JAKA-base coordinates under one calibration identifier. It is only
    a read-only geometric contract: it contains no device endpoint, SDK handle, or
    authority to move the robot.
    """

    calibration_id: str
    camera_id: str
    camera_calibration: CameraCalibrationResult
    board_to_camera: RigidTransform
    board_to_jaka_base_fit: RigidTransformFitResult
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Reject mixed camera records or transforms from different physical boards."""

        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise CalibrationValidationError("calibration_id must be a non-empty string.")
        if not isinstance(self.camera_id, str) or not self.camera_id.strip():
            raise CalibrationValidationError("camera_id must be a non-empty string.")
        if not isinstance(self.camera_calibration, CameraCalibrationResult):
            raise CalibrationValidationError("camera_calibration must be a CameraCalibrationResult.")
        if not isinstance(self.board_to_camera, RigidTransform):
            raise CalibrationValidationError("board_to_camera must be a RigidTransform.")
        if not isinstance(self.board_to_jaka_base_fit, RigidTransformFitResult):
            raise CalibrationValidationError(
                "board_to_jaka_base_fit must be a RigidTransformFitResult."
            )
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CalibrationValidationError("schema_version must be 1.")
        if self.camera_calibration.calibration_id != self.calibration_id:
            raise CalibrationValidationError(
                "camera_calibration.calibration_id must match workcell calibration_id."
            )
        if self.camera_calibration.camera_id != self.camera_id:
            raise CalibrationValidationError("camera_calibration.camera_id must match workcell camera_id.")
        if self.board_to_camera.source_frame != self.board_to_jaka_base.source_frame:
            raise CalibrationValidationError(
                "board_to_camera and board_to_jaka_base must share the same board source frame."
            )
        if self.board_to_camera.target_frame != "camera":
            raise CalibrationValidationError("board_to_camera.target_frame must be the standard camera frame.")
        if self.board_to_jaka_base.target_frame != "jaka_base":
            raise CalibrationValidationError(
                "board_to_jaka_base.target_frame must be the standard jaka_base frame."
            )

    @property
    def board_to_jaka_base(self) -> RigidTransform:
        """Expose the accepted board-to-base transform without discarding fit evidence."""

        return self.board_to_jaka_base_fit.transform

    def fit_is_acceptable(self, maximum_rms_error_mm: float = 1.0) -> bool:
        """Return whether the board-to-base fit meets the caller's RMS acceptance limit."""

        if type(maximum_rms_error_mm) not in (int, float):
            raise CalibrationValidationError("maximum_rms_error_mm must be a positive finite number.")
        limit = float(maximum_rms_error_mm)
        if not math.isfinite(limit) or limit <= 0.0:
            raise CalibrationValidationError("maximum_rms_error_mm must be a positive finite number.")
        return self.board_to_jaka_base_fit.rms_error_mm <= limit

    def validate_frame(
        self,
        camera_id: str,
        calibration_id: str,
        image_width_px: int,
        image_height_px: int,
    ) -> None:
        """Validate that a frame is exactly compatible before any pixel is projected.

        A changed resolution changes the intrinsic pixel model, and a camera or
        calibration switch invalidates the geometric relationship. Callers must turn
        this exception into a non-actionable object result rather than estimating a
        substitute pose.
        """

        if camera_id != self.camera_id:
            raise CalibrationValidationError(
                "Frame camera_id does not match the loaded workcell calibration."
            )
        if calibration_id != self.calibration_id:
            raise CalibrationValidationError(
                "Frame calibration_id does not match the loaded workcell calibration."
            )
        intrinsics = self.camera_calibration.intrinsics
        if image_width_px != intrinsics.image_width_px or image_height_px != intrinsics.image_height_px:
            raise CalibrationValidationError(
                "Frame image dimensions do not match the calibrated intrinsic image size."
            )

    def plane_projector_for_frame(
        self,
        camera_id: str,
        calibration_id: str,
        image_width_px: int,
        image_height_px: int,
        maximum_rms_error_mm: float = 1.0,
    ) -> PixelPlaneProjector:
        """Return a projector only after strict frame identity and size validation."""

        self.validate_frame(camera_id, calibration_id, image_width_px, image_height_px)
        if not self.fit_is_acceptable(maximum_rms_error_mm):
            raise CalibrationValidationError(
                "board_to_jaka_base fit RMS error exceeds the configured acceptance limit."
            )
        return PixelPlaneProjector(
            self.camera_calibration.intrinsics,
            self.board_to_camera,
            self.board_to_jaka_base,
        )

    def to_dict(self) -> Mapping[str, Any]:
        """Return a complete JSON-compatible local workcell calibration record."""

        return {
            "schema_version": self.schema_version,
            "calibration_id": self.calibration_id,
            "camera_id": self.camera_id,
            "camera_calibration": self.camera_calibration.to_dict(),
            "board_to_camera": self.board_to_camera.to_dict(),
            "board_to_jaka_base_fit": self.board_to_jaka_base_fit.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkcellCalibration":
        """Load and cross-validate a JSON record before making it available to a plugin."""

        if not isinstance(value, Mapping):
            raise CalibrationValidationError("Workcell calibration must be a JSON object.")
        return cls(
            calibration_id=value.get("calibration_id"),
            camera_id=value.get("camera_id"),
            camera_calibration=CameraCalibrationResult.from_dict(value.get("camera_calibration")),
            board_to_camera=RigidTransform.from_dict(value.get("board_to_camera")),
            board_to_jaka_base_fit=RigidTransformFitResult.from_dict(
                value.get("board_to_jaka_base_fit")
            ),
            schema_version=value.get("schema_version"),
        )
