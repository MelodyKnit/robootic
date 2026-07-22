"""Command-line entry points for safe development runtime execution and reload."""

import argparse
import asyncio
from dataclasses import replace

from gripper_ai_controller.bootstrap.runtime_builder import build_runtime


async def _run(args: argparse.Namespace) -> None:
    """Run one objective from an explicit JSON configuration and release resources."""

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

    runtime = build_runtime(args.config_file)
    await runtime.startup()
    try:
        modules = args.module or ["gripper_ai_controller.plugins"]
        await runtime.reload_modules(modules)
        print("Reloaded: {0}".format(", ".join(modules)))
    finally:
        await runtime.shutdown()


def _web(args: argparse.Namespace) -> None:
    """Run the read-only FastAPI camera preview without constructing the runtime."""

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
    settings = validate_web_settings(settings)
    preview_config.settings = settings
    application = create_web_app(preview_config, settings.frontend_dist_dir)
    uvicorn.run(application, host=settings.bind_host, port=settings.port)


def main() -> None:
    """Parse commands without creating a physical device connection."""

    parser = argparse.ArgumentParser(description="Run the gripper AI controller runtime.")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="Run one safe development objective.")
    run.add_argument("--config-file", default="configs/development.json")
    run.add_argument("--objective", default="Pick the detected workpiece")
    reload_command = subparsers.add_parser("reload", help="Explicitly reload development modules.")
    reload_command.add_argument("--config-file", default="configs/development.json")
    reload_command.add_argument("--module", action="append")
    web = subparsers.add_parser("web", help="Run the read-only camera preview web service.")
    web.add_argument("--config-file", default="configs/development.json")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    web.add_argument("--frontend-dist-dir")
    args = parser.parse_args()
    if args.command is None:
        args.command = "run"
        args.config_file = "configs/development.json"
        args.objective = "Pick the detected workpiece"
    if args.command == "reload":
        asyncio.run(_reload(args))
    elif args.command == "web":
        _web(args)
    else:
        asyncio.run(_run(args))
