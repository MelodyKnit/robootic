"""Build an isolated runtime graph from one caller-supplied JSON configuration file."""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from gripper_ai_controller.adapters.hikvision import HikvisionAdapter
from gripper_ai_controller.adapters.jaka import JakaAdapter, JakaDryRunRobotAdapter
from gripper_ai_controller.adapters.pgi import PgiTcpGripperAdapter
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


ROBOT_ADAPTERS = {
    "simulated-robot": SimulatedRobotAdapter,
    "jaka-robot": JakaAdapter,
    "jaka-dry-run-robot": JakaDryRunRobotAdapter,
}
GRIPPER_ADAPTERS = {
    "simulated-gripper": SimulatedGripperAdapter,
    "pgi-tcp-gripper": PgiTcpGripperAdapter,
}
VISION_ADAPTERS = {"simulated-camera": SimulatedCameraAdapter, "hikvision-camera": HikvisionAdapter}
PERCEPTION_PLUGINS = {"deterministic-perception": DeterministicPerceptionPlugin}
PLANNER_PLUGINS = {"demonstration-planner": DemonstrationPlannerPlugin}
OBSERVER_PLUGINS = {"audit": AuditPlugin}


def load_runtime_config(config_file: str) -> RuntimeConfig:
    """Load JSON chosen by the caller and construct a validated development graph."""

    payload = load_json_config(config_file)
    mode = _required_enum(payload, "runtime_mode", RuntimeMode)
    if mode == RuntimeMode.PRODUCTION:
        raise RuntimeError(
            "Production configuration is fail-closed until real project-local adapters are registered."
        )
    camera = required_mapping(payload, "camera")
    mounting = _required_enum(camera, "mounting", CameraMounting)
    parent_frame = "robot_base" if mounting == CameraMounting.FIXED else "tool0"
    calibration = CameraCalibration(
        calibration_id=required_string(camera, "calibration_id"),
        camera_id=required_string(camera, "camera_id"),
        mounting=mounting,
        parent_frame=parent_frame,
        camera_to_parent=Pose3D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, parent_frame),
    )
    components = required_mapping(payload, "components")
    plugins = required_mapping(components, "plugins")
    safety = payload.get("safety", {})
    if not isinstance(safety, dict):
        raise ValueError("safety must be a JSON object when present.")
    return RuntimeConfig(
        mode=mode,
        vision=create_component(
            VISION_ADAPTERS,
            required_string(components, "vision"),
            "vision adapter",
            optional_mapping(components, "vision_adapter_settings"),
        ),
        calibration=calibration,
        perception_plugin=create_component(
            PERCEPTION_PLUGINS, required_string(plugins, "perception"), "perception plugin"
        ),
        planner_plugins=tuple(
            create_component(PLANNER_PLUGINS, name, "planner plugin") for name in _required_list(plugins, "planners")
        ),
        observer_plugins=tuple(
            create_component(OBSERVER_PLUGINS, name, "observer plugin") for name in _required_list(plugins, "observers")
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
    robot = create_component(
        ROBOT_ADAPTERS,
        required_string(settings, "robot_adapter"),
        "robot adapter",
        optional_mapping(settings, "robot_adapter_settings"),
    )
    return ExecutionTarget(
        name=required_string(settings, "name"),
        role=_required_enum(settings, "role", TargetRole),
        robot=robot,
        gripper=create_component(
            GRIPPER_ADAPTERS,
            required_string(settings, "gripper_adapter"),
            "gripper adapter",
            optional_mapping(settings, "gripper_adapter_settings"),
        ),
        # JAKA adapters expose this pure validator; ordinary adapters retain generic safety only.
        robot_motion_constraint=getattr(robot, "motion_constraint", None),
    )


def load_jaka_dry_run_target(
    config_file: str, target_name: Optional[str] = None
) -> Tuple[str, JakaDryRunRobotAdapter]:
    """Load exactly one configured offline JAKA target without building the runtime graph.

    This deliberately reads only the selected target's robot adapter settings. It does
    not instantiate vision, gripper, planner, mirror, or physical JAKA components, so
    the command-line dry run cannot connect to a controller as an indirect side effect.
    """

    payload = load_json_config(config_file)
    candidates = []
    for target in _required_list(payload, "targets"):
        if not isinstance(target, dict):
            raise ValueError("Every targets entry must be a JSON object.")
        configured_name = required_string(target, "name")
        adapter_name = required_string(target, "robot_adapter")
        if adapter_name != "jaka-dry-run-robot":
            continue
        if target_name is None or target_name == configured_name:
            candidates.append(target)

    if target_name is not None and not candidates:
        raise ValueError(
            "No JAKA dry-run target named '{0}' exists in the supplied configuration.".format(
                target_name
            )
        )
    if len(candidates) != 1:
        raise ValueError(
            "The supplied configuration must contain exactly one selected JAKA dry-run target; "
            "use --target when it contains multiple dry-run targets."
        )

    selected = candidates[0]
    return (
        required_string(selected, "name"),
        JakaDryRunRobotAdapter(**optional_mapping(selected, "robot_adapter_settings")),
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


def _resolve_config_references(
    value: Any, base_dir: Path, visited: Optional[set] = None
) -> Any:
    """递归解析配置中的@引用，支持嵌套和循环检测。

    Args:
        value: 配置值（可能包含@引用）
        base_dir: 基准目录，用于解析相对路径
        visited: 已访问的文件集合，用于循环检测

    Returns:
        解析后的配置值

    引用语法：
        - 字符串以"@"开头 → 加载外部JSON文件
        - 适配器引用返回adapter_type字符串（保持向后兼容）
        - 插件引用返回plugin_type字符串
        - 普通字符串 → 保持不变（适配器/插件类型名）
        - 对象/列表 → 递归处理其中的值
    """
    if visited is None:
        visited = set()

    # 字符串：检查是否为引用
    if isinstance(value, str):
        if value.startswith("@"):
            ref_path = value[1:]  # 去掉@前缀
            full_path = base_dir / ref_path

            # 循环引用检测
            abs_path = full_path.resolve()
            if abs_path in visited:
                raise ValueError(f"检测到循环引用: {abs_path}")

            # 加载引用的文件
            if not full_path.exists():
                raise FileNotFoundError(f"引用的配置文件不存在: {ref_path} (完整路径: {full_path})")

            visited.add(abs_path)
            try:
                with full_path.open("r", encoding="utf-8") as handle:
                    referenced_config = json.load(handle)

                # 如果引用的文件包含adapter_type，返回类型名（向后兼容）
                if isinstance(referenced_config, dict) and "adapter_type" in referenced_config:
                    return referenced_config["adapter_type"]

                # 如果引用的文件包含plugin_type，返回类型名（向后兼容）
                if isinstance(referenced_config, dict) and "plugin_type" in referenced_config:
                    return referenced_config["plugin_type"]

                # 否则递归解析引用文件中的内容
                return _resolve_config_references(referenced_config, base_dir, visited)
            finally:
                visited.discard(abs_path)
        else:
            # 普通字符串，不是引用
            return value

    # 字典：递归处理所有值
    elif isinstance(value, dict):
        return {k: _resolve_config_references(v, base_dir, visited) for k, v in value.items()}

    # 列表：递归处理所有元素
    elif isinstance(value, list):
        return [_resolve_config_references(item, base_dir, visited) for item in value]

    # 其他类型（数字、布尔等）：直接返回
    else:
        return value


def load_json_config(config_file: str) -> Dict[str, Any]:
    """Read a JSON object while reporting missing files and malformed JSON clearly.

    支持@引用语法：
        - "@adapters/simulated-camera.json" → 加载configs/adapters/simulated-camera.json
        - 普通字符串保持不变
        - 递归解析嵌套引用
        - 自动检测循环引用
    """

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

    # 解析引用（基于配置文件所在目录）
    base_dir = path.parent
    resolved_payload = _resolve_config_references(payload, base_dir)

    return resolved_payload


def create_component(
    factories: Dict[str, Any], name: str, component_type: str, settings: Dict[str, Any] = None
) -> Any:
    """Instantiate one known safe component by its configured identifier."""

    try:
        factory = factories[name]
    except KeyError:
        raise ValueError("Unknown {0}: {1}".format(component_type, name))
    return factory(**(settings or {}))


def optional_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return an optional object field, defaulting to a fresh empty settings mapping."""

    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object when present.".format(key))
    return value


def required_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
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


def required_string(payload: Dict[str, Any], key: str) -> str:
    """Return a required non-empty string identifier from one JSON object."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{0} must be a non-empty string.".format(key))
    return value


def _required_enum(payload: Dict[str, Any], key: str, enum_type: Any) -> Any:
    """Resolve a JSON string into a project enum and report allowed values clearly."""

    value = required_string(payload, key)
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError("{0} must be one of: {1}.".format(key, allowed))
