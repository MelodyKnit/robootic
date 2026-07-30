"""Passive known-workpiece planar pose plugin for the camera preview."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Mapping, Optional

from PIL import Image

from gripper_ai_controller.calibration.workcell import WorkcellCalibration
from gripper_ai_controller.configuration import WorkpiecePoseSettings
from gripper_ai_controller.core.components import CameraBindingRequirement, Plugin
from gripper_ai_controller.core.events import FrameCaptured
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame
from gripper_ai_controller.object_pose.estimator import ForegroundObjectPoseEstimator
from gripper_ai_controller.object_pose.tracker import (
    ObjectPoseTrackingService,
    ObjectPoseTrackingSnapshot,
)


@dataclass(frozen=True)
class ObjectPoseAnalysisPluginSnapshot:
    """Object-pose cache plus the plugin lifecycle bit used by browser diagnostics."""

    camera_id: str
    started: bool
    object_pose: ObjectPoseTrackingSnapshot


class ObjectPoseAnalysisPlugin(Plugin):
    """Consume ``FrameCaptured`` only and maintain one passive object-pose cache.

    This plugin intentionally does not import or receive a vision adapter, JAKA
    adapter, gripper adapter, safety policy, or command facade. It can therefore
    neither acquire a camera frame nor cause a physical device operation.
    """

    manifest = ComponentManifest(
        "object-pose-analysis",
        "0.1.0",
        "plugin",
        ("frame-observer", "object-pose", "plane-calibration"),
        "build_object_pose_analysis_plugin",
    )
    ui_kind = "object-pose-analysis"
    camera_binding_requirement = CameraBindingRequirement.shared_single_source()

    def __init__(self, camera_id: str, tracking_service: ObjectPoseTrackingService) -> None:
        """Bind one preconstructed passive worker without starting it."""

        self.camera_id = camera_id
        self.tracking_service = tracking_service
        self._started = False
        self._shutdown = False

    async def startup(self) -> None:
        """Mark this observer ready; no camera or robot lifecycle is started here."""

        if self._shutdown:
            raise RuntimeError("A stopped object pose analysis plugin cannot be restarted.")
        self._started = True

    async def shutdown(self) -> None:
        """Drain the one passive analysis worker and release its CPU executor."""

        if self._shutdown:
            return
        self._started = False
        self._shutdown = True
        await self.tracking_service.shutdown()

    async def handle_event(self, event: object) -> None:
        """Submit an already acquired matching frame with no direct hardware access."""

        if not self._started or not isinstance(event, FrameCaptured):
            return
        if event.frame.camera_id != self.camera_id:
            return
        await self.tracking_service.submit_frame(event.frame)

    async def reset_frame_state(self) -> None:
        """Discard source-dependent state after a camera selection/restart transition."""

        if not self._started or self._shutdown:
            return
        await self.tracking_service.reset_frame_state()

    async def object_snapshot(self) -> ObjectPoseTrackingSnapshot:
        """Return cached analysis only; no inference, capture, or file read is triggered."""

        return await self.tracking_service.snapshot()

    async def snapshot(self) -> ObjectPoseAnalysisPluginSnapshot:
        """Expose compact lifecycle diagnostics at the trusted plugin boundary."""

        return ObjectPoseAnalysisPluginSnapshot(
            self.camera_id,
            self._started,
            await self.object_snapshot(),
        )

    async def reload_factory_kwargs(self) -> Mapping[str, object]:
        """Keep a hot replacement aligned with its current immutable local settings."""

        return {"workpiece_pose_settings": self.tracking_service.settings}


def build_object_pose_analysis_plugin(
    camera_id: str,
    workpiece_pose_settings: WorkpiecePoseSettings,
    background_loader: Optional[Callable[[str, str, str], ImageFrame]] = None,
    calibration_loader: Optional[Callable[[str], WorkcellCalibration]] = None,
) -> ObjectPoseAnalysisPlugin:
    """Build a passive plugin from trusted local config without connecting hardware.

    Missing/corrupt site assets do not abort camera preview. The plugin stays enabled
    but returns one explicit invalid reason and no object result until an operator
    supplies a correct background and calibration file under ``localstore/``.
    """

    settings = workpiece_pose_settings
    if not settings.enabled:
        return ObjectPoseAnalysisPlugin(
            camera_id, ObjectPoseTrackingService(camera_id, settings, None, None)
        )

    background_loader = background_loader or load_background_reference
    calibration_loader = calibration_loader or load_workcell_calibration
    background = None
    calibration = None
    setup_error = None
    try:
        background = background_loader(
            settings.background_reference_path,
            camera_id,
            settings.expected_calibration_id,
        )
    except (OSError, ValueError, TypeError):
        setup_error = "background_reference_unavailable"
    try:
        calibration = calibration_loader(settings.workcell_calibration_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if setup_error is None:
            setup_error = "calibration_unavailable"
    if calibration is not None:
        if calibration.camera_id != camera_id:
            setup_error = "calibration_camera_mismatch"
        elif calibration.calibration_id != settings.expected_calibration_id:
            setup_error = "calibration_id_mismatch"
    estimator = None
    if background is not None:
        estimator = ForegroundObjectPoseEstimator(settings.detector_settings, background)
    service = ObjectPoseTrackingService(
        camera_id,
        settings,
        estimator,
        calibration,
        setup_error=setup_error,
    )
    return ObjectPoseAnalysisPlugin(camera_id, service)


def load_background_reference(
    path_value: str, camera_id: str, calibration_id: str
) -> ImageFrame:
    """Load one explicit local background image into a transient normalized frame."""

    path = _localstore_input_path(path_value, "background_reference_path")
    with Image.open(str(path)) as source:
        if source.mode == "L":
            image = source.copy()
            pixel_format = "mono8"
        else:
            image = source.convert("RGB")
            pixel_format = "rgb8"
        width, height = image.size
        payload = image.tobytes()
    return ImageFrame(
        camera_id,
        0.0,
        "localstore-background-reference",
        calibration_id,
        True,
        payload,
        width,
        height,
        pixel_format,
    )


def load_workcell_calibration(path_value: str) -> WorkcellCalibration:
    """Load and validate a JSON-only workcell record from an explicit localstore path."""

    path = _localstore_input_path(path_value, "workcell_calibration_path")
    with path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    return WorkcellCalibration.from_dict(payload)


def _localstore_input_path(value: str, field_name: str) -> Path:
    """Reject traversal, absolute paths, and symlink escapes before reading local assets."""

    if not isinstance(value, str) or not value:
        raise ValueError("object_pose.{0} must name a localstore file.".format(field_name))
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
        raise ValueError(
            "object_pose.{0} must be a localstore-relative path without parent traversal.".format(
                field_name
            )
        )
    resolved_root = Path("localstore").resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError("object_pose.{0} resolves outside localstore.".format(field_name))
    if not resolved_path.is_file():
        raise ValueError("object_pose.{0} does not name an existing file.".format(field_name))
    return resolved_path
