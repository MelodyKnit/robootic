"""Configuration-boundary tests for the passive known-workpiece feature."""

import asyncio
import json
import os
import tempfile
import unittest

from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config
from gripper_ai_controller.web.app import _build_preview_plugin_host


class ObjectPoseConfigurationTests(unittest.TestCase):
    """Keep real local artifacts optional until the explicitly disabled feature is enabled."""

    def test_default_template_loads_without_local_background_or_calibration_assets(self):
        """The tracked template is safe to inspect on a machine with no localstore files."""

        preview = load_vision_preview_config("configs/object-pose-preview.example.json")

        settings = preview.workpiece_pose_settings
        self.assertFalse(settings.enabled)
        self.assertEqual("replace-with-local-calibration-id", settings.expected_calibration_id)
        self.assertEqual("known-workpiece-v1", settings.detector_settings.profile.profile_id)
        self.assertEqual("none", settings.detector_settings.profile.directional_feature)

    def test_rejects_a_directional_rule_outside_the_audited_profile_contract(self):
        """Unknown head-tail feature names must not silently become a permissive default."""

        with open("configs/object-pose-preview.example.json", "r", encoding="utf-8") as source:
            payload = json.load(source)
        payload["object_pose"]["profile"]["directional_feature"] = "unverified-feature"
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as output:
            json.dump(payload, output)
            config_path = output.name
        try:
            with self.assertRaisesRegex(ValueError, "directional_feature"):
                load_vision_preview_config(config_path)
        finally:
            os.unlink(config_path)

    def test_enabled_configuration_requires_measured_profile_geometry(self):
        """Live base-frame coordinates cannot start from only image-space shape thresholds."""

        with open("configs/object-pose-preview.example.json", "r", encoding="utf-8") as source:
            payload = json.load(source)
        payload["object_pose"].update(
            {
                "enabled": True,
                "background_reference_path": "localstore/object-pose/camera-a/empty-table.png",
                "workcell_calibration_path": "localstore/object-pose/camera-a/workcell.json",
                "expected_calibration_id": "calibration-a",
            }
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as output:
            json.dump(payload, output)
            config_path = output.name
        try:
            with self.assertRaisesRegex(ValueError, "measured at the workcell"):
                load_vision_preview_config(config_path)
        finally:
            os.unlink(config_path)

    def test_default_object_and_visual_plugins_coexist_and_reset_independently(self):
        """Adding the object observer does not replace the established visual observer."""

        async def scenario():
            preview = load_vision_preview_config("configs/object-pose-preview.example.json")
            host = _build_preview_plugin_host(preview)
            await host.startup()
            try:
                visual = await host.get_plugin("visual-pose-analysis")
                object_plugin = await host.get_plugin("object-pose-analysis")
                self.assertIsNotNone(visual)
                snapshot = await object_plugin.object_snapshot()
                self.assertFalse(snapshot.enabled)
                self.assertEqual("object_pose_disabled", snapshot.reason)
                await host.reset_frame_state()
                reset_snapshot = await object_plugin.object_snapshot()
                self.assertFalse(reset_snapshot.valid)
                self.assertEqual("object_pose_disabled", reset_snapshot.reason)
            finally:
                await host.shutdown()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
