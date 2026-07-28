"""Tests for passive plugin hosting and visual-pose plugin lifecycle boundaries."""

import asyncio
from unittest.mock import patch
import unittest

from gripper_ai_controller.configuration import PoseTrackingSettings, VisionAnalysisSettings
from gripper_ai_controller.core.components import Plugin
from gripper_ai_controller.core.events import FrameCaptured
from gripper_ai_controller.core.plugin_host import (
    PluginFactoryDescriptor,
    PluginHost,
    PluginReloadFailedError,
)
from gripper_ai_controller.domain.models import ComponentManifest, ImageFrame
from gripper_ai_controller.plugins.visual_pose_analysis import (
    VisualPoseAnalysisCapabilityError,
    VisualPoseAnalysisPlugin,
    build_visual_pose_analysis_plugin,
)


class RecordingPlugin(Plugin):
    """Record lifecycle and frame observer calls for host lifecycle tests."""

    manifest = ComponentManifest("recording-plugin", "0.1.0", "plugin", ("frame-observer",))
    ui_kind = "test-recording"

    def __init__(self, events, startup_gate=None, fail_startup=False, fail_on_event=False):
        """Bind deterministic mutable test seams without any hardware dependency."""

        self.events = events
        self.startup_gate = startup_gate
        self.fail_startup = fail_startup
        self.fail_on_event = fail_on_event

    async def startup(self):
        self.events.append("startup")
        if self.startup_gate is not None:
            await self.startup_gate.wait()
        if self.fail_startup:
            raise RuntimeError("planned startup failure")

    async def shutdown(self):
        self.events.append("shutdown")

    async def handle_event(self, event):
        self.events.append(event)
        if self.fail_on_event:
            raise RuntimeError("planned frame observer failure")


class TargetRetainingPlugin(Plugin):
    """Expose a mutable trusted setting to prove host reload state preservation."""

    manifest = ComponentManifest("target-retaining-plugin", "0.1.0", "plugin", ("frame-observer",))

    def __init__(self, target_joint, created_targets):
        self.target_joint = target_joint
        self.created_targets = created_targets

    async def startup(self):
        self.created_targets.append(self.target_joint)

    async def reload_factory_kwargs(self):
        return {"target_joint": self.target_joint}


def recording_plugin_factory(events, startup_schedule=None, fail_on_event=False):
    """Build test plugins with a mutable startup schedule that survives module reload mocks."""

    next_startup = None
    if startup_schedule:
        next_startup = startup_schedule.pop(0)
    if isinstance(next_startup, Exception):
        return RecordingPlugin(events, fail_startup=True, fail_on_event=fail_on_event)
    return RecordingPlugin(events, startup_gate=next_startup, fail_on_event=fail_on_event)


def target_retaining_plugin_factory(target_joint, created_targets):
    """Build a reloadable plugin that records the target supplied by its factory."""

    return TargetRetainingPlugin(target_joint, created_targets)


class FakePoseTracker:
    """Capture plugin calls without scheduling real CUDA inference in unit tests."""

    def __init__(self):
        self.submitted_frames = []
        self.target_joints = []
        self.inference_started_callbacks = []
        self.shutdown_called = False

    async def submit_frame(self, frame, on_inference_started=None):
        self.submitted_frames.append(frame)
        self.inference_started_callbacks.append(on_inference_started)
        if on_inference_started is not None:
            await on_inference_started()
        return True

    async def set_target_joint(self, target_joint):
        self.target_joints.append(target_joint)
        return target_joint

    async def shutdown(self):
        self.shutdown_called = True


class FakeVisionAnalysis:
    """Capture plugin calls without reading image pixels in unit tests."""

    def __init__(self):
        self.recorded_frames = []
        self.shutdown_called = False

    async def record_frame(self, frame):
        self.recorded_frames.append(frame)

    async def shutdown(self):
        self.shutdown_called = True

    async def snapshot(self):
        return "analysis"


