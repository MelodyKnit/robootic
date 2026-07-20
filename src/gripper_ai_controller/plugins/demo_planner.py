"""A deterministic planning plugin used until an LLM provider is integrated."""

from typing import Optional

from gripper_ai_controller.core.components import PlannerPlugin
from gripper_ai_controller.domain.models import ComponentManifest, GripperAction, GripperCommand, PerceptionResult


class DemonstrationPlannerPlugin(PlannerPlugin):
    """Proposes a bounded gripper action only when calibrated grasp data exists."""

    manifest = ComponentManifest(
        "demonstration-planner",
        "0.1.0",
        "planner",
        ("planner",),
        "DemonstrationPlannerPlugin",
    )

    async def propose(self, objective: str, perception: PerceptionResult) -> Optional[GripperCommand]:
        """Return a bounded demo command only when calibrated grasp data is available.

        The method is a stand-in for a future LLM plugin. It does not send an adapter
        command and returning ``None`` leaves the core runtime in a no-dispatch state.
        """

        if not perception.valid or not perception.objects:
            return None
        if not perception.objects[0].grasp_candidates:
            return None
        if "open" in objective.lower():
            return GripperCommand(GripperAction.OPEN, target_position=1000, force_percent=20, speed_percent=30)
        return GripperCommand(GripperAction.CLOSE, target_position=300, force_percent=30, speed_percent=30)
