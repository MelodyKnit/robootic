"""Offline contract tests for the explicit ChArUco calibration CLI."""

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy
from PIL import Image

from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config
from gripper_ai_controller.calibration import (
    DEFAULT_CHARUCO_BOARD,
    CameraCalibrationResult,
    CameraIntrinsics,
    CharucoObservation,
    PixelPoint,
    Point3D,
    RigidTransform,
    RigidTransformFitResult,
)
from gripper_ai_controller.calibration.cli import (
    _build_capture_vision_adapter,
    _calibration_artifact_path,
    _load_taught_correspondences,
    _localstore_path,
    add_calibration_commands,
    execute_calibration_command,
)
from gripper_ai_controller.calibration.errors import CalibrationValidationError
from gripper_ai_controller.domain.models import ImageFrame


def camera_calibration(reprojection_error_px=0.25):
    """Create a valid in-memory intrinsic artifact for CLI composition tests."""

    return CameraCalibrationResult(
        "calibration-a",
        "camera-a",
        DEFAULT_CHARUCO_BOARD,
        CameraIntrinsics(640, 480, 1000.0, 1000.0, 320.0, 240.0),
        reprojection_error_px,
        25,
    )


def board_to_camera():
    """Return a fixed board-to-camera transform for offline command orchestration tests."""

    return RigidTransform(
        "board",
        "camera",
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        Point3D(0.0, 0.0, 1000.0),
    )


def board_fit(rms_error_mm=0.4):
    """Return fit evidence with a JAKA-base target for offline command tests."""

    return RigidTransformFitResult(
        RigidTransform(
            "board",
            "jaka_base",
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            Point3D(100.0, 200.0, 20.0),
        ),
        4,
        rms_error_mm,
        rms_error_mm + 0.1,
    )


