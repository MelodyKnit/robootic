"""Plugin extension contracts used by the core runtime."""

from abc import ABC, abstractmethod
from typing import Optional

from gripper_ai_controller.domain.models import (
    CameraCalibration,
    CommandPayload,
    ComponentManifest,
    ImageFrame,
    PerceptionResult,
    RobotStatus,
)
from gripper_ai_controller.domain.ports import LifecycleComponent


class Plugin(LifecycleComponent):
    """A constrained extension that can observe events but cannot dispatch commands."""

    manifest = None  # type: ComponentManifest

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
