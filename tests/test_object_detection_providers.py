"""通用目标检测提供器的离线契约与输出解析测试。"""

import tempfile
import unittest
from pathlib import Path

import numpy

from gripper_ai_controller.domain.models import BoundingBox2D, ImageFrame
from gripper_ai_controller.object_detection import (
    DetectionCandidate,
    DetectionProviderError,
    TorchvisionFasterRcnnProvider,
    YoloWorldOnnxOpenCvProvider,
)


class FakeTensor:
    """提供 Torchvision 解析器需要的最小张量表面。"""

    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class FakeOpenCvNetwork:
    """记录 OpenCV DNN 调用并返回一个固定单类别输出。"""

    def __init__(self):
        self.backend = None
        self.target = None
        self.blob = None

    def setPreferableBackend(self, backend):
        self.backend = backend

    def setPreferableTarget(self, target):
        self.target = target

    def setInput(self, blob):
        self.blob = blob

    def getUnconnectedOutLayersNames(self):
        return ()

    def forward(self):
        # 32x32 letterbox: one center box and one configured class score.
        return numpy.asarray([[[16.0], [16.0], [16.0], [8.0], [0.90]]], dtype=numpy.float32)


class FakeOfficialNmsOpenCvNetwork(FakeOpenCvNetwork):
    """模拟官方导出图的四个命名 NMS 输出。"""

    def __init__(self):
        super().__init__()
        self.requested_output_names = None

    def getUnconnectedOutLayersNames(self):
        return ("num_dets", "boxes", "scores", "labels")

    def forward(self, output_names):
        self.requested_output_names = tuple(output_names)
        return (
            numpy.asarray([[1]], dtype=numpy.int32),
            numpy.asarray([[[8.0, 12.0, 24.0, 20.0]]], dtype=numpy.float32),
            numpy.asarray([[0.92]], dtype=numpy.float32),
            numpy.asarray([[0]], dtype=numpy.int32),
        )


class DetectionCandidateContractTests(unittest.TestCase):
    """确保任何提供器都只能发布有界的二维结果。"""

    def test_rejects_a_box_outside_the_normalized_image(self):
        with self.assertRaises(ValueError):
            DetectionCandidate("wrench", 0.9, BoundingBox2D(0.8, 0.2, 0.3, 0.4), 0)


class TorchvisionFasterRcnnProviderTests(unittest.TestCase):
    """不导入 Torch 即验证 COCO 结果映射和本地权重策略。"""

    def test_maps_scaled_coco_boxes_and_filters_configured_labels(self):
        prediction = {
            "boxes": FakeTensor([[10.0, 20.0, 60.0, 80.0], [5.0, 5.0, 20.0, 20.0]]),
            "scores": FakeTensor([0.91, 0.88]),
            "labels": FakeTensor([17, 1]),
        }

        detections = TorchvisionFasterRcnnProvider.map_prediction(
            prediction,
            source_width=200,
            source_height=200,
            scale_x=2.0,
            scale_y=2.0,
            confidence_threshold=0.50,
            allowed_labels=("cat",),
        )

        self.assertEqual(1, len(detections))
        detection = detections[0]
        self.assertEqual("cat", detection.label)
        self.assertEqual(17, detection.class_id)
        self.assertAlmostEqual(0.10, detection.bounding_box.x)
        self.assertAlmostEqual(0.20, detection.bounding_box.y)
        self.assertAlmostEqual(0.50, detection.bounding_box.width)
        self.assertAlmostEqual(0.60, detection.bounding_box.height)

    def test_rejects_inconsistent_prediction_arrays(self):
        prediction = {
            "boxes": FakeTensor([[0.0, 0.0, 10.0, 10.0]]),
            "scores": FakeTensor([0.9, 0.8]),
            "labels": FakeTensor([1]),
        }

        with self.assertRaises(DetectionProviderError):
            TorchvisionFasterRcnnProvider.map_prediction(prediction, 100, 100)

    def test_missing_local_weights_fail_only_when_inference_is_requested(self):
        provider = TorchvisionFasterRcnnProvider("localstore/models/missing-faster-rcnn.pth")
        frame = ImageFrame("camera-a", 1.0, None, None, True, b"\x00", 1, 1, "mono8")

        with self.assertRaises(DetectionProviderError):
            provider.infer(frame)


