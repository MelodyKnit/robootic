"""Strict JSON configuration loader for the offline image-centering simulation."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

from gripper_ai_controller.bootstrap.pose_settings import build_pose_tracking_settings
from gripper_ai_controller.configuration import PoseTrackingSettings
from gripper_ai_controller.image_servo_simulation.models import (
    ImageServoSimulationSettings,
    SIMULATED_JOINT_NAMES,
)


@dataclass(frozen=True)
class ImageServoSimulationConfig:
    """Configuration needed by the CUDA estimator and pure virtual image-plane model."""

    pose_settings: PoseTrackingSettings
    simulation_settings: ImageServoSimulationSettings


def _load_json_config(config_file: str) -> Dict[str, Any]:
    """Read one caller-selected JSON object without importing runtime configuration code."""

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


def _required_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return a required JSON object without accepting arrays or scalar coercion."""

    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError("{0} must be a JSON object.".format(key))
    return value


def load_image_servo_simulation_config(config_file: str) -> ImageServoSimulationConfig:
    """Load a hardware-free image-centering configuration from one explicit JSON file."""

    payload = _load_json_config(config_file)
    unknown = set(payload).difference({"pose", "simulation"})
    if unknown:
        raise ValueError(
            "Unsupported image-centering simulation root settings: {0}.".format(
                ", ".join(sorted(unknown))
            )
        )
    pose_settings = build_pose_tracking_settings(_required_mapping(payload, "pose"))
    if not pose_settings.enabled:
        raise ValueError("pose.enabled must be true for image-centering simulation.")
    if pose_settings.weights_path is None:
        raise ValueError("pose.weights_path is required for image-centering simulation.")
    return ImageServoSimulationConfig(
        pose_settings,
        _build_simulation_settings(_required_mapping(payload, "simulation")),
    )


def _build_simulation_settings(settings: Dict[str, Any]) -> ImageServoSimulationSettings:
    """Validate a compact virtual-arm model without accepting device connection fields."""

    allowed = {
        "fixture_id",
        "pixel_format",
        "desired_normalized",
        "center_deadband",
        "maximum_pose_age_seconds",
        "maximum_joint_step_rad",
        "gain",
        "damping",
        "maximum_iterations",
        "initial_joint_positions_rad",
        "joint_lower_limits_rad",
        "joint_upper_limits_rad",
        "image_jacobian",
    }
    unknown = set(settings).difference(allowed)
    if unknown:
        raise ValueError(
            "Unsupported simulation settings: {0}.".format(", ".join(sorted(unknown)))
        )
    fixture_id = _string(settings, "fixture_id", "full-body-front")
    pixel_format = _string(settings, "pixel_format", "mono8")
    if pixel_format not in ("rgb8", "mono8"):
        raise ValueError("simulation.pixel_format must be rgb8 or mono8.")
    desired = _number_vector(settings, "desired_normalized", (0.5, 0.5), 2, 0.0, 1.0)
    lower = _number_vector(
        settings,
        "joint_lower_limits_rad",
        (-2.8, -1.5, -2.0, -2.8, -2.0, -3.1),
        len(SIMULATED_JOINT_NAMES),
        -6.3,
        0.0,
    )
    upper = _number_vector(
        settings,
        "joint_upper_limits_rad",
        (2.8, 1.5, 2.0, 2.8, 2.0, 3.1),
        len(SIMULATED_JOINT_NAMES),
        0.0,
        6.3,
    )
    if any(lower[index] >= upper[index] for index in range(len(lower))):
        raise ValueError("Every simulation joint lower limit must be smaller than its upper limit.")
    initial = _number_vector(
        settings,
        "initial_joint_positions_rad",
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        len(SIMULATED_JOINT_NAMES),
        -6.3,
        6.3,
    )
    if any(initial[index] < lower[index] or initial[index] > upper[index] for index in range(len(initial))):
        raise ValueError("Every initial virtual joint position must lie within its configured limits.")
    jacobian = _jacobian(settings)
    if all(math.isclose(value, 0.0, abs_tol=1e-12) for row in jacobian for value in row):
        raise ValueError("simulation.image_jacobian must contain at least one non-zero value.")
    return ImageServoSimulationSettings(
        fixture_id=fixture_id,
        pixel_format=pixel_format,
        desired_normalized_x=desired[0],
        desired_normalized_y=desired[1],
        center_deadband=_number(settings, "center_deadband", 0.025, 0.0, 0.49),
        maximum_pose_age_seconds=_number(settings, "maximum_pose_age_seconds", 1.0, 0.01, 30.0),
        maximum_joint_step_rad=_number(settings, "maximum_joint_step_rad", 0.08, 0.0001, 1.0),
        gain=_number(settings, "gain", 0.85, 0.01, 2.0),
        damping=_number(settings, "damping", 0.10, 0.0001, 10.0),
        maximum_iterations=_integer(settings, "maximum_iterations", 20, 1, 200),
        initial_joint_positions_rad=initial,
        joint_lower_limits_rad=lower,
        joint_upper_limits_rad=upper,
        image_jacobian=jacobian,
    )


def _jacobian(settings: Dict[str, Any]) -> Tuple[Tuple[float, ...], ...]:
    """Validate exactly two image rows and six virtual-joint columns."""

    default = (
        (-0.52, 0.0, 0.0, -0.10, 0.0, 0.0),
        (0.0, -0.42, -0.18, 0.0, -0.08, 0.0),
    )
    if "image_jacobian" not in settings:
        return default
    value = settings["image_jacobian"]
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("simulation.image_jacobian must contain exactly two rows.")
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != len(SIMULATED_JOINT_NAMES):
            raise ValueError("Every simulation.image_jacobian row must contain six numbers.")
        values = []
        for item in row:
            if type(item) not in (int, float) or not math.isfinite(float(item)):
                raise ValueError("simulation.image_jacobian values must be finite numbers.")
            values.append(float(item))
        rows.append(tuple(values))
    return tuple(rows)


def _number_vector(
    settings: Dict[str, Any], key: str, default: Tuple[float, ...], size: int, minimum: float, maximum: float
) -> Tuple[float, ...]:
    """Read one finite fixed-size JSON number vector without accepting booleans."""

    value = settings.get(key, list(default))
    if not isinstance(value, list) or len(value) != size:
        raise ValueError("simulation.{0} must contain exactly {1} numeric values.".format(key, size))
    values = []
    for item in value:
        if type(item) not in (int, float) or not math.isfinite(float(item)):
            raise ValueError("simulation.{0} must contain only finite numbers.".format(key))
        number = float(item)
        if number < minimum or number > maximum:
            raise ValueError(
                "simulation.{0} values must be from {1} to {2}.".format(key, minimum, maximum)
            )
        values.append(number)
    return tuple(values)


def _string(settings: Dict[str, Any], key: str, default: str) -> str:
    """Read one non-empty JSON string without accepting filenames or path traversal semantics."""

    value = settings.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError("simulation.{0} must be a non-empty string.".format(key))
    return value


def _number(settings: Dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    """Read one bounded finite JSON number without accepting boolean flags."""

    value = settings.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError("simulation.{0} must be a finite number.".format(key))
    number = float(value)
    if number < minimum or number > maximum:
        raise ValueError("simulation.{0} must be from {1} to {2}.".format(key, minimum, maximum))
    return number


def _integer(settings: Dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    """Read one strict bounded integer without silently accepting float or boolean values."""

    value = settings.get(key, default)
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("simulation.{0} must be an integer from {1} to {2}.".format(key, minimum, maximum))
    return value
