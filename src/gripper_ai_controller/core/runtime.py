"""The asynchronous facade that coordinates perception, safety, and execution."""

import asyncio
import importlib
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Tuple

from gripper_ai_controller.core.components import PerceptionPlugin, PlannerPlugin, Plugin
from gripper_ai_controller.core.events import (
    AdapterFailed,
    CommandAuthorized,
    CommandCompleted,
    CommandDispatched,
    CommandProposed,
    CommandRejected,
    EventBus,
    FrameCaptured,
    ObjectiveRequested,
    PerceptionCompleted,
    TelemetryUpdated,
)
from gripper_ai_controller.core.registry import ComponentRegistry
from gripper_ai_controller.core.targets import ExecutionTarget
from gripper_ai_controller.domain.models import (
    CameraCalibration,
    CommandEnvelope,
    ExecutionResult,
    RobotCommand,
    RuntimeMode,
    SafetyDecision,
    TargetRole,
)
from gripper_ai_controller.domain.ports import VisionAdapter
from gripper_ai_controller.services.safety import SafetyPolicy


@dataclass
class RuntimeConfig:
    """Dependency-injected component graph assembled by a Python config module."""

    mode: RuntimeMode
    vision: VisionAdapter
    calibration: CameraCalibration
    perception_plugin: PerceptionPlugin
    planner_plugins: Tuple[PlannerPlugin, ...]
    observer_plugins: Tuple[Plugin, ...]
    targets: Tuple[ExecutionTarget, ...]
    safety_policy: SafetyPolicy


class Runtime:
    """Owns lifecycle, event delivery, safety authorization, and mirror dispatch."""

    def __init__(
        self,
        config: RuntimeConfig,
        rebuild_config: Optional[Callable[[], RuntimeConfig]] = None,
    ) -> None:
        """Bind one immutable component graph and its optional development rebuild factory.

        The runtime owns the dispatch lock so reload and normal objective execution cannot
        overlap. `rebuild_config` is supplied only by development configurations and must
        construct a fresh graph after modules have been reloaded.
        """

        self.config = config
        self.rebuild_config = rebuild_config
        self.event_bus = EventBus()
        self.registry = ComponentRegistry()
        self.dispatch_lock = asyncio.Lock()
        self.started = False

    async def startup(self) -> None:
        """Start adapters and plugins, then subscribe plugins as event observers."""

        if self.started:
            return
        self.event_bus = EventBus()
        self.registry = ComponentRegistry()
        for target in self.config.targets:
            await target.startup()
            self.registry.register(target.robot, "{0}.robot".format(target.name))
            self.registry.register(target.gripper, "{0}.gripper".format(target.name))
        await self.config.vision.startup()
        self.registry.register(self.config.vision)
        for plugin in self._all_plugins():
            await plugin.startup()
            self.registry.register(plugin)
            self.event_bus.subscribe(plugin.handle_event)
        self.started = True

    async def shutdown(self) -> None:
        """Stop plugins before adapters and prevent further dispatch."""

        if not self.started:
            return
        for plugin in reversed(self._all_plugins()):
            await plugin.shutdown()
        await self.config.vision.shutdown()
        for target in reversed(self.config.targets):
            await target.shutdown()
        self.started = False

    async def run_objective(self, objective: str) -> ExecutionResult:
        """Run one perception-plan-authorize-dispatch cycle under one dispatch lock."""

        async with self.dispatch_lock:
            if not self.started:
                raise RuntimeError("Runtime must be started before running objectives.")
            now = time.time()
            await self.event_bus.publish(ObjectiveRequested(now, objective))
            frame = await self.config.vision.capture()
            await self.event_bus.publish(FrameCaptured(time.time(), frame))
            primary = self._primary_target()
            primary_telemetry = await primary.telemetry()
            perception = await self.config.perception_plugin.perceive(
                frame, self.config.calibration, primary_telemetry.robot
            )
            await self.event_bus.publish(PerceptionCompleted(time.time(), perception))

            payload = await self._propose(objective, perception)
            if payload is None:
                decision = SafetyDecision(False, "No planner plugin proposed a command.")
                return ExecutionResult(None, decision, (), perception)
            command = CommandEnvelope(str(uuid.uuid4()), time.time(), payload)
            await self.event_bus.publish(CommandProposed(time.time(), command))
            decision = self.config.safety_policy.evaluate(
                command, primary_telemetry.robot, primary_telemetry.gripper, perception
            )
            if decision.allowed and isinstance(command.payload, RobotCommand):
                decision = primary.evaluate_robot_motion(command.payload, primary_telemetry.robot)
            if not decision.allowed:
                await self.event_bus.publish(CommandRejected(time.time(), command, decision))
                return ExecutionResult(command, decision, (), perception)

            await self.event_bus.publish(CommandAuthorized(time.time(), command, decision))
            reports = []
            primary_report = await primary.execute(command)
            reports.append(primary_report)
            await self.event_bus.publish(CommandDispatched(time.time(), primary_report))
            if not primary_report.succeeded:
                await self.event_bus.publish(AdapterFailed(time.time(), primary.name, primary_report.message))
                return ExecutionResult(command, decision, tuple(reports), perception)

            authoritative = primary_report.telemetry
            if authoritative is not None:
                await self.event_bus.publish(TelemetryUpdated(time.time(), primary.name, authoritative))
            for mirror in self._mirror_targets():
                report = await mirror.execute(command)
                reports.append(report)
                await self.event_bus.publish(CommandDispatched(time.time(), report))
                if not report.succeeded:
                    await self.event_bus.publish(AdapterFailed(time.time(), mirror.name, report.message))
                    continue
                if authoritative is not None:
                    await mirror.synchronize(authoritative)
                    corrected = await mirror.telemetry()
                    await self.event_bus.publish(TelemetryUpdated(time.time(), mirror.name, corrected))
            await self.event_bus.publish(CommandCompleted(time.time(), command, tuple(reports)))
            return ExecutionResult(command, decision, tuple(reports), perception)

    async def reload_modules(self, module_names: Iterable[str]) -> None:
        """Safely rebuild development components after an explicit reload request."""

        if self.config.mode == RuntimeMode.PRODUCTION:
            raise RuntimeError("Production runtimes require a process restart for module updates.")
        if self.rebuild_config is None:
            raise RuntimeError("This runtime has no configuration builder for reload.")
        async with self.dispatch_lock:
            await self.shutdown()
            for module_name in module_names:
                module = importlib.import_module(module_name)
                if module_name in sys.modules:
                    importlib.reload(module)
            self.config = self.rebuild_config()
            await self.startup()

    async def _propose(self, objective: str, perception: object) -> object:
        """Ask planner plugins in registration order for the first valid proposal."""

        for planner in self.config.planner_plugins:
            proposal = await planner.propose(objective, perception)
            if proposal is not None:
                return proposal
        return None

    def _all_plugins(self) -> Tuple[Plugin, ...]:
        """Return every plugin exactly once in lifecycle order."""

        return (self.config.perception_plugin,) + self.config.planner_plugins + self.config.observer_plugins

    def _primary_target(self) -> ExecutionTarget:
        """Return the single authoritative target defined by configuration."""

        primaries = [target for target in self.config.targets if target.role == TargetRole.PRIMARY]
        if len(primaries) != 1:
            raise RuntimeError("Runtime configuration requires exactly one primary target.")
        return primaries[0]

    def _mirror_targets(self) -> Tuple[ExecutionTarget, ...]:
        """Return all non-authoritative targets in configuration order."""

        return tuple(target for target in self.config.targets if target.role == TargetRole.MIRROR)
