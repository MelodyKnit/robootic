"""Passive visual-pose analysis plugin for browser preview integration."""

from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from gripper_ai_controller.configuration import PoseTrackingSettings, VisionAnalysisSettings
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.events import FrameCaptured
from gripper_ai_controller.domain.models import ComponentManifest
from gripper_ai_controller.plugins.errors import VisualPoseAnalysisCapabilityError
from gripper_ai_controller.pose.estimator import PoseEstimator, TorchvisionKeypointRcnnEstimator
from gripper_ai_controller.pose.models import PoseTrackingSnapshot
from gripper_ai_controller.pose.tracker import PoseTargetError, PoseTrackingService
from gripper_ai_controller.vision.analysis import VisionAnalysisService
from gripper_ai_controller.vision.models import VisionAnalysisSnapshot


@dataclass(frozen=True)
class VisualPoseAnalysisSnapshot:
    """Pair passive pose and image-quality snapshots from one plugin boundary."""

    camera_id: str
    started: bool
    pose: PoseTrackingSnapshot
    analysis: VisionAnalysisSnapshot


class VisualPoseAnalysisPlugin(Plugin):
    """Consume camera frame events and maintain browser-safe pose and diagnostic state.

    The plugin receives only :class:`FrameCaptured` events. It does not import a
    vision adapter, robot adapter, gripper adapter, control facade, or safety policy,
    so no plugin method can initiate a hardware connection or command.
    """

    manifest = ComponentManifest(
        "visual-pose-analysis",
        "0.1.0",
        "plugin",
        ("frame-observer", "pose-tracking", "vision-analysis"),
        "build_visual_pose_analysis_plugin",
    )
    ui_kind = "visual-pose-analysis"

    def __init__(
        self,
        camera_id: str,
        pose_tracking_service: Optional[PoseTrackingService],
        vision_analysis_service: VisionAnalysisService,
    ) -> None:
        """Bind passive services created from trusted application configuration."""

        self.camera_id = camera_id
        self.pose_tracking_service = pose_tracking_service
        self.vision_analysis_service = vision_analysis_service
        self._started = False
        self._shutdown = False

    async def startup(self) -> None:
        """Mark the event observer ready without starting a camera or physical device."""

        if self._shutdown:
            raise RuntimeError("A stopped visual pose analysis plugin cannot be restarted.")
        self._started = True

    async def shutdown(self) -> None:
        """Drain bounded analysis tasks and release only plugin-owned worker resources."""

        if self._shutdown:
            return
        self._started = False
        self._shutdown = True
        if self.pose_tracking_service is not None:
            await self.pose_tracking_service.shutdown()
        await self.vision_analysis_service.shutdown()

    async def handle_event(self, event: object) -> None:
        """Submit a matching captured frame to bounded passive analysis workers."""

        if not self._started or not isinstance(event, FrameCaptured):
            return
        frame = event.frame
        if frame.camera_id != self.camera_id:
            return
        await self.vision_analysis_service.record_frame(frame)
        if self.pose_tracking_service is not None:
            await self.pose_tracking_service.submit_frame(
                frame,
                event.on_pose_inference_started,
            )

    async def reset_frame_state(self) -> None:
        """Discard frame-derived plugin state without replacing lifecycle resources."""

        if not self._started or self._shutdown:
            return
        if self.pose_tracking_service is not None:
            await self.pose_tracking_service.reset_frame_state()
        await self.vision_analysis_service.reset_frame_state()

    async def pose_snapshot(self) -> PoseTrackingSnapshot:
        """Return cached pose metadata without starting inference or capturing a frame."""

        if self.pose_tracking_service is None:
            return PoseTrackingSnapshot(
                self.camera_id,
                None,
                False,
                "Pose tracking is disabled by the current preview configuration.",
                "right_wrist",
                None,
                None,
                None,
                0,
            )
        return await self.pose_tracking_service.snapshot()

    async def analysis_snapshot(self) -> VisionAnalysisSnapshot:
        """Return cached quality and detected-person metadata without new processing."""

        return await self.vision_analysis_service.snapshot()

    async def snapshot(self) -> VisualPoseAnalysisSnapshot:
        """Return both browser-facing visual snapshots from this plugin boundary."""

        pose = await self.pose_snapshot()
        analysis = await self.analysis_snapshot()
        return VisualPoseAnalysisSnapshot(self.camera_id, self._started, pose, analysis)

    async def set_target_joint(self, target_joint: str) -> PoseTrackingSnapshot:
        """Update the in-memory target joint without granting any control authority."""

        if self.pose_tracking_service is None:
            raise VisualPoseAnalysisCapabilityError(
                "Pose tracking is disabled by the current preview configuration."
            )
        return await self.pose_tracking_service.set_target_joint(target_joint)

    async def reload_factory_kwargs(self) -> Mapping[str, object]:
        """Expose the latest persisted-target equivalent to a trusted host reload.

        ``set_target_joint`` changes the immutable pose settings only after the Web
        facade has successfully persisted the selected joint. Returning that current
        settings object keeps a hot replacement aligned with the local configuration
        without giving the plugin authority to alter its module or factory identity.
        """

        if self.pose_tracking_service is None:
            return {}
        return {"pose_settings": self.pose_tracking_service.settings}


def build_visual_pose_analysis_plugin(
    camera_id: str,
    pose_settings: PoseTrackingSettings,
    vision_analysis_settings: VisionAnalysisSettings,
    estimator_factory: Optional[Callable[[], PoseEstimator]] = None,
) -> VisualPoseAnalysisPlugin:
    """Build one fresh visual plugin from trusted configuration for startup or reload.

    The optional factory is an application-side dependency seam for deterministic
    tests. Normal configurations omit it and create the existing lazy CUDA
    Keypoint R-CNN estimator without importing Torch or opening any hardware device.
    """

    pose_tracking_service = None
    if pose_settings.enabled:
        estimator = (
            estimator_factory()
            if estimator_factory is not None
            else TorchvisionKeypointRcnnEstimator(
                pose_settings.weights_path,
                pose_settings.device,
                pose_settings.inference_max_side,
                pose_settings.torch_cpu_threads,
                pose_settings.torch_interop_threads,
            )
        )
        pose_tracking_service = PoseTrackingService(camera_id, pose_settings, estimator)
    vision_analysis_service = VisionAnalysisService(
        camera_id,
        vision_analysis_settings,
        pose_settings,
        pose_tracking_service,
    )
    return VisualPoseAnalysisPlugin(camera_id, pose_tracking_service, vision_analysis_service)
