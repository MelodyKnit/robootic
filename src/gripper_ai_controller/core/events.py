"""Typed runtime events and a deterministic asynchronous event bus."""

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

from gripper_ai_controller.domain.models import (
    AdapterExecutionReport,
    CommandEnvelope,
    PerceptionResult,
    SafetyDecision,
    TelemetrySnapshot,
    ImageFrame,
)


@dataclass(frozen=True)
class RuntimeEvent:
    """Base event with a monotonic wall-clock timestamp."""

    occurred_at: float


@dataclass(frozen=True)
class ObjectiveRequested(RuntimeEvent):
    """Signals the beginning of one objective cycle before perception is captured."""

    objective: str


@dataclass(frozen=True)
class FrameCaptured(RuntimeEvent):
    """Publishes a frame emitted by a vision adapter for perception plugins to observe.

    ``on_pose_inference_started`` is an optional preview-only callback. It carries no
    adapter or command capability: the browser preview uses it only to preserve the
    already encoded JPEG when a bounded pose worker actually starts inference for
    this frame. Runtime-produced frame events leave it unset.
    """

    frame: ImageFrame
    on_pose_inference_started: Optional[Callable[[], Awaitable[None]]] = None


@dataclass(frozen=True)
class PerceptionCompleted(RuntimeEvent):
    """Publishes the validated or rejected semantic interpretation of one frame."""

    perception: PerceptionResult


@dataclass(frozen=True)
class CommandProposed(RuntimeEvent):
    """Records a planner proposal before deterministic safety authorization occurs."""

    command: CommandEnvelope


@dataclass(frozen=True)
class CommandAuthorized(RuntimeEvent):
    """Records that the core safety policy authorized a command for dispatch."""

    command: CommandEnvelope
    decision: SafetyDecision


@dataclass(frozen=True)
class CommandRejected(RuntimeEvent):
    """Records a safety rejection; no execution target receives this command."""

    command: CommandEnvelope
    decision: SafetyDecision


@dataclass(frozen=True)
class CommandDispatched(RuntimeEvent):
    """Reports the outcome of sending a command to one named execution target."""

    report: AdapterExecutionReport


@dataclass(frozen=True)
class TelemetryUpdated(RuntimeEvent):
    """Publishes authoritative or corrected target telemetry after an execution step."""

    target_name: str
    telemetry: TelemetrySnapshot


@dataclass(frozen=True)
class CommandCompleted(RuntimeEvent):
    """Marks completion of a command cycle after primary and mirror reports are collected."""

    command: CommandEnvelope
    reports: tuple


@dataclass(frozen=True)
class AdapterFailed(RuntimeEvent):
    """Reports an adapter failure without granting observers retry authority."""

    component_name: str
    message: str


EventHandler = Callable[[RuntimeEvent], Awaitable[None]]


class EventBus:
    """Delivers events in subscription order for deterministic testable behavior."""

    def __init__(self) -> None:
        """Initialize an empty ordered observer list for this runtime instance."""

        self.handlers = []  # type: List[EventHandler]

    def subscribe(self, handler: EventHandler) -> None:
        """Register an event observer without granting execution authority."""

        self.handlers.append(handler)

    async def publish(self, event: RuntimeEvent) -> None:
        """Deliver an event sequentially so observers see a stable lifecycle order."""

        for handler in tuple(self.handlers):
            await handler(event)
