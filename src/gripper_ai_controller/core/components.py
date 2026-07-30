"""Plugin extension contracts used by the core runtime."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple

from gripper_ai_controller.domain.models import (
    CameraCalibration,
    CommandPayload,
    ComponentManifest,
    ImageFrame,
    PerceptionResult,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import LifecycleComponent


@dataclass(frozen=True)
class CameraBindingRequirement:
    """Declare the logical camera-source contract for one passive plugin.

    A plugin declares only its input requirement here. The host owns the actual
    source binding and frame routing, keeping plugins unable to open, select, or
    otherwise control camera hardware. ``shared_single_source`` describes the
    current preview runtime; future multi-source plugins can use
    ``plugin_sources`` without changing the base contract.
    """

    mode: str = "none"
    minimum_sources: int = 0
    maximum_sources: Optional[int] = 0

    def __post_init__(self) -> None:
        """Reject incomplete or internally inconsistent source declarations."""

        if self.mode not in ("none", "shared_single_source", "plugin_sources"):
            raise ValueError("Unsupported plugin camera binding mode: {0}".format(self.mode))
        if type(self.minimum_sources) is not int or self.minimum_sources < 0:
            raise ValueError("Plugin minimum camera sources must be a non-negative integer.")
        if self.maximum_sources is not None and (
            type(self.maximum_sources) is not int or self.maximum_sources < 0
        ):
            raise ValueError("Plugin maximum camera sources must be a non-negative integer or None.")
        if self.maximum_sources is not None and self.maximum_sources < self.minimum_sources:
            raise ValueError("Plugin maximum camera sources cannot be below the minimum.")
        if self.mode == "none" and (
            self.minimum_sources != 0 or self.maximum_sources != 0
        ):
            raise ValueError("A plugin without camera input must declare zero sources.")
        if self.mode == "shared_single_source" and (
            self.minimum_sources != 1 or self.maximum_sources != 1
        ):
            raise ValueError("A shared preview plugin must declare exactly one camera source.")

    @property
    def requires_camera(self) -> bool:
        """Return whether the host must route camera frames to this plugin."""

        return self.mode != "none"

    @classmethod
    def shared_single_source(cls) -> "CameraBindingRequirement":
        """Create the fixed one-source contract used by the current preview graph."""

        return cls("shared_single_source", 1, 1)


@dataclass(frozen=True)
class CameraBinding:
    """Bind one plugin to stable logical camera IDs owned by the preview host."""

    camera_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep bindings stable, explicit, and free of duplicate source IDs."""

        if not isinstance(self.camera_ids, tuple):
            raise TypeError("Plugin camera binding IDs must be a tuple.")
        if any(not isinstance(camera_id, str) or not camera_id for camera_id in self.camera_ids):
            raise ValueError("Plugin camera binding IDs must be non-empty strings.")
        if len(set(self.camera_ids)) != len(self.camera_ids):
            raise ValueError("Plugin camera binding IDs must not contain duplicates.")

    def satisfies(self, requirement: CameraBindingRequirement) -> bool:
        """Return whether this binding satisfies one immutable plugin declaration."""

        if not isinstance(requirement, CameraBindingRequirement):
            raise TypeError("Plugin camera binding requirement must be a CameraBindingRequirement.")
        count = len(self.camera_ids)
        return (
            count >= requirement.minimum_sources
            and (requirement.maximum_sources is None or count <= requirement.maximum_sources)
        )

    def accepts(self, camera_id: str) -> bool:
        """Return whether a frame from the logical source belongs to this binding."""

        return camera_id in self.camera_ids


class Plugin(LifecycleComponent):
    """A constrained extension that can observe events but cannot dispatch commands."""

    manifest = None  # type: ComponentManifest
    camera_binding_requirement = CameraBindingRequirement()

    async def handle_event(self, event: object) -> None:
        """Observe a typed event after the core has performed its state transition."""


class PerceptionPlugin(Plugin):
    """Transforms one image frame into structured visual observations."""

    @abstractmethod
    async def perceive(
        self,
        frame: ImageFrame,
        calibration: CameraCalibration,
        robot_status: RobotStatus,
    ) -> PerceptionResult:
        """Return semantic observations; never access a camera SDK directly."""


class PlannerPlugin(Plugin):
    """Produces one normalized command proposal from a valid perception result."""

    @abstractmethod
    async def propose(self, objective: str, perception: PerceptionResult) -> Optional[CommandPayload]:
        """Return a proposal for core authorization or None when no action is appropriate."""