class YoloWorldOnnxOpenCvProviderTests(unittest.TestCase):
    """验证提示固化 ONNX 的输出布局、反 letterbox 和 NMS。"""

    def test_parses_ultralytics_output_and_suppresses_overlapping_same_class_boxes(self):
        output = numpy.asarray(
            [
                [
                    [320.0, 322.0],
                    [320.0, 322.0],
                    [200.0, 200.0],
                    [100.0, 100.0],
                    [0.90, 0.80],
                    [0.10, 0.20],
                ]
            ],
            dtype=numpy.float32,
        )

        detections = YoloWorldOnnxOpenCvProvider.parse_output(
            output,
            ("wrench", "screwdriver"),
            source_width=320,
            source_height=160,
            input_size=(640, 640),
            confidence_threshold=0.25,
            nms_iou_threshold=0.45,
        )

        self.assertEqual(1, len(detections))
        detection = detections[0]
        self.assertEqual("wrench", detection.label)
        self.assertEqual(0, detection.class_id)
        self.assertAlmostEqual(110.0 / 320.0, detection.bounding_box.x)
        self.assertAlmostEqual(55.0 / 160.0, detection.bounding_box.y)
        self.assertAlmostEqual(100.0 / 320.0, detection.bounding_box.width)
        self.assertAlmostEqual(50.0 / 160.0, detection.bounding_box.height)

    def test_parses_explicit_end2end_output_without_layout_guessing(self):
        output = numpy.asarray(
            [[[64.0, 64.0, 192.0, 192.0, 0.75, 1.0]]],
            dtype=numpy.float32,
        )

        detections = YoloWorldOnnxOpenCvProvider.parse_output(
            output,
            ("wrench", "pliers"),
            source_width=256,
            source_height=256,
            input_size=(256, 256),
            output_format="end2end",
        )

        self.assertEqual(1, len(detections))
        self.assertEqual("pliers", detections[0].label)
        self.assertAlmostEqual(0.25, detections[0].bounding_box.x)
        self.assertAlmostEqual(0.50, detections[0].bounding_box.width)

    def test_parses_official_named_nms_outputs_and_ignores_padded_rows(self):
        outputs = {
            "num_dets": numpy.asarray([[1]], dtype=numpy.int32),
            "boxes": numpy.asarray(
                [[[220.0, 270.0, 420.0, 370.0], [0.0, 0.0, 640.0, 640.0]]],
                dtype=numpy.float32,
            ),
            "scores": numpy.asarray([[0.90, 0.99]], dtype=numpy.float32),
            "labels": numpy.asarray([[0, 1]], dtype=numpy.int32),
        }

        detections = YoloWorldOnnxOpenCvProvider.parse_output(
            outputs,
            ("wrench", "pliers"),
            source_width=320,
            source_height=160,
            input_size=(640, 640),
            confidence_threshold=0.25,
            output_format="official-nms",
        )

        self.assertEqual(1, len(detections))
        detection = detections[0]
        self.assertEqual("wrench", detection.label)
        self.assertEqual(0, detection.class_id)
        self.assertAlmostEqual(110.0 / 320.0, detection.bounding_box.x)
        self.assertAlmostEqual(55.0 / 160.0, detection.bounding_box.y)
        self.assertAlmostEqual(100.0 / 320.0, detection.bounding_box.width)
        self.assertAlmostEqual(50.0 / 160.0, detection.bounding_box.height)

    def test_official_nms_infer_requests_and_maps_all_named_outputs(self):
        network = FakeOfficialNmsOpenCvNetwork()
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "yolo-world-official-nms.onnx"
            model_path.write_bytes(b"test-only-placeholder")
            provider = YoloWorldOnnxOpenCvProvider(
                str(model_path),
                ("wrench",),
                input_size=(32, 32),
                output_format="official-nms",
                network_factory=lambda path: network,
            )
            frame = ImageFrame(
                "camera-a",
                1.0,
                None,
                None,
                True,
                b"\x10\x80",
                2,
                1,
                "mono8",
            )

            detections = provider.infer(frame)

        self.assertEqual(
            ("num_dets", "boxes", "scores", "labels"),
            network.requested_output_names,
        )
        self.assertEqual(1, len(detections))
        self.assertEqual("wrench", detections[0].label)
        self.assertAlmostEqual(0.25, detections[0].bounding_box.x)
        self.assertAlmostEqual(0.25, detections[0].bounding_box.y)
        self.assertAlmostEqual(0.50, detections[0].bounding_box.width)
        self.assertAlmostEqual(0.50, detections[0].bounding_box.height)

    def test_official_nms_rejects_fractional_count_and_class_indexes(self):
        valid = {
            "num_dets": numpy.asarray([[1]], dtype=numpy.int32),
            "boxes": numpy.asarray([[[10.0, 10.0, 20.0, 20.0]]], dtype=numpy.float32),
            "scores": numpy.asarray([[0.9]], dtype=numpy.float32),
            "labels": numpy.asarray([[0]], dtype=numpy.int32),
        }
        for field_name, invalid_value in (
            ("num_dets", numpy.asarray([[0.5]], dtype=numpy.float32)),
            ("labels", numpy.asarray([[0.5]], dtype=numpy.float32)),
        ):
            with self.subTest(field_name=field_name):
                outputs = dict(valid)
                outputs[field_name] = invalid_value
                with self.assertRaises(DetectionProviderError):
                    YoloWorldOnnxOpenCvProvider.parse_output(
                        outputs,
                        ("wrench",),
                        32,
                        32,
                        input_size=(32, 32),
                        output_format="official-nms",
                    )

    def test_infer_accepts_mono8_and_uses_an_explicit_local_network_factory(self):
        network = FakeOpenCvNetwork()
        created_paths = []
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "yolo-world.onnx"
            model_path.write_bytes(b"test-only-placeholder")
            provider = YoloWorldOnnxOpenCvProvider(
                str(model_path),
                ("wrench",),
                input_size=(32, 32),
                network_factory=lambda path: created_paths.append(path) or network,
            )
            frame = ImageFrame(
                "camera-a",
                1.0,
                None,
                None,
                True,
                b"\x10\x80",
                2,
                1,
                "mono8",
            )

            detections = provider.infer(frame)

        self.assertEqual([str(model_path)], created_paths)
        self.assertIsNotNone(network.blob)
        self.assertEqual((1, 3, 32, 32), network.blob.shape)
        self.assertEqual(1, len(detections))
        self.assertEqual("wrench", detections[0].label)
        self.assertAlmostEqual(0.25, detections[0].bounding_box.x)
        self.assertAlmostEqual(0.25, detections[0].bounding_box.y)
        self.assertAlmostEqual(0.50, detections[0].bounding_box.width)
        self.assertAlmostEqual(0.50, detections[0].bounding_box.height)

    def test_class_names_must_match_the_exported_output_feature_count(self):
        output = numpy.zeros((1, 5, 10), dtype=numpy.float32)

        with self.assertRaises(DetectionProviderError):
            YoloWorldOnnxOpenCvProvider.parse_output(
                output,
                ("wrench", "pliers"),
                640,
                480,
            )


if __name__ == "__main__":
    unittest.main()
