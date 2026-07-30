"""Passive semantic object-detection plugin for the shared camera preview."""

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from gripper_ai_controller.core.components import CameraBindingRequirement, Plugin
from gripper_ai_controller.core.events import FrameCaptured
from gripper_ai_controller.domain.models import ComponentManifest
from gripper_ai_controller.object_detection.models import (
    DetectionModelProfile,
    DetectionProvider,
    ObjectDetectionSettings,
)
from gripper_ai_controller.object_detection.providers import (
    TorchvisionFasterRcnnProvider,
    YoloWorldOnnxOpenCvProvider,
)
from gripper_ai_controller.object_detection.tracker import (
    ObjectDetectionTrackingService,
    ObjectDetectionTrackingSnapshot,
)


@dataclass(frozen=True)
class ObjectDetectionAnalysisPluginSnapshot:
    """Detection cache plus lifecycle state for the browser diagnostics boundary."""

    camera_id: str
    started: bool
    detection: ObjectDetectionTrackingSnapshot


class ObjectDetectionAnalysisPlugin(Plugin):
    """Consume only already-captured frames and publish normalized bounding boxes.

    The plugin has no camera adapter, robot adapter, gripper adapter, safety policy,
    or command method. Model selection only swaps a configured passive provider.
    """

    manifest = ComponentManifest(
        "object-detection-analysis",
        "0.1.0",
        "plugin",
        ("frame-observer", "object-detection", "bounding-boxes"),
        "build_object_detection_analysis_plugin",
    )
    ui_kind = "object-detection-analysis"
    camera_binding_requirement = CameraBindingRequirement.shared_single_source()

    def __init__(
        self,
        camera_id: str,
        settings: ObjectDetectionSettings,
        tracking_service: ObjectDetectionTrackingService,
    ) -> None:
        """Bind one passive worker without starting a device or model download."""

        self.camera_id = camera_id
        self.settings = settings
        self.tracking_service = tracking_service
        self._started = False
        self._shutdown = False

    async def startup(self) -> None:
        """Mark the observer ready; acquisition remains owned by the preview service."""

        if self._shutdown:
            raise RuntimeError("A stopped object detection plugin cannot be restarted.")
        self._started = True

    async def shutdown(self) -> None:
        """Drain the one inference worker and release its executor."""

        if self._shutdown:
            return
        self._started = False
        self._shutdown = True
        await self.tracking_service.shutdown()

    async def handle_event(self, event: object) -> None:
        """Submit only matching ``FrameCaptured`` events."""

        if not self._started or not isinstance(event, FrameCaptured):
            return
        if event.frame.camera_id != self.camera_id:
            return
        await self.tracking_service.submit_frame(event.frame)

    async def reset_frame_state(self) -> None:
        """Discard source-dependent detections after a camera transition."""

        if self._started and not self._shutdown:
            await self.tracking_service.reset_frame_state()

    async def detection_snapshot(self) -> ObjectDetectionTrackingSnapshot:
        """Return cache metadata only; no capture or inference is triggered."""

        return await self.tracking_service.snapshot()

    async def snapshot(self) -> ObjectDetectionAnalysisPluginSnapshot:
        """Expose compact lifecycle diagnostics at the trusted plugin boundary."""

        return ObjectDetectionAnalysisPluginSnapshot(
            self.camera_id,
            self._started,
            await self.detection_snapshot(),
        )

    def model_profiles(self) -> Tuple[DetectionModelProfile, ...]:
        """Return the immutable configured model catalog for read-only API rendering."""

        return self.settings.models

    async def select_model(self, model_id: str) -> ObjectDetectionTrackingSnapshot:
        """Select one configured model and clear all detections from the prior model."""

        return await self.tracking_service.select_model(model_id)

    async def reload_factory_kwargs(self) -> Mapping[str, object]:
        """Keep a hot replacement aligned with trusted immutable local settings."""

        return {"object_detection_settings": self.settings}


def build_object_detection_analysis_plugin(
    camera_id: str,
    object_detection_settings: ObjectDetectionSettings,
) -> ObjectDetectionAnalysisPlugin:
    """Build configured providers without opening a camera or downloading model files."""

    providers = {}  # type: dict
    setup_error = None  # type: Optional[str]
    for profile in object_detection_settings.models:
        try:
            # Provider constructors only validate immutable settings. Model files stay
            # unopened until an enabled worker receives its first frame.
            providers[profile.model_id] = _build_provider(profile)
        except (TypeError, ValueError, OSError):
            if profile.model_id == object_detection_settings.selected_model_id:
                setup_error = "object_detection_model_configuration_invalid"
    service = ObjectDetectionTrackingService(
        camera_id=camera_id,
        enabled=object_detection_settings.enabled,
        providers=providers,
        selected_model_id=(
            object_detection_settings.selected_model_id
            if object_detection_settings.selected_model_id in providers
            else None
        ),
        max_analysis_fps=object_detection_settings.max_analysis_fps,
        overlay_max_frame_lag_seconds=object_detection_settings.overlay_max_frame_lag_seconds,
        setup_error=setup_error,
    )
    return ObjectDetectionAnalysisPlugin(camera_id, object_detection_settings, service)


def _build_provider(profile: DetectionModelProfile) -> DetectionProvider:
    """Construct only allow-listed provider classes from a validated profile."""

    if profile.provider_id == TorchvisionFasterRcnnProvider.provider_id:
        return TorchvisionFasterRcnnProvider(
            weights_path=profile.model_path,
            device=profile.device,
            confidence_threshold=profile.confidence_threshold,
            allowed_labels=profile.allowed_labels,
            inference_max_side=profile.inference_max_side,
            max_detections=profile.max_detections,
        )
    if profile.provider_id == YoloWorldOnnxOpenCvProvider.provider_id:
        return YoloWorldOnnxOpenCvProvider(
            model_path=profile.model_path,
            class_names=profile.class_names,
            input_size=profile.input_size,
            confidence_threshold=profile.confidence_threshold,
            nms_iou_threshold=profile.nms_iou_threshold,
            max_detections=profile.max_detections,
            output_format=profile.output_format,
            backend=profile.backend,
        )
    raise ValueError("Unsupported object detection provider: {0}".format(profile.provider_id))
