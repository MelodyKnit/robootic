"""An observer plugin that records event flow without controlling execution."""

from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.domain.models import ComponentManifest


class AuditPlugin(Plugin):
    """Collects typed events for diagnostics and deterministic tests."""

    manifest = ComponentManifest("audit", "0.1.0", "audit", ("event-observer",), "AuditPlugin")

    def __init__(self) -> None:
        """Create an empty in-memory event history for diagnostics and unit tests."""

        self.events = []
        self.started = False

    async def startup(self) -> None:
        """Mark the observer active after the core has established its event bus."""

        self.started = True

    async def shutdown(self) -> None:
        """Mark the observer inactive; recorded events remain available for diagnosis."""

        self.started = False

    async def handle_event(self, event: object) -> None:
        """Append the immutable event without interpreting or changing runtime state."""

        self.events.append(event)
