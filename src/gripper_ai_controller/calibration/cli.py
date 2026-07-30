"""Explicit CLI workflows for the fixed-camera ChArUco calibration.

Most commands deliberately operate only on already captured local files.  The
explicit ``calibration-capture-charuco`` command is the single exception: it
constructs and starts only the configured vision adapter to save read-only
frames.  No calibration command constructs a JAKA controller, gripper,
runtime, or plugin, and none can issue a robot or gripper command.
"""

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence, Tuple

from PIL import Image

from gripper_ai_controller.calibration.camera import (
    MINIMUM_CHARUCO_VIEWS,
    CameraCalibrationInput,
    calibrate_charuco_camera,
    detect_charuco_observation,
    estimate_board_to_camera,
)
from gripper_ai_controller.calibration.charuco import generate_charuco_board_image
from gripper_ai_controller.calibration.errors import CalibrationValidationError
from gripper_ai_controller.calibration.models import (
    DEFAULT_CHARUCO_BOARD,
    CameraCalibrationResult,
    Point3D,
)
from gripper_ai_controller.calibration.opencv import require_opencv_aruco
from gripper_ai_controller.calibration.transform import fit_board_to_jaka_base
from gripper_ai_controller.calibration.workcell import WorkcellCalibration


_IMAGE_SUFFIXES = (".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff")
_MAXIMUM_ACCEPTED_REPROJECTION_ERROR_PX = 0.5
_MAXIMUM_ACCEPTED_BOARD_FIT_RMS_ERROR_MM = 1.0
_MAXIMUM_CHARUCO_CAPTURE_FRAMES = 500
_DEFAULT_CHARUCO_CAPTURE_INTERVAL_SECONDS = 2.0


def add_calibration_commands(subparsers: Any) -> None:
    """Register explicit calibration subcommands on the application's parser."""

    generate = subparsers.add_parser(
        "calibration-generate-charuco",
        help="离线生成指定规格的 ChArUco 标定板图，不连接任何设备。",
    )
    generate.add_argument(
        "--output-file",
        required=True,
        help="输出 PNG，只能位于 localstore/ 或 temp/gripper-ai-controller/。",
    )
    generate.add_argument("--image-width-px", type=int, default=2480)
    generate.add_argument("--image-height-px", type=int, default=1754)
    generate.add_argument("--margin-px", type=int, default=40)
    generate.add_argument("--border-bits", type=int, default=1)

    capture = subparsers.add_parser(
        "calibration-capture-charuco",
        help="显式启动唯一相机并只读采集至少 25 张 ChArUco PNG 到 localstore/。",
    )
    capture.add_argument(
        "--config-file",
        required=True,
        help="显式相机 JSON 配置；只读取 camera 和 components.vision 设置。",
    )
    capture.add_argument(
        "--output-dir",
        required=True,
        help="空的 localstore/ 子目录；采集帧按 charuco-0001.png 顺序写入。",
    )
    capture.add_argument(
        "--frame-count",
        required=True,
        type=int,
        help="要保存的帧数，至少为 25。",
    )
    capture.add_argument(
        "--capture-interval-seconds",
        type=float,
        default=_DEFAULT_CHARUCO_CAPTURE_INTERVAL_SECONDS,
        help="相邻两帧间给操作者移动标定板的等待时间，默认 2 秒。",
    )

    intrinsics = subparsers.add_parser(
        "calibration-camera-intrinsics",
        help="从 localstore 中已采集的 ChArUco 图像离线拟合相机内参。",
    )
    intrinsics.add_argument("--camera-id", required=True)
    intrinsics.add_argument("--calibration-id", required=True)
    intrinsics.add_argument(
        "--images-dir",
        required=True,
        help="包含至少 25 张不同角度、不同视场位置图像的 localstore/ 目录。",
    )
    intrinsics.add_argument(
        "--output-file",
        required=True,
        help="内参 JSON 输出，只能位于 localstore/。",
    )
    intrinsics.add_argument("--minimum-views", type=int, default=MINIMUM_CHARUCO_VIEWS)
    intrinsics.add_argument(
        "--maximum-reprojection-error-px",
        type=float,
        default=_MAXIMUM_ACCEPTED_REPROJECTION_ERROR_PX,
        help="验收上限，首期不得大于 0.5 px。",
    )

    workcell = subparsers.add_parser(
        "calibration-build-workcell",
        help="用已标定板图和操作者示教点离线建立 board 到 jaka_base 映射。",
    )
    workcell.add_argument(
        "--camera-calibration-file",
        required=True,
        help="由 calibration-camera-intrinsics 生成的 localstore/ JSON。",
    )
    workcell.add_argument(
        "--board-image-file",
        required=True,
        help="固定相机拍摄的完整 ChArUco 板图，只能位于 localstore/。",
    )
    workcell.add_argument(
        "--taught-points-file",
        required=True,
        help="操作者离线记录的 board/jaka_base 对应点 JSON，只能位于 localstore/。",
    )
    workcell.add_argument(
        "--output-file",
        required=True,
        help="完整 WorkcellCalibration JSON 输出，只能位于 localstore/。",
    )
    workcell.add_argument(
        "--maximum-reprojection-error-px",
        type=float,
        default=_MAXIMUM_ACCEPTED_REPROJECTION_ERROR_PX,
        help="接受现有内参的上限，首期不得大于 0.5 px。",
    )
    workcell.add_argument(
        "--maximum-fit-rms-error-mm",
        type=float,
        default=_MAXIMUM_ACCEPTED_BOARD_FIT_RMS_ERROR_MM,
        help="board 到 jaka_base 拟合 RMS 上限，首期不得大于 1 mm。",
    )


