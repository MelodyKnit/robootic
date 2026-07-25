"""Runtime composition entry points with lazy imports for offline configuration helpers."""


def build_runtime(config_file):
    """Create a runtime only when a caller explicitly requests runtime construction."""

    from gripper_ai_controller.bootstrap.runtime_builder import build_runtime as runtime_builder

    return runtime_builder(config_file)


def load_runtime_config(config_file):
    """Load a runtime graph only when a caller explicitly requests runtime configuration."""

    from gripper_ai_controller.bootstrap.runtime_builder import load_runtime_config as runtime_config_loader

    return runtime_config_loader(config_file)


__all__ = ["build_runtime", "load_runtime_config"]