class PluginHostTests(unittest.TestCase):
    """Exercise lifecycle, failure isolation, and atomic replacement behavior."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def descriptor(self, events, startup_schedule=None, fail_on_event=False):
        """Create a trusted descriptor pointing at this test module's factory."""

        return PluginFactoryDescriptor(
            "recording-plugin",
            __name__,
            "recording_plugin_factory",
            {
                "events": events,
                "startup_schedule": startup_schedule,
                "fail_on_event": fail_on_event,
            },
        )

    def test_startup_dispatch_and_shutdown_follow_owned_lifecycle(self):
        async def scenario():
            events = []
            host = PluginHost((self.descriptor(events),), reload_enabled=True)
            await host.startup()
            self.assertTrue(host.started)
            self.assertIn("recording-plugin", host.registry.components)
            self.assertTrue(await host.publish_frame(self.frame("camera-a")))
            frame_events = [event for event in events if isinstance(event, FrameCaptured)]
            self.assertEqual(1, len(frame_events))
            status = await host.status("recording-plugin")
            self.assertEqual("running", status.lifecycle_state)
            self.assertEqual("test-recording", status.ui_kind)
            await host.shutdown()
            self.assertFalse(host.started)
            self.assertNotIn("recording-plugin", host.registry.components)
            self.assertEqual(["startup", "shutdown"], [item for item in events if isinstance(item, str)])

        self.run_async(scenario())

    def test_plugin_frame_failure_is_isolated_and_recorded(self):
        async def scenario():
            failing_events = []
            recording_events = []
            host = PluginHost(
                (
                    self.descriptor(failing_events, fail_on_event=True),
                    PluginFactoryDescriptor(
                        "second-recording-plugin",
                        __name__,
                        "second_recording_plugin_factory",
                        {"events": recording_events},
                    ),
                )
            )
            await host.startup()
            try:
                self.assertTrue(await host.publish_frame(self.frame("camera-a")))
                self.assertEqual(1, len([item for item in recording_events if isinstance(item, FrameCaptured)]))
                status = await host.status("recording-plugin")
                self.assertIn("planned frame observer failure", status.error)
            finally:
                await host.shutdown()

        self.run_async(scenario())

    def test_offered_frames_are_dispatched_asynchronously_and_keep_the_latest_offer(self):
        """Keep post-JPEG offers detached from the caller while retaining a latest replacement."""

        async def scenario():
            events = []
            host = PluginHost((self.descriptor(events),))
            await host.startup()
            first = self.frame("camera-a")
            latest = ImageFrame(
                "camera-a", 2.0, None, None, True, b"\x01\x02\x03", 1, 1, "rgb8"
            )
            self.assertTrue(host.offer_frame(first))
            self.assertTrue(host.offer_frame(latest))
            for _ in range(100):
                frame_events = [event for event in events if isinstance(event, FrameCaptured)]
                if frame_events:
                    break
                await asyncio.sleep(0.001)
            frame_events = [event for event in events if isinstance(event, FrameCaptured)]
            self.assertTrue(frame_events)
            self.assertEqual(2.0, frame_events[-1].frame.captured_at)
            await host.shutdown()

        self.run_async(scenario())

    def test_failed_reload_keeps_old_plugin_registered_and_drops_frames_while_reloading(self):
        async def scenario():
            events = []
            start_gate = asyncio.Event()
            schedule = [None, start_gate]
            host = PluginHost((self.descriptor(events, startup_schedule=schedule),), reload_enabled=True)
            await host.startup()
            old_plugin = await host.get_plugin("recording-plugin")
            with patch("gripper_ai_controller.core.plugin_host.importlib.reload", side_effect=lambda module: module):
                reload_task = asyncio.create_task(host.reload(("recording-plugin",)))
                for _ in range(100):
                    if (await host.status("recording-plugin")).lifecycle_state == "reloading":
                        break
                    await asyncio.sleep(0.001)
                self.assertFalse(await host.publish_frame(self.frame("camera-a")))
                start_gate.set()
                await reload_task
            replacement = await host.get_plugin("recording-plugin")
            self.assertIsNot(old_plugin, replacement)
            self.assertIn("shutdown", events)
            await host.shutdown()

        self.run_async(scenario())

    def test_failed_replacement_preserves_old_plugin_and_continues_later_frame_delivery(self):
        async def scenario():
            events = []
            schedule = [None, RuntimeError("planned startup failure")]
            host = PluginHost((self.descriptor(events, startup_schedule=schedule),), reload_enabled=True)
            await host.startup()
            old_plugin = await host.get_plugin("recording-plugin")
            with patch("gripper_ai_controller.core.plugin_host.importlib.reload", side_effect=lambda module: module):
                with self.assertRaises(PluginReloadFailedError):
                    await host.reload(("recording-plugin",))
            self.assertIs(old_plugin, await host.get_plugin("recording-plugin"))
            self.assertTrue(await host.publish_frame(self.frame("camera-a")))
            status = await host.status("recording-plugin")
            self.assertEqual("running", status.lifecycle_state)
            self.assertIn("replacement failed", status.error)
            await host.shutdown()

        self.run_async(scenario())

    def test_reload_uses_runtime_plugin_overrides_for_latest_persisted_equivalent_settings(self):
        async def scenario():
            created_targets = []
            host = PluginHost(
                (
                    PluginFactoryDescriptor(
                        "target-retaining-plugin",
                        __name__,
                        "target_retaining_plugin_factory",
                        {"target_joint": "right_wrist", "created_targets": created_targets},
                    ),
                ),
                reload_enabled=True,
            )
            await host.startup()
            old_plugin = await host.get_plugin("target-retaining-plugin")
            old_plugin.target_joint = "left_wrist"
            with patch("gripper_ai_controller.core.plugin_host.importlib.reload", side_effect=lambda module: module):
                await host.reload(("target-retaining-plugin",))
            replacement = await host.get_plugin("target-retaining-plugin")
            self.assertEqual("left_wrist", replacement.target_joint)
            self.assertEqual(["right_wrist", "left_wrist"], created_targets)
            await host.shutdown()

        self.run_async(scenario())

    @staticmethod
    def frame(camera_id):
        """Return a minimal healthy RGB test frame with no external resource dependency."""

        return ImageFrame(camera_id, 1.0, None, None, True, b"\x00\x00\x00", 1, 1, "rgb8")