def execute_calibration_command(args: argparse.Namespace) -> None:
    """Execute exactly one registered calibration command with its declared boundary."""

    if args.command == "calibration-generate-charuco":
        _generate_charuco(args)
    elif args.command == "calibration-capture-charuco":
        asyncio.run(_capture_charuco_images(args))
    elif args.command == "calibration-camera-intrinsics":
        _calibrate_camera_intrinsics(args)
    elif args.command == "calibration-build-workcell":
        _build_workcell_calibration(args)
    else:
        raise ValueError("Unsupported calibration command: {0}".format(args.command))


async def _capture_charuco_images(args: argparse.Namespace) -> None:
    """Save a bounded, homogeneous read-only camera sequence as PNG files.

    This command intentionally bypasses the preview builder.  It only builds the
    requested vision adapter and never reads or constructs targets, controls,
    Runtime, or preview plugins.
    """

    frame_count = _capture_frame_count(args.frame_count)
    capture_interval_seconds = _capture_interval_seconds(args.capture_interval_seconds)
    output_directory = _localstore_path(args.output_dir, "--output-dir")
    _prepare_capture_output_directory(output_directory)
    configured_camera_id, vision = _build_capture_vision_adapter(args.config_file)
    operation_error = None
    expected_frame_contract = None
    pixel_formats = set()
    try:
        await vision.startup()
        for index in range(1, frame_count + 1):
            try:
                frame = await vision.capture()
            except Exception as error:
                raise RuntimeError(
                    "Unable to capture ChArUco frame {0} of {1}.".format(index, frame_count)
                ) from error
            image, pixel_format, frame_contract = _frame_as_png_image(
                frame,
                configured_camera_id,
                index,
            )
            if expected_frame_contract is None:
                expected_frame_contract = frame_contract
            elif frame_contract != expected_frame_contract:
                raise RuntimeError(
                    "Captured frame {0} does not match the first frame's camera ID, "
                    "dimensions, or pixel format; no mixed calibration set is allowed."
                    .format(index)
                )
            output_file = output_directory / "charuco-{0:04d}.png".format(index)
            try:
                image.save(str(output_file), format="PNG")
            except (OSError, ValueError) as error:
                raise RuntimeError(
                    "Could not write captured ChArUco frame: {0}".format(output_file)
                ) from error
            pixel_formats.add(pixel_format)
            if index < frame_count:
                await asyncio.sleep(capture_interval_seconds)
    except BaseException as error:
        operation_error = error
        raise
    finally:
        try:
            await vision.shutdown()
        except BaseException as error:
            if operation_error is None:
                raise RuntimeError("Could not shut down the read-only vision adapter.") from error

    width, height = expected_frame_contract[1:3]
    print(
        json.dumps(
            {
                "output_dir": str(output_directory),
                "camera_id": configured_camera_id,
                "captured_frame_count": frame_count,
                "image_size_px": {"width": width, "height": height},
                "pixel_formats": sorted(pixel_formats),
                "vision_adapter_started": True,
                "read_only_capture": True,
                "robot_or_gripper_connection": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _build_capture_vision_adapter(config_file: str) -> Tuple[str, Any]:
    """Build only the selected vision adapter from one explicit JSON configuration.

    This deliberately uses a small local parser instead of importing the broad
    runtime builder: the capture command must not even import the robot, gripper,
    Runtime, or plugin construction graph.  The explicit whitelist mirrors the
    currently registered vision adapters and imports only the selected factory.
    """

    payload = _load_capture_config(config_file)
    camera = _capture_required_mapping(payload, "camera")
    components = _capture_required_mapping(payload, "components")
    configured_camera_id = _capture_required_string(camera, "camera_id")
    vision_name = _capture_required_string(components, "vision")
    adapter_settings = _capture_optional_mapping(components, "vision_adapter_settings")
    if vision_name == "simulated-camera":
        from gripper_ai_controller.adapters.simulation.devices import SimulatedCameraAdapter

        adapter_factory = SimulatedCameraAdapter
    elif vision_name == "hikvision-camera":
        from gripper_ai_controller.adapters.hikvision.adapter import HikvisionAdapter

        adapter_factory = HikvisionAdapter
    else:
        raise ValueError("Unknown vision adapter: {0}".format(vision_name))
    return configured_camera_id, adapter_factory(**adapter_settings)


def _load_capture_config(config_file: str) -> Mapping[str, Any]:
    """Read an explicit JSON object without loading the runtime composition graph."""

    path = Path(config_file)
    if path.suffix.lower() != ".json":
        raise ValueError("Configuration files must use the JSON format.")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise FileNotFoundError("Configuration file was not found: {0}".format(config_file))
    except json.JSONDecodeError as error:
        raise ValueError("Configuration JSON is invalid: {0}".format(error))
    if not isinstance(payload, Mapping):
        raise ValueError("Configuration root must be a JSON object.")
    return payload


def _capture_required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return one mandatory capture configuration object with a focused error."""

    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError("{0} must be a JSON object.".format(key))
    return value


def _capture_optional_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return an optional capture configuration object without reading other sections."""

    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError("{0} must be a JSON object when present.".format(key))
    return value


def _capture_required_string(payload: Mapping[str, Any], key: str) -> str:
    """Return one non-empty capture identifier without coercing JSON values."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{0} must be a non-empty string.".format(key))
    return value


def _prepare_capture_output_directory(output_directory: Path) -> None:
    """Create one empty explicitly named localstore directory for a capture set."""

    if output_directory.exists():
        if not output_directory.is_dir():
            raise ValueError("--output-dir must be a directory path below localstore/.")
        if any(output_directory.iterdir()):
            raise ValueError(
                "--output-dir must be empty so it represents one unambiguous calibration capture set."
            )
        return
    try:
        output_directory.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise RuntimeError(
            "Could not create local ChArUco capture directory: {0}".format(output_directory)
        ) from error


def _capture_frame_count(value: Any) -> int:
    """Require at least the documented number of independent calibration frames."""

    if (
        type(value) is not int
        or value < MINIMUM_CHARUCO_VIEWS
        or value > _MAXIMUM_CHARUCO_CAPTURE_FRAMES
    ):
        raise ValueError(
            "--frame-count must be an integer from {0} through {1}.".format(
                MINIMUM_CHARUCO_VIEWS,
                _MAXIMUM_CHARUCO_CAPTURE_FRAMES,
            )
        )
    return value


def _capture_interval_seconds(value: Any) -> float:
    """Keep each collection slow enough for deliberate board repositioning."""

    if type(value) not in (int, float):
        raise ValueError("--capture-interval-seconds must be a finite number from 0.5 through 60.")
    interval = float(value)
    if not math.isfinite(interval) or interval < 0.5 or interval > 60.0:
        raise ValueError("--capture-interval-seconds must be a finite number from 0.5 through 60.")
    return interval


def _frame_as_png_image(
    frame: Any,
    configured_camera_id: str,
    index: int,
) -> Tuple[Image.Image, str, Tuple[str, int, int, str]]:
    """Validate one normalized RGB8 or Mono8 frame before local PNG persistence."""

    camera_id = getattr(frame, "camera_id", None)
    if not isinstance(camera_id, str) or not camera_id:
        raise RuntimeError("Captured frame {0} does not declare a valid camera ID.".format(index))
    if camera_id != configured_camera_id:
        raise RuntimeError(
            "Captured frame {0} camera ID '{1}' does not match config camera_id '{2}'."
            .format(index, camera_id, configured_camera_id)
        )
    if getattr(frame, "healthy", None) is not True:
        raise RuntimeError("Captured frame {0} is marked unhealthy.".format(index))
    width = getattr(frame, "width", None)
    height = getattr(frame, "height", None)
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise RuntimeError(
            "Captured frame {0} must declare positive integer width and height.".format(index)
        )
    pixel_format = getattr(frame, "pixel_format", None)
    if not isinstance(pixel_format, str):
        raise RuntimeError(
            "Captured frame {0} must use RGB8 or Mono8 pixel format.".format(index)
        )
    normalized_format = pixel_format.lower()
    if normalized_format == "rgb8":
        image_mode = "RGB"
        bytes_per_pixel = 3
    elif normalized_format == "mono8":
        image_mode = "L"
        bytes_per_pixel = 1
    else:
        raise RuntimeError(
            "Captured frame {0} uses unsupported pixel format '{1}'; expected RGB8 or Mono8."
            .format(index, pixel_format)
        )
    payload = getattr(frame, "pixel_payload", None)
    if not isinstance(payload, (bytes, bytearray)):
        raise RuntimeError("Captured frame {0} has no byte pixel payload.".format(index))
    expected_length = width * height * bytes_per_pixel
    if len(payload) != expected_length:
        raise RuntimeError(
            "Captured frame {0} payload length {1} does not match {2} {3} pixels."
            .format(index, len(payload), expected_length, pixel_format)
        )
    try:
        image = Image.frombytes(image_mode, (width, height), bytes(payload))
    except (TypeError, ValueError) as error:
        raise RuntimeError("Captured frame {0} could not be converted to PNG pixels.".format(index)) from error
    return image, normalized_format, (camera_id, width, height, normalized_format)


def _generate_charuco(args: argparse.Namespace) -> None:
    """Render the fixed physical board into an explicitly permitted output path."""

    output_path = _calibration_artifact_path(args.output_file, "--output-file")
    if output_path.suffix.lower() != ".png":
        raise ValueError("--output-file must use the .png suffix for a ChArUco board image.")
    board_image = generate_charuco_board_image(
        DEFAULT_CHARUCO_BOARD,
        args.image_width_px,
        args.image_height_px,
        margin_px=args.margin_px,
        border_bits=args.border_bits,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_image).save(str(output_path), format="PNG")
    print(
        json.dumps(
            {
                "output_file": str(output_path),
                "charuco_board": DEFAULT_CHARUCO_BOARD.to_dict(),
                "image_size_px": {
                    "width": args.image_width_px,
                    "height": args.image_height_px,
                },
                "hardware_connection": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _calibrate_camera_intrinsics(args: argparse.Namespace) -> None:
    """Detect local ChArUco views and persist only an accepted intrinsic record."""

    minimum_views = _minimum_views(args.minimum_views)
    maximum_error = _reprojection_error_limit(args.maximum_reprojection_error_px)
    image_directory = _localstore_path(args.images_dir, "--images-dir")
    output_path = _localstore_path(args.output_file, "--output-file")
    image_width, image_height, observations, inspected_count = _load_charuco_observations(
        image_directory,
        DEFAULT_CHARUCO_BOARD,
    )
    result = calibrate_charuco_camera(
        CameraCalibrationInput(
            calibration_id=args.calibration_id,
            camera_id=args.camera_id,
            board_spec=DEFAULT_CHARUCO_BOARD,
            image_width_px=image_width,
            image_height_px=image_height,
            observations=observations,
            minimum_views=minimum_views,
        )
    )
    if result.reprojection_error_px > maximum_error:
        raise RuntimeError(
            "Camera reprojection error {0:.4f} px exceeds the accepted limit {1:.4f} px; "
            "no calibration file was written.".format(result.reprojection_error_px, maximum_error)
        )
    _write_json(output_path, result.to_dict())
    print(
        json.dumps(
            {
                "output_file": str(output_path),
                "camera_id": result.camera_id,
                "calibration_id": result.calibration_id,
                "images_inspected": inspected_count,
                "valid_charuco_views": result.views_used,
                "reprojection_error_px": result.reprojection_error_px,
                "hardware_connection": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _build_workcell_calibration(args: argparse.Namespace) -> None:
    """Compose the camera and taught-point evidence into one local workcell record."""

    maximum_reprojection_error = _reprojection_error_limit(args.maximum_reprojection_error_px)
    maximum_fit_error = _board_fit_error_limit(args.maximum_fit_rms_error_mm)
    camera_calibration_path = _localstore_path(
        args.camera_calibration_file,
        "--camera-calibration-file",
    )
    board_image_path = _localstore_path(args.board_image_file, "--board-image-file")
    taught_points_path = _localstore_path(args.taught_points_file, "--taught-points-file")
    output_path = _localstore_path(args.output_file, "--output-file")

    camera_calibration = CameraCalibrationResult.from_dict(
        _read_json_object(camera_calibration_path, "--camera-calibration-file")
    )
    if camera_calibration.reprojection_error_px > maximum_reprojection_error:
        raise RuntimeError(
            "Stored camera reprojection error {0:.4f} px exceeds the accepted limit {1:.4f} px."
            .format(camera_calibration.reprojection_error_px, maximum_reprojection_error)
        )
    board_image = _read_calibration_image(board_image_path, "--board-image-file")
    image_height, image_width = board_image.shape[:2]
    intrinsics = camera_calibration.intrinsics
    if image_width != intrinsics.image_width_px or image_height != intrinsics.image_height_px:
        raise CalibrationValidationError(
            "The board image dimensions must exactly match the camera calibration image dimensions."
        )
    observation = detect_charuco_observation(board_image, camera_calibration.board_spec)
    if observation is None:
        raise CalibrationValidationError(
            "The supplied board image does not contain enough detectable ChArUco corners."
        )
    board_to_camera = estimate_board_to_camera(
        observation,
        camera_calibration.board_spec,
        intrinsics,
    )
    board_points, jaka_base_points = _load_taught_correspondences(taught_points_path)
    fit = fit_board_to_jaka_base(board_points, jaka_base_points)
    if fit.rms_error_mm > maximum_fit_error:
        raise RuntimeError(
            "board -> jaka_base RMS error {0:.4f} mm exceeds the accepted limit {1:.4f} mm; "
            "no workcell calibration file was written.".format(fit.rms_error_mm, maximum_fit_error)
        )
    workcell = WorkcellCalibration(
        calibration_id=camera_calibration.calibration_id,
        camera_id=camera_calibration.camera_id,
        camera_calibration=camera_calibration,
        board_to_camera=board_to_camera,
        board_to_jaka_base_fit=fit,
    )
    _write_json(output_path, workcell.to_dict())
    print(
        json.dumps(
            {
                "output_file": str(output_path),
                "camera_id": workcell.camera_id,
                "calibration_id": workcell.calibration_id,
                "reference_point_count": fit.point_count,
                "board_to_jaka_base_rms_error_mm": fit.rms_error_mm,
                "board_to_jaka_base_maximum_error_mm": fit.maximum_error_mm,
                "hardware_connection": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_charuco_observations(
    image_directory: Path,
    board_spec: Any,
) -> Tuple[int, int, Tuple[Any, ...], int]:
    """Read one local image directory and retain only valid, same-size ChArUco views."""

    if not image_directory.is_dir():
        raise ValueError("--images-dir must refer to an existing localstore/ directory.")
    image_paths = tuple(
        path
        for path in sorted(image_directory.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )
    if not image_paths:
        raise ValueError("--images-dir does not contain any supported calibration image files.")
    expected_size = None
    observations = []
    for image_path in image_paths:
        image = _read_calibration_image(image_path, "--images-dir")
        height, width = image.shape[:2]
        if expected_size is None:
            expected_size = (width, height)
        elif expected_size != (width, height):
            raise CalibrationValidationError(
                "All calibration images must have exactly the same pixel dimensions."
            )
        observation = detect_charuco_observation(image, board_spec)
        if observation is not None:
            observations.append(observation)
    if expected_size is None:
        raise ValueError("--images-dir does not contain readable calibration images.")
    return expected_size[0], expected_size[1], tuple(observations), len(image_paths)


def _read_calibration_image(path: Path, argument_name: str) -> Any:
    """Read one local image through OpenCV after checking its allowed suffix."""

    if not path.is_file():
        raise ValueError("{0} must refer to an existing image file.".format(argument_name))
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        raise ValueError("{0} must use a supported image suffix: {1}.".format(
            argument_name,
            ", ".join(_IMAGE_SUFFIXES),
        ))
    cv2 = require_opencv_aruco()
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("{0} could not be decoded as an image: {1}".format(argument_name, path))
    return image


def _load_taught_correspondences(path: Path) -> Tuple[Tuple[Point3D, ...], Tuple[Point3D, ...]]:
    """Parse ordered board-to-JAKA-base reference pairs recorded by the operator."""

    document = _read_json_object(path, "--taught-points-file")
    correspondences = document.get("correspondences")
    if not isinstance(correspondences, Sequence) or isinstance(correspondences, (str, bytes)):
        raise CalibrationValidationError("taught-points JSON must contain a correspondences array.")
    board_points = []
    jaka_base_points = []
    for index, correspondence in enumerate(correspondences):
        if not isinstance(correspondence, Mapping):
            raise CalibrationValidationError(
                "correspondences[{0}] must be a JSON object.".format(index)
            )
        board_point = Point3D.from_dict(correspondence.get("board_point_mm"))
        if abs(board_point.z_mm) > 1e-6:
            raise CalibrationValidationError(
                "correspondences[{0}].board_point_mm.z_mm must be 0 for the board plane.".format(index)
            )
        jaka_base_point = Point3D.from_dict(correspondence.get("jaka_base_point_mm"))
        board_points.append(board_point)
        jaka_base_points.append(jaka_base_point)
    return tuple(board_points), tuple(jaka_base_points)


def _minimum_views(value: Any) -> int:
    """Keep the physical workflow at or above its documented 25-view minimum."""

    if type(value) is not int or value < MINIMUM_CHARUCO_VIEWS:
        raise ValueError(
            "--minimum-views must be an integer of at least {0}.".format(MINIMUM_CHARUCO_VIEWS)
        )
    return value


def _reprojection_error_limit(value: Any) -> float:
    """Reject attempts to accept a weaker-than-specified camera calibration."""

    return _bounded_positive_number(
        value,
        "--maximum-reprojection-error-px",
        _MAXIMUM_ACCEPTED_REPROJECTION_ERROR_PX,
    )


def _board_fit_error_limit(value: Any) -> float:
    """Reject attempts to accept a weaker-than-specified board/base fit."""

    return _bounded_positive_number(
        value,
        "--maximum-fit-rms-error-mm",
        _MAXIMUM_ACCEPTED_BOARD_FIT_RMS_ERROR_MM,
    )


def _bounded_positive_number(value: Any, argument_name: str, maximum: float) -> float:
    """Validate a finite positive operator acceptance limit with a safety ceiling."""

    if type(value) not in (int, float):
        raise ValueError("{0} must be a positive finite number.".format(argument_name))
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0 or normalized > maximum:
        raise ValueError(
            "{0} must be greater than zero and no greater than {1}.".format(argument_name, maximum)
        )
    return normalized


def _localstore_path(value: str, argument_name: str) -> Path:
    """Accept only explicit localstore-relative inputs and outputs."""

    return _restricted_relative_path(value, argument_name, ("localstore",))


def _calibration_artifact_path(value: str, argument_name: str) -> Path:
    """Allow generated printable boards in localstore or project-owned temporary storage."""

    return _restricted_relative_path(
        value,
        argument_name,
        ("localstore", "temp/gripper-ai-controller"),
    )


def _restricted_relative_path(value: str, argument_name: str, allowed_roots: Tuple[str, ...]) -> Path:
    """Guard local CLI file paths against absolute paths and parent traversal."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("{0} must be a non-empty relative path.".format(argument_name))
    path = Path(value)
    normalized = path.as_posix()
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("{0} must not be absolute or contain parent traversal.".format(argument_name))
    if not any(normalized == root or normalized.startswith(root + "/") for root in allowed_roots):
        raise ValueError(
            "{0} must be below one of: {1}.".format(argument_name, ", ".join(allowed_roots))
        )
    if normalized in allowed_roots:
        raise ValueError("{0} must name a file or directory below its allowed root.".format(argument_name))
    return path


def _read_json_object(path: Path, argument_name: str) -> Mapping[str, Any]:
    """Read one local JSON document with command-line-quality error messages."""

    if not path.is_file():
        raise ValueError("{0} must refer to an existing JSON file.".format(argument_name))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("{0} could not be read as UTF-8 JSON.".format(argument_name)) from error
    if not isinstance(value, Mapping):
        raise CalibrationValidationError("{0} must contain a JSON object.".format(argument_name))
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Persist a validated local calibration artifact only after all checks pass."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise RuntimeError("Could not write calibration output file: {0}".format(path)) from error
