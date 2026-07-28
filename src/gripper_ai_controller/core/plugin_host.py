"""Bounded lifecycle hosting for browser-facing observer plugins."""

import asyncio
from dataclasses import dataclass, field, replace
import importlib
import inspect
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional, Tuple

from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.events import EventBus, FrameCaptured, RuntimeEvent
from gripper_ai_controller.core.registry import ComponentRegistry
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame


class PluginHostError(RuntimeError):
    """Report an invalid plugin-host lifecycle or factory operation."""


class UnknownPluginError(PluginHostError):
    """Report a request for a plugin identifier absent from this host."""


class PluginReloadNotAllowedError(PluginHostError):
    """Report a reload request disabled by the caller's runtime policy."""


class PluginReloadInProgressError(PluginHostError):
    """Report a conflicting request for an already reloading plugin."""


class PluginReloadFailedError(PluginHostError):
    """Report a failed replacement after the active plugin was preserved."""


@dataclass(frozen=True)
class PluginFactoryDescriptor:
    """Declare one reloadable plugin factory without accepting arbitrary imports from a request.

    Construction code builds this descriptor from trusted configuration before the
    preview service starts. Browser callers can later select only ``plugin_id``;
    they never supply a Python module name, factory name, or factory arguments.
    """

    plugin_id: str
    module_name: str
    factory_name: str
    factory_kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject incomplete trusted configuration before any module is imported."""

        for field_name, value in (
            ("plugin_id", self.plugin_id),
            ("module_name", self.module_name),
            ("factory_name", self.factory_name),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError("Plugin factory {0} must be a non-empty string.".format(field_name))
        if not isinstance(self.factory_kwargs, Mapping):
            raise TypeError("Plugin factory kwargs must be a mapping.")


@dataclass(frozen=True)
class PluginStatus:
    """Browser-safe lifecycle metadata for one configured plugin instance."""

    plugin_id: str
    name: str
    version: str
    capabilities: Tuple[str, ...]
    ui_kind: str
    lifecycle_state: str
    error: Optional[str]
    reloadable: bool


@dataclass
class _PluginSlot:
    """Keep the active instance and its immutable construction descriptor together."""

    descriptor: PluginFactoryDescriptor
    plugin: Plugin
    error: Optional[str] = None


class PluginHost:
    """Own passive plugin lifecycle, frame dispatch, status, and selective reload.

    The host is intentionally narrower than :class:`Runtime`: it accepts only
    typed frame events and exposes no adapters, targets, safety policy, or command
    dispatch method. This is the boundary that keeps browser-facing perception
    modules observable yet unable to command physical equipment.
    """

    def __init__(
        self,
        descriptors: Iterable[PluginFactoryDescriptor],
        event_bus: Optional[EventBus] = None,
        registry: Optional[ComponentRegistry] = None,
        reload_enabled: bool = False,
    ) -> None:
        """Bind trusted plugin descriptors to an owned event and registry boundary."""

        descriptor_by_id = {}  # type: Dict[str, PluginFactoryDescriptor]
        for descriptor in descriptors:
            if not isinstance(descriptor, PluginFactoryDescriptor):
                raise TypeError("PluginHost descriptors must be PluginFactoryDescriptor instances.")
            if descriptor.plugin_id in descriptor_by_id:
                raise ValueError("Duplicate plugin identifier: {0}".format(descriptor.plugin_id))
            descriptor_by_id[descriptor.plugin_id] = descriptor
        self._descriptors = descriptor_by_id
        self.event_bus = event_bus or EventBus()
        self.registry = registry or ComponentRegistry()
        self.reload_enabled = reload_enabled
        self._slots = {}  # type: Dict[str, _PluginSlot]
        self._started = False
        self._reloading = set()
        self._state_lock = None  # type: Optional[Any]
        self._frame_dispatch_lock = None  # type: Optional[Any]
        self._offered_frame_task = None  # type: Optional[asyncio.Task]
        self._pending_offered_frame = None  # type: Optional[Tuple[ImageFrame, Optional[Callable[[], Awaitable[None]]]]]
        self._subscribed = False

    @property
    def started(self) -> bool:
        """Return whether the host currently accepts frame publication."""

        return self._started

    async def startup(self) -> None:
        """Construct, start, register, and subscribe every configured plugin in order."""

        async with self._state_lock_for_current_loop():
            if self._started:
                return
            descriptors = tuple(self._descriptors.values())

        started_slots = []
        try:
            for descriptor in descriptors:
                slot = await self._create_started_slot(descriptor, reload_module=False)
                started_slots.append(slot)
        except Exception as error:
            for slot in reversed(started_slots):
                await self._shutdown_slot(slot)
                self.registry.unregister(slot.descriptor.plugin_id)
            raise PluginHostError("Unable to start configured plugin: {0}".format(error)) from error

        async with self._state_lock_for_current_loop():
            self._slots = {slot.descriptor.plugin_id: slot for slot in started_slots}
            self._started = True
            if not self._subscribed:
                self.event_bus.subscribe(self._handle_event)
                self._subscribed = True

    async def shutdown(self) -> None:
        """Stop plugins after blocking new frame delivery and clear their registry entries."""

        async with self._state_lock_for_current_loop():
            if not self._started:
                return
            self._started = False
            self._reloading = set(self._slots)
            slots = tuple(self._slots.values())
            offered_frame_task = self._offered_frame_task
            self._pending_offered_frame = None

        if offered_frame_task is not None:
            try:
                await offered_frame_task
            except asyncio.CancelledError:
                pass

        async with self._frame_dispatch_lock_for_current_loop():
            for slot in reversed(slots):
                await self._shutdown_slot(slot)
                self.registry.unregister(slot.descriptor.plugin_id)

        async with self._state_lock_for_current_loop():
            self._slots = {}
            self._reloading.clear()

    async def publish_frame(
        self,
        frame: ImageFrame,
        on_pose_inference_started: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> bool:
        """Publish one frame to active plugins and return whether any plugin accepted it.

        During a selective reload, a reloading plugin is intentionally omitted. The
        caller can therefore continue camera acquisition without waiting for a
        plugin replacement or accumulating stale image work.
        """

        if not isinstance(frame, ImageFrame):
            raise TypeError("PluginHost accepts ImageFrame instances only.")
        async with self._state_lock_for_current_loop():
            active = self._started and any(
                plugin_id not in self._reloading for plugin_id in self._slots
            )
        if not active:
            return False
        await self.event_bus.publish(
            FrameCaptured(time.time(), frame, on_pose_inference_started)
        )
        return True

    def offer_frame(
        self,
        frame: ImageFrame,
        on_pose_inference_started: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> bool:
        """Queue a browser-published frame without allowing slow plugins to block its producer.

        The JPEG publisher calls this synchronous handoff only after a frame is visible
        in the shared browser cache. One dispatcher task consumes it; while that task
        is waiting for a reload or an event observer, a newer offer replaces the older
        pending frame. This prevents a FIFO of stale analysis work from accumulating.
        The optional callback preserves only the JPEG whose frame later enters the
        pose model, never every browser preview frame.
        """

        if not isinstance(frame, ImageFrame):
            raise TypeError("PluginHost accepts ImageFrame instances only.")
        if not self._started:
            return False
        active_task = self._offered_frame_task
        if active_task is not None and not active_task.done():
            self._pending_offered_frame = (frame, on_pose_inference_started)
            return True
        self._pending_offered_frame = None
        self._offered_frame_task = asyncio.create_task(
            self._deliver_offered_frames(frame, on_pose_inference_started)
        )
        return True

    async def _deliver_offered_frames(
        self,
        first_frame: ImageFrame,
        first_on_pose_inference_started: Optional[Callable[[], Awaitable[None]]],
    ) -> None:
        """Dispatch one offered frame and at most the latest replacement after it finishes."""

        current_frame = first_frame
        current_on_pose_inference_started = first_on_pose_inference_started
        try:
            while True:
                await self.publish_frame(current_frame, current_on_pose_inference_started)
                pending_frame = self._pending_offered_frame
                self._pending_offered_frame = None
                if pending_frame is None:
                    return
                current_frame, current_on_pose_inference_started = pending_frame
        finally:
            self._offered_frame_task = None

    async def statuses(self) -> Tuple[PluginStatus, ...]:
        """Return configured plugin status in deterministic configuration order."""

        async with self._state_lock_for_current_loop():
            result = []
            for plugin_id, descriptor in self._descriptors.items():
                slot = self._slots.get(plugin_id)
                if slot is None:
                    result.append(
                        PluginStatus(
                            plugin_id,
                            plugin_id,
                            "",
                            (),
                            "generic",
                            "stopped",
                            None,
                            False,
                        )
                    )
                    continue
                manifest = self._manifest_for(slot.plugin)
                lifecycle_state = "reloading" if plugin_id in self._reloading else "running"
                result.append(
                    PluginStatus(
                        plugin_id,
                        manifest.name,
                        manifest.version,
                        manifest.capabilities,
                        self._ui_kind_for(slot.plugin),
                        lifecycle_state,
                        slot.error,
                        self.reload_enabled and lifecycle_state == "running",
                    )
                )
            return tuple(result)

    async def status(self, plugin_id: str) -> PluginStatus:
        """Return one configured plugin status or raise a stable unknown-plugin error."""

        for status in await self.statuses():
            if status.plugin_id == plugin_id:
                return status
        raise UnknownPluginError("Unknown plugin: {0}".format(plugin_id))

    async def get_plugin(self, plugin_id: str) -> Plugin:
        """Return one active plugin for a trusted server-side facade, never a browser request."""

        async with self._state_lock_for_current_loop():
            if plugin_id not in self._descriptors:
                raise UnknownPluginError("Unknown plugin: {0}".format(plugin_id))
            slot = self._slots.get(plugin_id)
            if slot is None:
                raise PluginHostError("Plugin is not started: {0}".format(plugin_id))
            return slot.plugin

    async def reload(self, plugin_ids: Iterable[str] = ()) -> Tuple[PluginStatus, ...]:
        """Atomically replace selected plugins while preserving an old instance on failure.

        A new instance is fully constructed and started before the active instance is
        removed. If import, construction, or startup fails, the old plugin remains
        registered and continues receiving later frames after the reload marker clears.
        """

        if not self.reload_enabled:
            raise PluginReloadNotAllowedError("Plugin reload is disabled by the current runtime policy.")
        requested = tuple(plugin_ids)
        if not requested:
            requested = tuple(self._descriptors)
        if len(set(requested)) != len(requested):
            raise ValueError("Plugin reload identifiers must not contain duplicates.")

        async with self._state_lock_for_current_loop():
            if not self._started:
                raise PluginHostError("PluginHost must be started before reloading plugins.")
            unknown = [plugin_id for plugin_id in requested if plugin_id not in self._descriptors]
            if unknown:
                raise UnknownPluginError("Unknown plugin: {0}".format(unknown[0]))
            in_progress = [plugin_id for plugin_id in requested if plugin_id in self._reloading]
            if in_progress:
                raise PluginReloadInProgressError(
                    "Plugin reload is already in progress: {0}".format(in_progress[0])
                )
            self._reloading.update(requested)

        failures = []
        try:
            async with self._frame_dispatch_lock_for_current_loop():
                for plugin_id in requested:
                    error = await self._reload_one(plugin_id)
                    if error is not None:
                        failures.append((plugin_id, error))
        finally:
            async with self._state_lock_for_current_loop():
                self._reloading.difference_update(requested)

        if failures:
            failure_messages = "; ".join(
                "{0}: {1}".format(plugin_id, error) for plugin_id, error in failures
            )
            raise PluginReloadFailedError(
                "One or more plugin replacements failed; active instances were preserved. {0}".format(
                    failure_messages
                )
            )
        return await self.statuses()

    async def _handle_event(self, event: RuntimeEvent) -> None:
        """Forward frame events to active plugins while isolating observer failures."""

        if not isinstance(event, FrameCaptured):
            return
        async with self._frame_dispatch_lock_for_current_loop():
            async with self._state_lock_for_current_loop():
                if not self._started:
                    return
                slots = tuple(
                    slot
                    for plugin_id, slot in self._slots.items()
                    if plugin_id not in self._reloading
                )
            for slot in slots:
                try:
                    await slot.plugin.handle_event(event)
                except Exception as error:
                    await self._record_error(slot.descriptor.plugin_id, str(error))

    async def _reload_one(self, plugin_id: str) -> Optional[str]:
        """Replace one plugin only after its replacement has started successfully."""

        async with self._state_lock_for_current_loop():
            old_slot = self._slots[plugin_id]
        replacement = None  # type: Optional[_PluginSlot]
        try:
            descriptor = await self._descriptor_for_reload(old_slot)
            replacement = await self._create_started_slot(descriptor, reload_module=True)
            async with self._state_lock_for_current_loop():
                self.registry.replace(replacement.plugin, plugin_id)
                self._slots[plugin_id] = replacement
        except Exception as error:
            if replacement is not None:
                await self._shutdown_slot(replacement)
            message = "Plugin replacement failed: {0}".format(error)
            await self._record_error(plugin_id, message)
            return message
        shutdown_error = await self._shutdown_slot(old_slot)
        if shutdown_error is not None:
            message = "Previous plugin shutdown failed after replacement: {0}".format(shutdown_error)
            await self._record_error(plugin_id, message)
        return None

    async def _create_started_slot(
        self, descriptor: PluginFactoryDescriptor, reload_module: bool
    ) -> _PluginSlot:
        """Load a trusted factory, validate its plugin, and start its lifecycle."""

        module = importlib.import_module(descriptor.module_name)
        if reload_module:
            module = importlib.reload(module)
        factory = getattr(module, descriptor.factory_name, None)
        if not callable(factory):
            raise PluginHostError(
                "Plugin factory is unavailable: {0}.{1}".format(
                    descriptor.module_name, descriptor.factory_name
                )
            )
        plugin = factory(**dict(descriptor.factory_kwargs))
        if not isinstance(plugin, Plugin):
            raise TypeError("Plugin factories must return Plugin instances.")
        manifest = self._manifest_for(plugin)
        if manifest.name != descriptor.plugin_id:
            raise PluginHostError(
                "Plugin factory returned manifest '{0}' for configured plugin '{1}'.".format(
                    manifest.name, descriptor.plugin_id
                )
            )
        try:
            await plugin.startup()
            if not reload_module:
                self.registry.register(plugin, descriptor.plugin_id)
        except Exception:
            try:
                await plugin.shutdown()
            except Exception:
                pass
            raise
        slot = _PluginSlot(descriptor, plugin)
        return slot

    async def _shutdown_slot(self, slot: _PluginSlot) -> Optional[str]:
        """Shutdown one plugin while converting cleanup errors into observable status."""

        try:
            await slot.plugin.shutdown()
        except Exception as error:
            return str(error)
        return None

    async def _record_error(self, plugin_id: str, message: str) -> None:
        """Store an operator-safe plugin error without interrupting other frame observers."""

        async with self._state_lock_for_current_loop():
            slot = self._slots.get(plugin_id)
            if slot is not None:
                slot.error = message

    async def _descriptor_for_reload(self, slot: _PluginSlot) -> PluginFactoryDescriptor:
        """Merge an active plugin's trusted state into a fresh replacement descriptor.

        A browser pose-target update is persisted before it updates the active plugin.
        The optional provider therefore lets that plugin preserve its latest in-memory
        configuration during reload without allowing a plugin to change the module or
        factory selected by trusted application configuration.
        """

        provider = getattr(slot.plugin, "reload_factory_kwargs", None)
        if not callable(provider):
            return slot.descriptor
        overrides = provider()
        if inspect.isawaitable(overrides):
            overrides = await overrides
        if not isinstance(overrides, Mapping):
            raise PluginHostError("Plugin reload factory overrides must be a mapping.")
        factory_kwargs = dict(slot.descriptor.factory_kwargs)
        factory_kwargs.update(overrides)
        return replace(slot.descriptor, factory_kwargs=factory_kwargs)

    def _state_lock_for_current_loop(self) -> Any:
        """Create host state protection lazily for Python 3.7 ASGI construction."""

        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        return self._state_lock

    def _frame_dispatch_lock_for_current_loop(self) -> Any:
        """Serialize frame delivery with replacement and shutdown transitions."""

        if self._frame_dispatch_lock is None:
            self._frame_dispatch_lock = asyncio.Lock()
        return self._frame_dispatch_lock

    @staticmethod
    def _manifest_for(plugin: Plugin) -> ComponentManifest:
        """Return a validated manifest from a plugin instance."""

        manifest = getattr(plugin, "manifest", None)
        if not isinstance(manifest, ComponentManifest):
            raise TypeError("Plugins must expose a ComponentManifest.")
        return manifest

    @staticmethod
    def _ui_kind_for(plugin: Plugin) -> str:
        """Return an explicitly declared UI hint without trusting arbitrary object values."""

        ui_kind = getattr(plugin, "ui_kind", "generic")
        return ui_kind if isinstance(ui_kind, str) and ui_kind else "generic"