class VisualPoseAnalysisPluginTests(unittest.TestCase):
    """Verify the concrete plugin sees only typed frame events and passive services."""

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_matching_frames_reach_bounded_pose_and_analysis_services(self):
        async def scenario():
            tracker = FakePoseTracker()
            analysis = FakeVisionAnalysis()
            plugin = VisualPoseAnalysisPlugin("camera-a", tracker, analysis)
            await plugin.startup()
            matching_frame = PluginHostTests.frame("camera-a")
            retained_sources = []

            async def retain_source():
                """Record the preview-only diagnostic callback without retaining pixels."""

                retained_sources.append(matching_frame.captured_at)

            await plugin.handle_event(FrameCaptured(1.0, matching_frame, retain_source))
            await plugin.handle_event(FrameCaptured(1.0, PluginHostTests.frame("camera-b")))
            await plugin.handle_event(object())
            self.assertEqual([matching_frame], analysis.recorded_frames)
            self.assertEqual([matching_frame], tracker.submitted_frames)
            self.assertEqual([matching_frame.captured_at], retained_sources)
            self.assertEqual(1, len(tracker.inference_started_callbacks))
            self.assertEqual("visual-pose-analysis", plugin.ui_kind)
            await plugin.shutdown()
            self.assertTrue(tracker.shutdown_called)
            self.assertTrue(analysis.shutdown_called)

        self.run_async(scenario())

    def test_disabled_pose_rejects_target_updates_without_affecting_analysis(self):
        async def scenario():
            analysis = FakeVisionAnalysis()
            plugin = VisualPoseAnalysisPlugin("camera-a", None, analysis)
            with self.assertRaises(VisualPoseAnalysisCapabilityError):
                await plugin.set_target_joint("left_wrist")
            await plugin.shutdown()

        self.run_async(scenario())

    def test_visual_plugin_exposes_its_latest_pose_settings_for_a_hot_replacement(self):
        async def scenario():
            tracker = FakePoseTracker()
            tracker.settings = PoseTrackingSettings(enabled=True, target_joint="right_wrist")
            plugin = VisualPoseAnalysisPlugin("camera-a", tracker, FakeVisionAnalysis())
            await plugin.set_target_joint("left_wrist")
            tracker.settings = tracker.settings.with_target_joint("left_wrist")
            overrides = await plugin.reload_factory_kwargs()
            self.assertEqual("left_wrist", overrides["pose_settings"].target_joint)
            await plugin.shutdown()

        self.run_async(scenario())

    def test_factory_creates_disabled_plugin_without_loading_torch_or_hardware(self):
        plugin = build_visual_pose_analysis_plugin(
            "camera-a",
            PoseTrackingSettings(enabled=False),
            VisionAnalysisSettings(),
        )
        self.assertIsNone(plugin.pose_tracking_service)
        self.assertEqual("camera-a", plugin.camera_id)


def second_recording_plugin_factory(events):
    """Build a second uniquely named observer for event-isolation tests."""

    plugin = RecordingPlugin(events)
    plugin.manifest = ComponentManifest(
        "second-recording-plugin", "0.1.0", "plugin", ("frame-observer",)
    )
    return plugin


if __name__ == "__main__":
    unittest.main()
