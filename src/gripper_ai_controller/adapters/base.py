"""Shared lifecycle and frame-observer behavior for adapter implementations."""

import asyncio
from typing import List, Optional, Union

from gripper_ai_controller.domain.models import ImageFrame
from gripper_ai_controller.domain.ports import (
    FrameHandler,
    FrameHandlerDecorator,
    LifecycleComponent,
    VisionAdapter,
)


class BaseAdapter(LifecycleComponent):
    """Provide idempotent lifecycle state and guarded adapter operations.

    Subclasses override the lifecycle hooks to allocate or release their own resources.
    The base class never opens a device connection by itself, so introducing it does not
    change the behavior of existing adapters.
    """

    def __init__(self) -> None:
        """Create an adapter that has not entered its runtime lifecycle."""

        self._started = False

    @property
    def started(self) -> bool:
        """Return whether the adapter completed its startup lifecycle hook."""

        return self._started

    async def startup(self) -> None:
        """Start the adapter once and mark it available after a successful hook."""

        if self._started:
            return
        await self.on_startup()
        self._started = True

    async def shutdown(self) -> None:
        """Stop the adapter once and clear lifecycle state even when cleanup fails."""

        if not self._started:
            return
        try:
            await self.on_shutdown()
        finally:
            self._started = False

    def ensure_started(self) -> None:
        """Raise when an operation requires an adapter that has not been started."""

        if not self._started:
            raise RuntimeError("Adapter must be started before this operation.")

    async def on_startup(self) -> None:
        """Allocate subclass resources after the runtime requests startup."""

    async def on_shutdown(self) -> None:
        """Release subclass resources before the runtime marks the adapter stopped."""


class FrameDispatchingVisionAdapter(BaseAdapter, VisionAdapter):
    """Provide instance-scoped asynchronous frame observation for vision adapters.

    Concrete camera adapters construct an ``ImageFrame`` after a successful acquisition
    and call ``emit_frame`` before returning it from ``capture``. This keeps observer
    delivery independent from the core runtime event bus while preserving a normalized
    frame contract for every camera implementation.
    """

    def __init__(self) -> None:
        """Create a stopped vision adapter with no registered frame observers."""

        super().__init__()
        self._frame_handlers = []  # type: List[FrameHandler]

    def on_frame(
        self, handler: Optional[FrameHandler] = None
    ) -> Union[FrameHandler, FrameHandlerDecorator]:
        """Register an async frame observer or return a decorator that registers one.

        Re-registering the same callable is idempotent. Synchronous callbacks are
        rejected during registration so a successful ``capture`` never silently drops
        a result that a caller expected the observer to await.
        """

        def register(callback: FrameHandler) -> FrameHandler:
            """Validate and retain one observer without changing its callable identity."""

            if not asyncio.iscoroutinefunction(callback):
                raise TypeError("Frame handlers must be declared with async def.")
            if callback not in self._frame_handlers:
                self._frame_handlers.append(callback)
            return callback

        if handler is None:
            return register
        return register(handler)

    async def emit_frame(self, frame: ImageFrame) -> None:
        """Notify frame observers in registration order after a successful acquisition.

        A tuple snapshot makes changes made by a callback take effect on the next frame.
        Observer exceptions intentionally propagate to the capture caller; an observer
        failure must not be silently discarded or reported as a camera-device failure.
        """

        for handler in tuple(self._frame_handlers):
            await handler(frame)
