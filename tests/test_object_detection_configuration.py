"""Configuration tests for allow-listed local object detection models."""

import json
import os
import tempfile
import unittest

from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config


class ObjectDetectionConfigurationTests(unittest.TestCase):
    """Keep model files local, explicit, and disabled by default."""

    def test_development_template_registers_disabled_detection_plugin_and_models(self):
        preview = load_vision_preview_config("configs/development.json")

        self.assertIn("object-detection-analysis", preview.preview_plugin_names)
        settings = preview.object_detection_settings
        self.assertFalse(settings.enabled)
        self.assertEqual("fasterrcnn-coco-local", settings.selected_model_id)
        self.assertEqual(2, len(settings.models))
        self.assertEqual(
            "yolo-world-onnx-opencv",
            settings.models[1].provider_id,
        )

    def test_rejects_model_paths_outside_localstore(self):
        payload = self._payload()
        payload["object_detection"]["models"][0]["model_path"] = "C" + ":/models/model.pth"

        with self.assertRaisesRegex(ValueError, "localstore"):
            self._load_temporary(payload)

    def test_rejects_yolo_world_without_exported_class_order(self):
        payload = self._payload()
        payload["object_detection"]["models"][1]["class_names"] = []

        with self.assertRaisesRegex(ValueError, "labels"):
            self._load_temporary(payload)

    def test_accepts_official_yolo_world_named_nms_output_format(self):
        payload = self._payload()
        payload["object_detection"]["models"][1]["output_format"] = "official-nms"

        preview = self._load_temporary(payload)

        self.assertEqual(
            "official-nms",
            preview.object_detection_settings.models[1].output_format,
        )

    def test_rejects_unknown_yolo_world_output_format(self):
        payload = self._payload()
        payload["object_detection"]["models"][1]["output_format"] = "guessed-layout"

        with self.assertRaisesRegex(ValueError, "output_format"):
            self._load_temporary(payload)

    @staticmethod
    def _payload():
        with open("configs/development.json", "r", encoding="utf-8") as source:
            return json.load(source)

    @staticmethod
    def _load_temporary(payload):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", encoding="utf-8", delete=False
        ) as output:
            json.dump(payload, output)
            path = output.name
        try:
            return load_vision_preview_config(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
