"""Command-line entry points for safe development runtime execution and reload."""

import argparse
import asyncio
import json
import math
from pathlib import Path
from dataclasses import replace

from gripper_ai_controller.pose.estimator import (
    PoseEstimatorError,
    TorchvisionKeypointRcnnEstimator,
    download_keypoint_rcnn_weights,
)
from gripper_ai_controller.pose.gpu import inspect_cuda_gpu
from gripper_ai_controller.object_detection.models import DetectionProviderError
from gripper_ai_controller.object_detection.weights import install_faster_rcnn_coco_weights
from gripper_ai_controller.vision.analysis import JointVisibilityEvaluator
from gripper_ai_controller.vision.fixtures import (
    VisionFixtureManifestError,
    evaluation_report,
    evaluate_fixture,
    load_vision_fixtures,
    render_fixture_overlay,
)
from gripper_ai_controller.vision.quality import FrameQualityInspector


async def _run(args: argparse.Namespace) -> None:
    """Run one objective from an explicit JSON configuration and release resources."""

    from gripper_ai_controller.bootstrap.runtime_builder import build_runtime

    runtime = build_runtime(args.config_file)
    await runtime.startup()
    try:
        result = await runtime.run_objective(args.objective)
        command_name = "none" if result.command is None else result.command.payload.__class__.__name__
        print("command={0} approved={1} reports={2} reason={3}".format(
            command_name, result.decision.allowed, len(result.reports), result.decision.reason
        ))
    finally:
        await runtime.shutdown()


async def _reload(args: argparse.Namespace) -> None:
    """Reload a development runtime rebuilt from the same explicit JSON file."""

    from gripper_ai_controller.bootstrap.runtime_builder import build_runtime

    runtime = build_runtime(args.config_file)
    await runtime.startup()
    try:
        modules = args.module or ["gripper_ai_controller.plugins"]
        await runtime.reload_modules(modules)
        print("Reloaded: {0}".format(", ".join(modules)))
    finally:
        await runtime.shutdown()


