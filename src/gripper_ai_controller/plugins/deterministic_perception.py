"""A simulation-only perception plugin with explicit camera-to-robot transforms."""

from gripper_ai_controller.core.components import PerceptionPlugin
from gripper_ai_controller.domain.models import (
    BoundingBox2D,
    CameraCalibration,
    CameraMounting,
    ComponentManifest,
    DetectedObject,
    GraspCandidate,
    ImageFrame,
    PerceptionResult,
    Pose3D,
    RobotStatus,
)


class DeterministicPerceptionPlugin(PerceptionPlugin):
    """Produces a fixed workpiece observation while enforcing calibration semantics."""

    manifest = ComponentManifest(
        "deterministic-perception",
        "0.1.0",
        "perception",
        ("perception", "pose"),
        "DeterministicPerceptionPlugin",
    )

    async def perceive(
        self,
        frame: ImageFrame,
        calibration: CameraCalibration,
        robot_status: RobotStatus,
    ) -> PerceptionResult:
        """Convert one synthetic frame into a calibrated robot-base grasp candidate.

        The result is invalidated before detection when the frame, calibration, camera
        identity, or tool-mounted robot state cannot establish a trustworthy transform.
        """

        if not frame.healthy or not frame.frame_reference:
            return self._invalid(frame, "Camera frame is unavailable or unhealthy.")
        if not calibration.valid or frame.calibration_id != calibration.calibration_id:
            return self._invalid(frame, "Camera calibration is missing or invalid.")
        if calibration.camera_id != frame.camera_id:
            return self._invalid(frame, "Frame camera ID does not match calibration.")

        pose = self._resolve_workpiece_pose(calibration, robot_status)
        if pose is None:
            return self._invalid(frame, "Camera mounting cannot be resolved to robot_base.")
        candidate = GraspCandidate(pose=pose, score=0.95)
        detected = DetectedObject(
            label="workpiece",
            bounding_box=BoundingBox2D(0.4, 0.4, 0.2, 0.2),
            confidence=0.95,
            pose=pose,
            grasp_candidates=(candidate,),
        )
        return PerceptionResult(frame.camera_id, frame.captured_at, True, "Perception complete.", (detected,))

    def _resolve_workpiece_pose(
        self,
        calibration: CameraCalibration,
        robot_status: RobotStatus,
    ) -> Pose3D:
        """Compose simulated transforms into robot_base without pretending to do full kinematics."""

        camera_observation = Pose3D(150.0, 0.0, 50.0, 0.0, 0.0, 0.0, "camera")
        if calibration.mounting == CameraMounting.FIXED:
            if calibration.parent_frame != "robot_base":
                return None
            return self._compose(calibration.camera_to_parent, camera_observation, "robot_base")
        if calibration.mounting == CameraMounting.TOOL:
            if calibration.parent_frame != "tool0" or not robot_status.initialized:
                return None
            in_tool = self._compose(calibration.camera_to_parent, camera_observation, "tool0")
            return self._compose(robot_status.tcp_pose, in_tool, "robot_base")
        return None

    @staticmethod
    def _compose(parent: Pose3D, child: Pose3D, frame_id: str) -> Pose3D:
        """Use additive simulated transforms; real adapters must supply calibrated kinematics."""

        return Pose3D(
            parent.x_mm + child.x_mm,
            parent.y_mm + child.y_mm,
            parent.z_mm + child.z_mm,
            parent.rx_rad + child.rx_rad,
            parent.ry_rad + child.ry_rad,
            parent.rz_rad + child.rz_rad,
            frame_id,
        )

    @staticmethod
    def _invalid(frame: ImageFrame, reason: str) -> PerceptionResult:
        """Build a non-actionable result that preserves frame identity and failure context."""

        return PerceptionResult(frame.camera_id, frame.captured_at, False, reason, ())
