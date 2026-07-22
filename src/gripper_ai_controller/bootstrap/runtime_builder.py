"""Build an isolated runtime graph from one caller-supplied JSON configuration file."""

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from gripper_ai_controller.adapters.hikvision import HikvisionAdapter
from gripper_ai_controller.adapters.jaka import JakaAdapter
from gripper_ai_controller.adapters.simulation import (
    SimulatedCameraAdapter,
    SimulatedGripperAdapter,
    SimulatedRobotAdapter,
)
from gripper_ai_controller.configuration import SafetyLimits
from gripper_ai_controller.core.runtime import Runtime, RuntimeConfig
from gripper_ai_controller.core.targets import ExecutionTarget
from gripper_ai_controller.domain.models import CameraCalibration, CameraMounting, Pose3D, RuntimeMode, TargetRole
from gripper_ai_controller.plugins import AuditPlugin, DemonstrationPlannerPlugin, DeterministicPerceptionPlugin
from gripper_ai_controller.services.safety import SafetyPolicy


ROBOT_ADAPTERS = {"simulated-robot": SimulatedRobotAdapter, "jaka-robot": JakaAdapter}
GRIPPER_ADAPTERS = {"simulated-gripper": SimulatedGripperAdapter}
VISION_ADAPTERS = {"simulated-camera": SimulatedCameraAdapter, "hikvision-camera": HikvisionAdapter}
PERCEPTION_PLUGINS = {"deterministic-perception": DeterministicPerceptionPlugin}
PLANNER_PLUGINS = {"demonstration-planner": DemonstrationPlannerPlugin}
OBSERVER_PLUGINS = {"audit": AuditPlugin}


def load_runtime_config(config_file: str) -> RuntimeConfig:
    """Load JSON chosen by the caller and construct a validated development graph."""

    payload = _load_json(config_file)
    mode = _required_enum(payload, "runtime_mode", RuntimeMode)
    if mode == RuntimeMode.PRODUCTION:
        raise RuntimeError(
            "Production configuration is fail-closed until real project-local adapters are registered."
        )
    camera = _required_mapping(payload, "camera")
    mounting = _required_enum(camera, "mounting", CameraMounting)
    parent_frame = "robot_base" if mounting == CameraMounting.FIXED else "tool0"
    calibration = CameraCalibration(
        calibration_id=_required_string(camera, "calibration_id"),
        camera_id=_required_string(camera, "camera_id"),
        mounting=mounting,
        parent_frame=parent_frame,
        camera_to_parent=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, parent_frame),
    )
    components = _required_mapping(payload, "components")
    plugins = _required_mapping(components, "plugins")
    safety = payload.get("safety", {})
    if not isinstance(safety, dict):
        raise ValueError("safety must be a JSON object when present.")
    return RuntimeConfig(
        mode=mode,
        vision=_create(
            VISION_ADAPTERS,
            _required_string(components, "vision"),
            "vision adapter",
            _optional_mapping(components, "vision_adapter_settings"),
        ),
        calibration=calibration,
        perception_plugin=_create(
            PERCEPTION_PLUGINS, _required_string(plugins, "perception"), "perception plugin"
        ),
        planner_plugins=tuple(
            _create(PLANNER_PLUGINS, name, "planner plugin") for name in _required_list(plugins, "planners")
        ),
        observer_plugins=tuple(
            _create(OBSERVER_PLUGINS, name, "observer plugin") for name in _required_list(plugins, "observers")
        ),
        targets=tuple(_build_target(target) for target in _required_list(payload, "targets")),
        safety_policy=SafetyPolicy(_build_safety_limits(safety)),
    )


def build_runtime(config_file: str) -> Runtime:
    """Create a runtime that rereads the same explicit JSON file on development reload."""

    def rebuild() -> RuntimeConfig:
        """Construct fresh components after a safe lifecycle shutdown and module reload."""

        return load_runtime_config(config_file)

    return Runtime(rebuild(), rebuild)


def _build_target(settings: Any) -> ExecutionTarget:
    """Create one robot/gripper execution target from validated JSON settings."""

    if not isinstance(settings, dict):
        raise ValueError("Every targets entry must be a JSON object.")
    return ExecutionTarget(
        _required_string(settings, "name"),
        _required_enum(settings, "role", TargetRole),
        _create(
            ROBOT_ADAPTERS,
            _required_string(settings, "robot_adapter"),
            "robot adapter",
            _optional_mapping(settings, "robot_adapter_settings"),
        ),
        _create(GRIPPER_ADAPTERS, _required_string(settings, "gripper_adapter"), "gripper adapter"),
    )


def _build_safety_limits(settings: Dict[str, Any]) -> SafetyLimits:
    """Apply documented optional safety overrides to immutable project defaults."""

    allowed = {
        "minimum_force_percent",
        "maximum_force_percent",
        "maximum_position",
        "minimum_speed_percent",
        "maximum_speed_percent",
        "minimum_perception_confidence",
        "maximum_perception_age_seconds",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError("Unsupported safety settings: {0}".format(", ".join(sorted(unknown))))
    return SafetyLimits(**settings)


def _load_json(config_file: str) -> Dict[str, Any]:
    """Read a JSON object while reporting missing files and malformed JSON clearly."""

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
    if not isinstance(payload, dict):
        raise ValueError("Configuration root must be a JSON object.")
    return payload


def _create(
    factories: Dict[str, Any], name: str, component_type: str, settings: Dict[str, Any] = None
) -> Any:
    """Instantiate one known safe component by its configured identifier."""

    try:
        factory = factories[name]
    except KeyError:
        raise ValueError("Unknown {0}: {1}".format(component_type, name))
    return factory(**(settings or {}))


def _optional_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return an optional object field, defaulting to a fresh empty settings mapping."""

    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object when present.".format(key))
    return value


def _required_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return a required object field or raise a configuration-specific error."""

    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object.".format(key))
    return value


def _required_list(payload: Dict[str, Any], key: str) -> Iterable[Any]:
    """Return a required list field or raise a configuration-specific error."""

    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError("{0} must be a JSON array.".format(key))
    return value


def _required_string(payload: Dict[str, Any], key: str) -> str:
    """Return a required non-empty string identifier from one JSON object."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{0} must be a non-empty string.".format(key))
    return value


def _required_enum(payload: Dict[str, Any], key: str, enum_type: Any) -> Any:
    """Resolve a JSON string into a project enum and report allowed values clearly."""

    value = _required_string(payload, key)
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError("{0} must be one of: {1}.".format(key, allowed))