async def _read_jaka_joint_positions(args: argparse.Namespace) -> None:
    """Connect only to one configured JAKA target and print its current joint angles."""

    from gripper_ai_controller.adapters.jaka import JakaAdapter
    from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config

    configuration = load_runtime_config(args.config_file)
    candidates = [target for target in configuration.targets if isinstance(target.robot, JakaAdapter)]
    if args.target is not None:
        candidates = [target for target in candidates if target.name == args.target]
        if not candidates:
            raise ValueError("No JAKA target named '{0}' exists in the supplied configuration.".format(args.target))
    if len(candidates) != 1:
        raise ValueError(
            "The supplied configuration must contain exactly one selected JAKA target; "
            "use --target when it contains multiple JAKA targets."
        )

    target = candidates[0]
    adapter = target.robot
    await adapter.startup()
    try:
        snapshot = await adapter.get_joint_positions()
    finally:
        await adapter.shutdown()

    values = snapshot.joint_positions_rad
    print(
        json.dumps(
            {
                "target_name": target.name,
                "captured_at": snapshot.captured_at,
                "joint_positions_rad": {
                    "J{0}".format(index + 1): value for index, value in enumerate(values)
                },
                "joint_positions_deg": {
                    "J{0}".format(index + 1): math.degrees(value)
                    for index, value in enumerate(values)
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _jaka_joint_dry_run(args: argparse.Namespace) -> None:
    """Compile and execute one offline JAKA joint command against in-memory state only."""

    from gripper_ai_controller.bootstrap.runtime_builder import load_jaka_dry_run_target
    from gripper_ai_controller.domain.models import JointMoveMode, RobotAction, RobotCommand

    target_name, adapter = load_jaka_dry_run_target(args.config_file, args.target)
    if args.stop:
        if args.mode is not None or args.speed_rad_per_second is not None:
            raise ValueError("--mode and --speed-rad-per-second cannot be used with --stop.")
        command = RobotCommand(action=RobotAction.STOP)
    else:
        if args.mode is None or args.speed_rad_per_second is None:
            raise ValueError(
                "--mode and --speed-rad-per-second are required with --joint-positions-rad."
            )
        try:
            joint_positions = json.loads(args.joint_positions_rad)
        except json.JSONDecodeError as error:
            raise ValueError("--joint-positions-rad must be a JSON array: {0}".format(error))
        if not isinstance(joint_positions, list):
            raise ValueError("--joint-positions-rad must be a JSON array.")
        command = RobotCommand(
            action=RobotAction.MOVE_JOINTS,
            joint_positions_rad=tuple(joint_positions),
            speed=args.speed_rad_per_second,
            joint_move_mode=JointMoveMode(args.mode),
        )

    await adapter.startup()
    try:
        await adapter.initialize()
        preview = adapter.preview(command)
        predicted_status = await adapter.execute(command)
    finally:
        await adapter.shutdown()

    print(
        json.dumps(
            {
                "target_name": target_name,
                "sent_to_hardware": False,
                "predicted_joint_positions_rad": list(predicted_status.joint_positions_rad),
                "sdk_preview": {
                    "method": preview.sdk_method,
                    "arguments": preview.sdk_arguments,
                    "source_joint_positions_rad": list(preview.source_joint_positions_rad),
                    "target_joint_positions_rad": list(preview.target_joint_positions_rad),
                    "joint_deltas_rad": list(preview.joint_deltas_rad),
                    "estimated_duration_seconds": preview.estimated_duration_seconds,
                },
                "message": "干运行完成，未向真实硬件发送任何命令。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _web(args: argparse.Namespace) -> None:
    """Run camera preview and optional local manual device controls without Runtime."""

    import uvicorn

    from gripper_ai_controller.bootstrap.preview_builder import load_vision_preview_config, validate_web_settings
    from gripper_ai_controller.web import create_web_app

    preview_config = load_vision_preview_config(args.config_file)
    settings = preview_config.settings
    if args.host is not None:
        settings = replace(settings, bind_host=args.host)
    if args.port is not None:
        if args.port < 1 or args.port > 65535:
            raise ValueError("--port must be an integer from 1 to 65535.")
        settings = replace(settings, port=args.port)
    if args.frontend_dist_dir is not None:
        settings = replace(settings, frontend_dist_dir=args.frontend_dist_dir)
    settings = validate_web_settings(settings, preview_config.runtime_mode)
    preview_config.settings = settings
    if args.reload:
        if preview_config.runtime_mode.value != "development":
            raise ValueError("--reload is available only when runtime_mode is development.")
        import os
        os.environ["GRIPPER_CONFIG_FILE"] = args.config_file
        uvicorn.run(
            "gripper_ai_controller.web.app:create_web_app_factory",
            factory=True,
            host=settings.bind_host,
            port=settings.port,
            reload=True,
            reload_dirs=["src/gripper_ai_controller"],
        )
    else:
        application = create_web_app(preview_config, settings.frontend_dist_dir)
        uvicorn.run(application, host=settings.bind_host, port=settings.port)


def _gpu_check(args: argparse.Namespace) -> None:
    """Print read-only CUDA evidence and fail only when the caller requires runtime readiness."""

    result = inspect_cuda_gpu()
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    if not result.cuda_driver_compatible:
        raise RuntimeError("CUDA 11.7-compatible NVIDIA driver is required before pose dependency installation.")
    if args.require_torch and not result.ready_for_pose_inference:
        raise RuntimeError("Pinned CUDA Torch runtime is not ready for pose inference.")


def _download_pose_weights(args: argparse.Namespace) -> None:
    """Download model weights only to an explicit localstore-relative path."""

    path = Path(args.weights_file)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
        raise ValueError("--weights-file must be a localstore-relative path without parent traversal.")
    try:
        saved_path = download_keypoint_rcnn_weights(args.weights_file)
    except PoseEstimatorError as error:
        raise RuntimeError(str(error))
    print("Downloaded Keypoint R-CNN weights: {0}".format(saved_path))


def _download_object_detection_weights(args: argparse.Namespace) -> None:
    """按显式 CLI 请求安装官方 Faster R-CNN 权重到 localstore。"""

    path = Path(args.weights_file)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "localstore":
        raise ValueError("--weights-file must be a localstore-relative path without parent traversal.")
    try:
        saved_path = install_faster_rcnn_coco_weights(args.weights_file)
    except DetectionProviderError as error:
        raise RuntimeError(str(error))
    print("Downloaded Faster R-CNN weights: {0}".format(saved_path))


def _vision_evaluate(args: argparse.Namespace) -> None:
    """Evaluate public local image fixtures without constructing a camera or control runtime."""

    from gripper_ai_controller.bootstrap.preview_builder import load_vision_evaluation_config

    configuration = load_vision_evaluation_config(args.config_file)
    if not configuration.pose_settings.enabled:
        raise RuntimeError("pose.enabled must be true for vision-evaluate.")
    preflight = inspect_cuda_gpu()
    if not preflight.ready_for_pose_inference:
        raise RuntimeError("CUDA pose inference is not ready: {0}".format(preflight.reason))
    try:
        fixtures = load_vision_fixtures(args.fixture_manifest)
    except VisionFixtureManifestError as error:
        raise RuntimeError(str(error))
    estimator = TorchvisionKeypointRcnnEstimator(
        configuration.pose_settings.weights_path,
        configuration.pose_settings.device,
        configuration.pose_settings.inference_max_side,
        configuration.pose_settings.torch_cpu_threads,
        configuration.pose_settings.torch_interop_threads,
    )
    quality_inspector = FrameQualityInspector(configuration.vision_analysis_settings)
    visibility_evaluator = JointVisibilityEvaluator(
        configuration.pose_settings.joint_confidence_threshold
    )
    evaluations = []
    for fixture in fixtures:
        for pixel_format in ("rgb8", "mono8"):
            try:
                evaluation = evaluate_fixture(
                    fixture,
                    pixel_format,
                    estimator,
                    quality_inspector,
                    visibility_evaluator,
                    configuration.pose_settings.person_confidence_threshold,
                )
            except (PoseEstimatorError, VisionFixtureManifestError) as error:
                raise RuntimeError("Fixture '{0}' could not be evaluated: {1}".format(fixture.fixture_id, error))
            evaluations.append(evaluation)

    report = evaluation_report(evaluations)
    report["model"] = configuration.pose_settings.model
    report["device"] = configuration.pose_settings.device
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_file is not None:
        report_path = _temporary_output_path(args.report_file, "--report-file")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text + "\n", encoding="utf-8")
    if args.save_overlays:
        overlay_directory = _temporary_output_path(args.overlay_dir, "--overlay-dir")
        overlay_directory.mkdir(parents=True, exist_ok=True)
        for evaluation in evaluations:
            fixture = next(item for item in fixtures if item.fixture_id == evaluation.fixture_id)
            render_fixture_overlay(
                fixture,
                evaluation,
                str(overlay_directory / "{0}-{1}.png".format(evaluation.fixture_id, evaluation.pixel_format)),
            )
    print(report_text)
    if not report["passed"]:
        raise RuntimeError("One or more vision fixtures did not meet their documented acceptance criteria.")


def _image_centering_simulate(args: argparse.Namespace) -> None:
    """Run a CUDA-backed, static-image visual-servo simulation without any hardware graph."""

    from gripper_ai_controller.image_servo_simulation.configuration import (
        load_image_servo_simulation_config,
    )
    from gripper_ai_controller.image_servo_simulation.runner import (
        ImageServoInputError,
        render_console_report,
        run_static_image_centering,
        select_image_target,
    )
    from gripper_ai_controller.vision.fixtures import (
        VisionFixtureManifestError,
        load_fixture_frame,
        load_vision_fixtures,
    )

    configuration = load_image_servo_simulation_config(args.config_file)
    preflight = inspect_cuda_gpu()
    if not preflight.ready_for_pose_inference:
        raise RuntimeError("CUDA pose inference is not ready: {0}".format(preflight.reason))
    try:
        fixtures = load_vision_fixtures(args.fixture_manifest)
    except VisionFixtureManifestError as error:
        raise RuntimeError(str(error))
    fixture = next(
        (item for item in fixtures if item.fixture_id == configuration.simulation_settings.fixture_id),
        None,
    )
    if fixture is None:
        raise RuntimeError(
            "The configured fixture_id was not found in the local vision fixture manifest."
        )
    frame = load_fixture_frame(fixture, configuration.simulation_settings.pixel_format)
    estimator = TorchvisionKeypointRcnnEstimator(
        configuration.pose_settings.weights_path,
        configuration.pose_settings.device,
        configuration.pose_settings.inference_max_side,
        configuration.pose_settings.torch_cpu_threads,
        configuration.pose_settings.torch_interop_threads,
    )
    try:
        target = select_image_target(
            estimator.infer(frame),
            frame.camera_id,
            frame.captured_at,
            configuration.pose_settings.target_joint,
            configuration.pose_settings.person_confidence_threshold,
            configuration.pose_settings.joint_confidence_threshold,
        )
    except (ImageServoInputError, PoseEstimatorError) as error:
        raise RuntimeError("Image-centering simulation cannot start: {0}".format(error))
    result = run_static_image_centering(configuration.simulation_settings, target)
    print(render_console_report(result))
    if not result.centered:
        raise RuntimeError("Image-centering simulation did not converge: {0}".format(result.reason))


def _temporary_output_path(value: str, argument_name: str) -> Path:
    """Restrict generated reports and overlays to the project temporary artifact area."""

    path = Path(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 2
        or path.parts[0] != "temp"
        or path.parts[1] != "gripper-ai-controller"
    ):
        raise ValueError(
            "{0} must be a relative path below temp/gripper-ai-controller without parent traversal.".format(
                argument_name
            )
        )
    return path


def main() -> None:
    """Parse commands without creating a physical device connection."""

    from gripper_ai_controller.calibration.cli import (
        add_calibration_commands,
        execute_calibration_command,
    )

    parser = argparse.ArgumentParser(description="Run the gripper AI controller runtime.")
    subparsers = parser.add_subparsers(dest="command")
    add_calibration_commands(subparsers)
    run = subparsers.add_parser("run", help="Run one safe development objective.")
    run.add_argument("--config-file", default="configs/development.json")
    run.add_argument("--objective", default="Pick the detected workpiece")
    reload_command = subparsers.add_parser("reload", help="Explicitly reload development modules.")
    reload_command.add_argument("--config-file", default="configs/development.json")
    reload_command.add_argument("--module", action="append")
    jaka_joints = subparsers.add_parser(
        "jaka-joints",
        help="Read current JAKA J1-J6 angles without enabling or moving the robot.",
    )
    jaka_joints.add_argument("--config-file", required=True)
    jaka_joints.add_argument("--target")
    jaka_dry_run = subparsers.add_parser(
        "jaka-joint-dry-run",
        help="Preview a JAKA joint command in memory without loading the SDK or controller.",
    )
    jaka_dry_run.add_argument("--config-file", required=True)
    jaka_dry_run.add_argument("--target")
    jaka_dry_run_command = jaka_dry_run.add_mutually_exclusive_group(required=True)
    jaka_dry_run_command.add_argument("--stop", action="store_true")
    jaka_dry_run_command.add_argument(
        "--joint-positions-rad",
        help="A JSON array containing six absolute targets or relative deltas in radians.",
    )
    jaka_dry_run.add_argument("--mode", choices=("absolute", "relative"))
    jaka_dry_run.add_argument("--speed-rad-per-second", type=float)
    web = subparsers.add_parser(
        "web",
        help="Run camera preview and optional local gripper or JAKA control without Runtime.",
    )
    web.add_argument("--config-file", default="configs/development.json")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    web.add_argument("--frontend-dist-dir")
    web.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload on code changes.")
    gpu_check = subparsers.add_parser("gpu-check", help="Inspect CUDA readiness without loading a pose model.")
    gpu_check.add_argument("--require-torch", action="store_true")
    weights = subparsers.add_parser(
        "pose-download-weights",
        help="Explicitly download official Keypoint R-CNN weights into localstore.",
    )
    weights.add_argument("--weights-file", required=True)
    detection_weights = subparsers.add_parser(
        "object-detection-download-fasterrcnn",
        help="Explicitly download official Faster R-CNN weights into localstore.",
    )
    detection_weights.add_argument(
        "--weights-file",
        default="localstore/models/fasterrcnn_resnet50_fpn_coco.pth",
    )
    evaluation = subparsers.add_parser(
        "vision-evaluate",
        help="Evaluate local public-image fixtures without opening a camera or control runtime.",
    )
    evaluation.add_argument("--config-file", required=True)
    evaluation.add_argument("--fixture-manifest", default="data/vision-fixtures/manifest.json")
    evaluation.add_argument("--report-file")
    evaluation.add_argument("--save-overlays", action="store_true")
    evaluation.add_argument(
        "--overlay-dir",
        default="temp/gripper-ai-controller/vision-evaluation/overlays",
    )
    image_centering = subparsers.add_parser(
        "image-centering-simulate",
        help="Run a static-image virtual camera-arm centering simulation without hardware.",
    )
    image_centering.add_argument("--config-file", required=True)
    image_centering.add_argument("--fixture-manifest", default="data/vision-fixtures/manifest.json")
    args = parser.parse_args()
    if args.command is None:
        args.command = "run"
        args.config_file = "configs/development.json"
        args.objective = "Pick the detected workpiece"
    if args.command.startswith("calibration-"):
        execute_calibration_command(args)
    elif args.command == "gpu-check":
        _gpu_check(args)
    elif args.command == "pose-download-weights":
        _download_pose_weights(args)
    elif args.command == "object-detection-download-fasterrcnn":
        _download_object_detection_weights(args)
    elif args.command == "vision-evaluate":
        _vision_evaluate(args)
    elif args.command == "image-centering-simulate":
        _image_centering_simulate(args)
    elif args.command == "jaka-joints":
        asyncio.run(_read_jaka_joint_positions(args))
    elif args.command == "jaka-joint-dry-run":
        asyncio.run(_jaka_joint_dry_run(args))
    elif args.command == "reload":
        asyncio.run(_reload(args))
    elif args.command == "web":
        _web(args)
    else:
        asyncio.run(_run(args))