class FakeVisionAdapter:
    """Minimal asynchronous camera used to prove capture never reaches real hardware."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.startup_calls = 0
        self.capture_calls = 0
        self.shutdown_calls = 0

    async def startup(self):
        self.startup_calls += 1

    async def capture(self):
        self.capture_calls += 1
        frame = self.frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame

    async def shutdown(self):
        self.shutdown_calls += 1


def captured_frame(pixel_format="rgb8", payload=None):
    """Build one normalized 2 x 1 fake camera frame without a device connection."""

    if payload is None:
        payload = b"\x10\x20" if pixel_format == "mono8" else b"\x10\x20\x30\x40\x50\x60"
    return ImageFrame(
        camera_id="camera-a",
        captured_at=123.0,
        frame_reference="test://frame",
        calibration_id="calibration-a",
        healthy=True,
        pixel_payload=payload,
        width=2,
        height=1,
        pixel_format=pixel_format,
    )


async def _skip_capture_interval(_seconds):
    """Keep fake-camera tests deterministic while production keeps the operator interval."""

    return None


class CalibrationCliPathTests(unittest.TestCase):
    """Ensure calibration files stay within the documented mutable-storage boundary."""

    def test_localstore_paths_reject_absolute_parent_and_other_project_locations(self):
        self.assertEqual(Path("localstore/object-pose/camera.json"), _localstore_path(
            "localstore/object-pose/camera.json", "--output-file"
        ))
        for invalid in (
            "C" + ":" + "\\outside.json",
            "localstore/../outside.json",
            "configs/calibration.json",
            "localstore",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    _localstore_path(invalid, "--output-file")

    def test_printable_board_allows_only_localstore_or_project_temp(self):
        self.assertEqual(
            Path("temp/gripper-ai-controller/charuco/board.png"),
            _calibration_artifact_path(
                "temp/gripper-ai-controller/charuco/board.png", "--output-file"
            ),
        )
        self.assertEqual(
            Path("localstore/object-pose/board.png"),
            _calibration_artifact_path("localstore/object-pose/board.png", "--output-file"),
        )
        with self.assertRaises(ValueError):
            _calibration_artifact_path("temp/board.png", "--output-file")


class TaughtPointContractTests(unittest.TestCase):
    """Check that the operator's offline point record cannot silently lose pairing semantics."""

    def test_loads_ordered_planar_correspondences(self):
        payload = {
            "correspondences": [
                {
                    "board_point_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                    "jaka_base_point_mm": {"x_mm": 100.0, "y_mm": 200.0, "z_mm": 20.0},
                },
                {
                    "board_point_mm": {"x_mm": 100.0, "y_mm": 0.0, "z_mm": 0.0},
                    "jaka_base_point_mm": {"x_mm": 200.0, "y_mm": 200.0, "z_mm": 20.0},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "taught-points.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            board_points, base_points = _load_taught_correspondences(path)

        self.assertEqual((Point3D(0.0, 0.0, 0.0), Point3D(100.0, 0.0, 0.0)), board_points)
        self.assertEqual(Point3D(100.0, 200.0, 20.0), base_points[0])

    def test_rejects_a_non_planar_board_reference_point(self):
        payload = {
            "correspondences": [
                {
                    "board_point_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.1},
                    "jaka_base_point_mm": {"x_mm": 100.0, "y_mm": 200.0, "z_mm": 20.0},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "taught-points.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CalibrationValidationError):
                _load_taught_correspondences(path)


class CalibrationCommandTests(unittest.TestCase):
    """Verify command dispatch stays file-only and writes only accepted results."""

    def test_registers_all_explicit_calibration_commands(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_calibration_commands(subparsers)

        generate = parser.parse_args(
            [
                "calibration-generate-charuco",
                "--output-file",
                "temp/gripper-ai-controller/charuco/board.png",
            ]
        )
        capture = parser.parse_args(
            [
                "calibration-capture-charuco",
                "--config-file",
                "localstore/object-pose/camera-a/camera.local.json",
                "--output-dir",
                "localstore/object-pose/camera-a/charuco-intrinsics",
            "--frame-count",
            "25",
            ]
        )
        intrinsics = parser.parse_args(
            [
                "calibration-camera-intrinsics",
                "--camera-id",
                "camera-a",
                "--calibration-id",
                "calibration-a",
                "--images-dir",
                "localstore/object-pose/camera-a/images",
                "--output-file",
                "localstore/object-pose/camera-a/camera.json",
            ]
        )
        workcell = parser.parse_args(
            [
                "calibration-build-workcell",
                "--camera-calibration-file",
                "localstore/object-pose/camera-a/camera.json",
                "--board-image-file",
                "localstore/object-pose/camera-a/board.png",
                "--taught-points-file",
                "localstore/object-pose/camera-a/points.json",
                "--output-file",
                "localstore/object-pose/camera-a/workcell.json",
            ]
        )

        self.assertEqual("calibration-generate-charuco", generate.command)
        self.assertEqual("calibration-capture-charuco", capture.command)
        self.assertEqual(25, capture.frame_count)
        self.assertEqual(2.0, capture.capture_interval_seconds)
        self.assertEqual("calibration-camera-intrinsics", intrinsics.command)
        self.assertEqual("calibration-build-workcell", workcell.command)

    def test_disabled_versioned_object_pose_template_loads_without_local_artifacts(self):
        """A fresh checkout must not need calibration images or a device to read its template."""

        preview = load_vision_preview_config("configs/object-pose-preview.example.json")

        self.assertFalse(preview.workpiece_pose_settings.enabled)
        self.assertEqual(
            "localstore/object-pose/replace-with-local-camera-id/empty-table.png",
            preview.workpiece_pose_settings.background_reference_path,
        )
        self.assertEqual(
            "known-workpiece-v1",
            preview.workpiece_pose_settings.detector_settings.profile.profile_id,
        )

    def test_generate_command_writes_only_its_explicit_png_artifact(self):
        args = argparse.Namespace(
            command="calibration-generate-charuco",
            output_file="temp/gripper-ai-controller/calibration-cli-test/board.png",
            image_width_px=640,
            image_height_px=480,
            margin_px=20,
            border_bits=1,
        )
        image_writer = Mock()
        with patch(
            "gripper_ai_controller.calibration.cli.generate_charuco_board_image",
            return_value=numpy.zeros((480, 640), dtype=numpy.uint8),
        ) as generate, patch(
            "gripper_ai_controller.calibration.cli.Image.fromarray",
            return_value=image_writer,
        ):
            execute_calibration_command(args)

        generate.assert_called_once()
        image_writer.save.assert_called_once_with(
            "temp\\gripper-ai-controller\\calibration-cli-test\\board.png",
            format="PNG",
        )

    def test_intrinsics_command_does_not_write_an_error_exceeding_result(self):
        args = argparse.Namespace(
            command="calibration-camera-intrinsics",
            camera_id="camera-a",
            calibration_id="calibration-a",
            images_dir="localstore/object-pose/camera-a/images",
            output_file="localstore/object-pose/camera-a/camera.json",
            minimum_views=25,
            maximum_reprojection_error_px=0.5,
        )
        observations = tuple(
            CharucoObservation(
                (
                    PixelPoint(10.0, 10.0),
                    PixelPoint(20.0, 10.0),
                    PixelPoint(10.0, 20.0),
                    PixelPoint(20.0, 20.0),
                ),
                (0, 1, 2, 3),
            )
            for _ in range(25)
        )
        with patch(
            "gripper_ai_controller.calibration.cli._load_charuco_observations",
            return_value=(640, 480, observations, 25),
        ), patch(
            "gripper_ai_controller.calibration.cli.calibrate_charuco_camera",
            return_value=camera_calibration(reprojection_error_px=0.51),
        ), patch("gripper_ai_controller.calibration.cli._write_json") as write_json:
            with self.assertRaisesRegex(RuntimeError, "no calibration file was written"):
                execute_calibration_command(args)

        write_json.assert_not_called()

    def test_workcell_command_writes_complete_record_only_after_all_offline_evidence_passes(self):
        args = argparse.Namespace(
            command="calibration-build-workcell",
            camera_calibration_file="localstore/object-pose/camera-a/camera.json",
            board_image_file="localstore/object-pose/camera-a/fixed-board.png",
            taught_points_file="localstore/object-pose/camera-a/taught-points.json",
            output_file="localstore/object-pose/camera-a/workcell.json",
            maximum_reprojection_error_px=0.5,
            maximum_fit_rms_error_mm=1.0,
        )
        points = (
            Point3D(0.0, 0.0, 0.0),
            Point3D(100.0, 0.0, 0.0),
            Point3D(0.0, 100.0, 0.0),
            Point3D(100.0, 100.0, 0.0),
        )
        with patch(
            "gripper_ai_controller.calibration.cli._read_json_object",
            return_value=camera_calibration().to_dict(),
        ), patch(
            "gripper_ai_controller.calibration.cli._read_calibration_image",
            return_value=numpy.zeros((480, 640), dtype=numpy.uint8),
        ), patch(
            "gripper_ai_controller.calibration.cli.detect_charuco_observation",
            return_value=Mock(),
        ), patch(
            "gripper_ai_controller.calibration.cli.estimate_board_to_camera",
            return_value=board_to_camera(),
        ), patch(
            "gripper_ai_controller.calibration.cli._load_taught_correspondences",
            return_value=(points, points),
        ), patch(
            "gripper_ai_controller.calibration.cli.fit_board_to_jaka_base",
            return_value=board_fit(),
        ), patch("gripper_ai_controller.calibration.cli._write_json") as write_json:
            execute_calibration_command(args)

        write_json.assert_called_once()
        saved_record = write_json.call_args[0][1]
        self.assertEqual("camera-a", saved_record["camera_id"])
        self.assertEqual(0.4, saved_record["board_to_jaka_base_fit"]["rms_error_mm"])

    def test_calibration_cli_import_does_not_load_hardware_or_runtime_composition(self):
        probe = (
            "import sys; import gripper_ai_controller.calibration.cli; "
            "blocked = [name for name in sys.modules if 'runtime_builder' in name "
            "or 'adapters.jaka' in name or 'adapters.hikvision' in name]; "
            "raise SystemExit(1 if blocked else 0)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


class CharucoCaptureCommandTests(unittest.TestCase):
    """Verify the one explicit camera command is read-only and always closes its adapter."""

    @staticmethod
    def _args(frame_count=25):
        return argparse.Namespace(
            command="calibration-capture-charuco",
            config_file="localstore/object-pose/camera-a/camera.local.json",
            output_dir="localstore/object-pose/camera-a/charuco-intrinsics",
            frame_count=frame_count,
            capture_interval_seconds=2.0,
        )

    def test_capture_writes_rgb_and_mono_png_sequences_using_only_the_fake_adapter(self):
        """Both normalized camera formats become inspectable PNGs with no SDK involvement."""

        for pixel_format, expected_mode in (("rgb8", "RGB"), ("mono8", "L")):
            with self.subTest(pixel_format=pixel_format), tempfile.TemporaryDirectory() as directory:
                output_directory = Path(directory) / "capture"
                adapter = FakeVisionAdapter([captured_frame(pixel_format) for _ in range(25)])
                with patch(
                    "gripper_ai_controller.calibration.cli._localstore_path",
                    return_value=output_directory,
                ), patch(
                    "gripper_ai_controller.calibration.cli._build_capture_vision_adapter",
                    return_value=("camera-a", adapter),
                ), patch(
                    "gripper_ai_controller.calibration.cli.asyncio.sleep",
                    new=_skip_capture_interval,
                ), redirect_stdout(StringIO()):
                    execute_calibration_command(self._args())

                saved_files = sorted(output_directory.glob("charuco-*.png"))
                self.assertEqual(25, len(saved_files))
                with Image.open(str(saved_files[0])) as saved_image:
                    self.assertEqual((2, 1), saved_image.size)
                    self.assertEqual(expected_mode, saved_image.mode)
                self.assertEqual(1, adapter.startup_calls)
                self.assertEqual(25, adapter.capture_calls)
                self.assertEqual(1, adapter.shutdown_calls)

    def test_capture_rejects_bad_frame_and_still_shuts_down_the_fake_adapter(self):
        """Malformed pixels cannot become calibration evidence, even after startup succeeds."""

        adapter = FakeVisionAdapter([captured_frame(payload=b"\x00")])
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "capture"
            with patch(
                "gripper_ai_controller.calibration.cli._localstore_path",
                return_value=output_directory,
            ), patch(
                "gripper_ai_controller.calibration.cli._build_capture_vision_adapter",
                return_value=("camera-a", adapter),
            ), patch(
                "gripper_ai_controller.calibration.cli.asyncio.sleep",
                new=_skip_capture_interval,
            ), self.assertRaisesRegex(RuntimeError, "payload length"):
                execute_calibration_command(self._args())

            self.assertTrue(output_directory.is_dir())
            self.assertEqual([], list(output_directory.iterdir()))
        self.assertEqual(1, adapter.startup_calls)
        self.assertEqual(1, adapter.capture_calls)
        self.assertEqual(1, adapter.shutdown_calls)

    def test_capture_rejects_less_than_twenty_five_frames_before_constructing_an_adapter(self):
        """The command cannot start an incomplete site calibration collection."""

        with patch(
            "gripper_ai_controller.calibration.cli._build_capture_vision_adapter"
        ) as build_adapter:
            with self.assertRaisesRegex(ValueError, "--frame-count"):
                execute_calibration_command(self._args(frame_count=24))

        build_adapter.assert_not_called()

    def test_capture_rejects_an_unsafe_collection_interval_before_constructing_an_adapter(self):
        """A rapid burst cannot masquerade as a 25-view operator collection."""

        with patch(
            "gripper_ai_controller.calibration.cli._build_capture_vision_adapter"
        ) as build_adapter:
            args = self._args()
            args.capture_interval_seconds = 0.49
            with self.assertRaisesRegex(ValueError, "capture-interval-seconds"):
                execute_calibration_command(args)

        build_adapter.assert_not_called()

    def test_capture_builder_reads_only_vision_fields_and_ignores_targets(self):
        """An invalid target cannot be constructed as an indirect capture side effect."""

        payload = {
            "camera": {"camera_id": "sim-camera"},
            "components": {"vision": "simulated-camera"},
            "targets": [
                {
                    "name": "must-not-be-built",
                    "robot_adapter": "unknown-robot",
                    "gripper_adapter": "unknown-gripper",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "capture.json"
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            configured_camera_id, adapter = _build_capture_vision_adapter(str(config_file))

        self.assertEqual("sim-camera", configured_camera_id)
        self.assertEqual("simulated-camera", adapter.manifest.name)
        self.assertFalse(adapter.started)

    def test_capture_builder_does_not_import_control_or_runtime_modules(self):
        """The local capture parser retains its isolation after it builds a camera."""

        payload = {
            "camera": {"camera_id": "sim-camera"},
            "components": {"vision": "simulated-camera"},
        }
        probe = (
            "import sys; "
            "from gripper_ai_controller.calibration.cli import _build_capture_vision_adapter; "
            "_build_capture_vision_adapter(sys.argv[1]); "
            "blocked = [name for name in sys.modules if name in ("
            "'gripper_ai_controller.bootstrap.runtime_builder', "
            "'gripper_ai_controller.adapters.jaka', "
            "'gripper_ai_controller.adapters.pgi', "
            "'gripper_ai_controller.core.runtime', "
            "'gripper_ai_controller.plugins')]; "
            "raise SystemExit(1 if blocked else 0)"
        )
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "capture.json"
            config_file.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-c", probe, str(config_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
