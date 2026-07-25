"""Stable, hardware-neutral data contracts for the runtime."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Union


class GripperAction(Enum):
    """Supported high-level gripper actions."""

    OPEN = "open"
    CLOSE = "close"
    HOLD = "hold"
    STOP = "stop"


class RobotAction(Enum):
    """Supported high-level robot actions."""

    MOVE_JOINTS = "move_joints"
    MOVE_LINEAR = "move_linear"
    STOP = "stop"


class TargetRole(Enum):
    """The authority level of an execution target."""

    PRIMARY = "primary"
    MIRROR = "mirror"


class RuntimeMode(Enum):
    """Runtime behavior for reload and device safety."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class CameraMounting(Enum):
    """Supported camera-to-robot calibration topologies."""

    FIXED = "fixed"
    TOOL = "tool"


class CameraParameterKind(Enum):
    """Browser control shape for one normalized camera parameter."""

    FLOAT = "float"
    ENUM = "enum"
    BOOLEAN = "boolean"


class CameraParameterApplyMode(Enum):
    """Whether applying a camera parameter can continue or must restart acquisition."""

    LIVE = "live"
    RESTART = "restart"


@dataclass(frozen=True)
class Pose3D:
    """A six-axis pose expressed in one named coordinate frame."""

    x_mm: float
    y_mm: float
    z_mm: float
    rx_rad: float
    ry_rad: float
    rz_rad: float
    frame_id: str


@dataclass(frozen=True)
class BoundingBox2D:
    """A normalized image bounding box."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class SensorReading:
    """A calibrated scalar reading from one non-visual sensing channel."""

    sensor_name: str
    value: float
    unit: str


@dataclass(frozen=True)
class ImageFrame:
    """A camera frame and the metadata required to interpret its pixel payload.

    ``pixel_format`` is a normalized identifier such as ``rgb8`` or ``mono8``.
    Pixel bytes remain acquisition data, not a persisted image file or a perception
    result. Width and height are required whenever a payload is supplied for rendering.
    """

    camera_id: str
    captured_at: float
    frame_reference: Optional[str]
    calibration_id: Optional[str]
    healthy: bool
    pixel_payload: Optional[bytes] = None
    width: Optional[int] = None
    height: Optional[int] = None
    pixel_format: Optional[str] = None


CameraParameterValue = Union[float, str, bool]
"""One scalar camera setting value accepted by the normalized control contract."""


@dataclass(frozen=True)
class CameraParameter:
    """A whitelisted, browser-safe camera parameter and its live device constraints."""

    key: str
    kind: CameraParameterKind
    apply_mode: CameraParameterApplyMode
    value: CameraParameterValue
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    unit: Optional[str] = None
    options: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CameraParameterUpdateResult:
    """The normalized parameter state returned after one atomic browser update request."""

    parameters: Tuple[CameraParameter, ...]
    restarted_acquisition: bool


@dataclass(frozen=True)
class CameraCalibration:
    """The calibrated transform from a camera frame to its parent frame."""

    calibration_id: str
    camera_id: str
    mounting: CameraMounting
    parent_frame: str
    camera_to_parent: Pose3D
    valid: bool = True


@dataclass(frozen=True)
class GraspCandidate:
    """A proposed grasp pose that has been resolved into robot-base coordinates."""

    pose: Pose3D
    score: float


@dataclass(frozen=True)
class DetectedObject:
    """One visual object with optional 3D pose and grasp candidates."""

    label: str
    bounding_box: BoundingBox2D
    confidence: float
    pose: Optional[Pose3D]
    grasp_candidates: Tuple[GraspCandidate, ...]


@dataclass(frozen=True)
class PerceptionResult:
    """Semantic perception output consumed by planning plugins."""

    camera_id: str
    captured_at: float
    valid: bool
    reason: str
    objects: Tuple[DetectedObject, ...]


@dataclass(frozen=True)
class PerceptionSnapshot:
    """Planner input composed from vision and optional scalar sensors."""

    vision: PerceptionResult
    sensor_readings: Tuple[SensorReading, ...] = ()


@dataclass(frozen=True)
class GripperCommand:
    """A normalized gripper command proposal, not an executable SDK call."""

    action: GripperAction
    target_position: Optional[int] = None
    force_percent: Optional[int] = None
    speed_percent: Optional[int] = None


@dataclass(frozen=True)
class RobotCommand:
    """A normalized robot command proposal, not an executable SDK call."""

    action: RobotAction
    joint_positions_rad: Tuple[float, ...] = ()
    target_pose: Optional[Pose3D] = None
    speed: float = 0.0


CommandPayload = Union[GripperCommand, RobotCommand]


@dataclass(frozen=True)
class CommandEnvelope:
    """A command proposal with a runtime-generated correlation identifier."""

    command_id: str
    issued_at: float
    payload: CommandPayload


@dataclass(frozen=True)
class GripperStatus:
    """The safety-relevant live state reported by a gripper adapter."""

    initialized: bool
    position: int
    gripping: bool
    faulted: bool = False
    emergency_stopped: bool = False


@dataclass(frozen=True)
class RobotStatus:
    """The safety-relevant live state reported by a robot adapter."""

    initialized: bool
    joint_positions_rad: Tuple[float, ...]
    tcp_pose: Pose3D
    faulted: bool = False
    emergency_stopped: bool = False
    connected: bool = False
    powered: bool = False
    enabled: bool = False


@dataclass(frozen=True)
class JointPositionSnapshot:
    """One timestamped six-axis joint-angle observation in radians.

    This contract describes robot configuration coordinates such as J1 through J6.
    It does not describe the Cartesian positions of the physical joint axes or links.
    """

    captured_at: float
    joint_positions_rad: Tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class TelemetrySnapshot:
    """A synchronized robot and gripper state for one execution target."""

    captured_at: float
    robot: RobotStatus
    gripper: GripperStatus


@dataclass(frozen=True)
class SafetyDecision:
    """The deterministic authorization result for one command proposal."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class AdapterExecutionReport:
    """The result of dispatching one command to one named target."""

    target_name: str
    command_id: str
    succeeded: bool
    message: str
    telemetry: Optional[TelemetrySnapshot] = None


@dataclass(frozen=True)
class ExecutionResult:
    """The end-to-end observable outcome of one runtime objective."""

    command: Optional[CommandEnvelope]
    decision: SafetyDecision
    reports: Tuple[AdapterExecutionReport, ...]
    perception: PerceptionResult


@dataclass(frozen=True)
class ComponentManifest:
    """Metadata used by the project-local adapter and plugin registry."""

    name: str
    version: str
    configuration_key: str
    capabilities: Tuple[str, ...]
    factory_name: str = ""
