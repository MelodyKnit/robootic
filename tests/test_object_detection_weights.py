"""Tests for explicit generic detection weight installation."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gripper_ai_controller.object_detection.models import DetectionProviderError
from gripper_ai_controller.object_detection.weights import install_faster_rcnn_coco_weights


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class ObjectDetectionWeightTests(unittest.TestCase):
    """Verify successful downloads, hash rejection, and existing-file reuse."""

    @staticmethod
    def _localstore_root(directory: str) -> Path:
        root = Path(directory) / "localstore"
        root.mkdir()
        return root

    def test_installs_verified_payload_atomically(self):
        payload = b"verified-faster-rcnn"
        with tempfile.TemporaryDirectory() as directory:
            root = self._localstore_root(directory)
            target = root / "models" / "model.pth"
            with patch(
                "gripper_ai_controller.object_detection.weights.FASTER_RCNN_COCO_SHA256",
                hashlib.sha256(payload).hexdigest(),
            ), patch(
                "gripper_ai_controller.object_detection.weights._localstore_root",
                return_value=root.resolve(),
            ):
                result = install_faster_rcnn_coco_weights(
                    "localstore/models/model.pth", opener=lambda *_args, **_kwargs: _Response(payload)
                )
            self.assertEqual(str(Path("localstore") / "models" / "model.pth"), result)
            self.assertEqual(payload, target.read_bytes())
            self.assertEqual([], list(target.parent.glob("*.tmp")))

    def test_rejects_unverified_payload_without_replacing_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._localstore_root(directory)
            target = root / "model.pth"
            target.write_bytes(b"existing")
            with patch(
                "gripper_ai_controller.object_detection.weights._localstore_root",
                return_value=root.resolve(),
            ):
                with self.assertRaisesRegex(DetectionProviderError, "SHA-256"):
                    install_faster_rcnn_coco_weights(
                        "localstore/model.pth",
                        opener=lambda *_args, **_kwargs: _Response(b"invalid"),
                    )
            self.assertEqual(b"existing", target.read_bytes())

    def test_reuses_an_existing_verified_file_without_network(self):
        payload = b"already-present"
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = self._localstore_root(directory)
            target = root / "model.pth"
            target.write_bytes(payload)
            with patch(
                "gripper_ai_controller.object_detection.weights.FASTER_RCNN_COCO_SHA256",
                hashlib.sha256(payload).hexdigest(),
            ), patch(
                "gripper_ai_controller.object_detection.weights._localstore_root",
                return_value=root.resolve(),
            ):
                install_faster_rcnn_coco_weights(
                    "localstore/model.pth", opener=lambda *_args, **_kwargs: calls.append(True)
                )
            self.assertEqual([], calls)

    def test_rejects_paths_outside_localstore_before_opening_network(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            absolute_path = str(Path(directory) / "model.pth")
        for destination in (
            absolute_path,
            "localstore/../escape.pth",
            "data/model.pth",
            "localstore",
        ):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(DetectionProviderError, "localstore-relative"):
                    install_faster_rcnn_coco_weights(
                        destination,
                        opener=lambda *_args, **_kwargs: calls.append(True),
                    )
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
