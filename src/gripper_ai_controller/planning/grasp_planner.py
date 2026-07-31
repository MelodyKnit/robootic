"""抓取规划器 - 计算抓取点、姿态和轨迹"""
import numpy as np
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum


class GraspStrategy(Enum):
    """抓取策略类型"""
    VERTICAL_TOP = "vertical_top"  # 垂直从上方抓取
    HORIZONTAL = "horizontal"  # 水平侧面抓取
    ANGLED = "angled"  # 倾斜抓取


@dataclass
class Pose6D:
    """6自由度位姿 (位置 + 姿态)"""
    x: float  # mm
    y: float  # mm
    z: float  # mm
    rx: float  # 度 (绕X轴旋转)
    ry: float  # 度 (绕Y轴旋转)
    rz: float  # 度 (绕Z轴旋转)

    def to_dict(self) -> Dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "rx": self.rx,
            "ry": self.ry,
            "rz": self.rz
        }

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> "Pose6D":
        return cls(**data)


@dataclass
class GraspPoint:
    """抓取点信息"""
    position: Pose6D
    gripper_width: float  # 夹爪开口宽度 (mm)
    approach_direction: np.ndarray  # 接近方向向量
    score: float = 1.0  # 抓取质量评分 (0-1)
    strategy: GraspStrategy = GraspStrategy.VERTICAL_TOP


@dataclass
class GraspPlan:
    """完整的抓取计划"""
    grasp_point: GraspPoint

    # 运动轨迹关键点
    pre_grasp_pose: Pose6D  # 预抓位置 (接近点)
    grasp_pose: Pose6D  # 抓取位置
    lift_pose: Pose6D  # 提升位置

    # 夹爪控制
    gripper_open_width: float  # 接近时的开口
    gripper_close_width: float  # 抓取时的闭合宽度
    gripper_force: float  # 抓取力 (N)

    # 元数据
    object_id: Optional[str] = None
    object_class: Optional[str] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grasp_point": {
                "position": self.grasp_point.position.to_dict(),
                "gripper_width": self.grasp_point.gripper_width,
                "score": self.grasp_point.score,
                "strategy": self.grasp_point.strategy.value
            },
            "trajectory": {
                "pre_grasp": self.pre_grasp_pose.to_dict(),
                "grasp": self.grasp_pose.to_dict(),
                "lift": self.lift_pose.to_dict()
            },
            "gripper": {
                "open_width": self.gripper_open_width,
                "close_width": self.gripper_close_width,
                "force": self.gripper_force
            },
            "object": {
                "id": self.object_id,
                "class": self.object_class,
                "confidence": self.confidence
            },
            "metadata": self.metadata
        }


class GraspPlanner:
    """
    抓取规划器

    根据物体的位置、姿态和形状，规划合适的抓取方案
    """

    def __init__(
        self,
        gripper_max_width: float = 85.0,  # mm
        gripper_finger_width: float = 10.0,  # mm
        safety_margin: float = 5.0,  # mm
        approach_distance: float = 100.0,  # mm
        lift_height: float = 50.0,  # mm
        default_force: float = 30.0  # N
    ):
        """
        初始化抓取规划器

        Args:
            gripper_max_width: 夹爪最大开口
            gripper_finger_width: 夹爪指尖宽度
            safety_margin: 安全余量
            approach_distance: 接近距离
            lift_height: 提升高度
            default_force: 默认抓取力
        """
        self.gripper_max_width = gripper_max_width
        self.gripper_finger_width = gripper_finger_width
        self.safety_margin = safety_margin
        self.approach_distance = approach_distance
        self.lift_height = lift_height
        self.default_force = default_force

    def plan_vertical_grasp(
        self,
        object_center: Tuple[float, float, float],  # (x, y, z) in robot base frame
        object_size: Tuple[float, float, float],  # (width, height, depth)
        object_orientation: float = 0.0,  # 物体旋转角度（度）
        table_height: Optional[float] = None,
        object_class: Optional[str] = None
    ) -> Optional[GraspPlan]:
        """
        规划垂直从上方的抓取

        Args:
            object_center: 物体中心坐标 (mm)
            object_size: 物体尺寸 (宽, 高, 深) (mm)
            object_orientation: 物体绕Z轴旋转角度
            table_height: 工作台高度，如果提供则用于安全检查
            object_class: 物体类别（用于策略选择）

        Returns:
            GraspPlan 或 None（如果无法抓取）
        """
        x, y, z = object_center
        width, height, depth = object_size

        # 选择抓取方向（较小的维度）
        grasp_width = min(width, depth)

        # 检查是否在夹爪范围内
        required_opening = grasp_width + 2 * self.safety_margin
        if required_opening > self.gripper_max_width:
            return None  # 物体太大，无法抓取

        # 计算抓取高度（物体顶部上方一点）
        grasp_z = z + height / 2 - 5.0  # 略低于顶部，确保接触

        # 安全检查：不要撞到工作台
        if table_height is not None and grasp_z < table_height + 10:
            grasp_z = table_height + 10

        # 抓取位姿（垂直向下）
        grasp_pose = Pose6D(
            x=x,
            y=y,
            z=grasp_z,
            rx=180.0,  # 夹爪朝下
            ry=0.0,
            rz=object_orientation  # 对齐物体方向
        )

        # 预抓位姿（上方接近点）
        pre_grasp_pose = Pose6D(
            x=x,
            y=y,
            z=grasp_z + self.approach_distance,
            rx=180.0,
            ry=0.0,
            rz=object_orientation
        )

        # 提升位姿
        lift_pose = Pose6D(
            x=x,
            y=y,
            z=grasp_z + self.lift_height,
            rx=180.0,
            ry=0.0,
            rz=object_orientation
        )

        # 夹爪控制参数
        gripper_open = required_opening + 10.0  # 接近时多开10mm
        gripper_close = grasp_width - 2.0  # 闭合到略小于物体宽度

        # 创建抓取点
        grasp_point = GraspPoint(
            position=grasp_pose,
            gripper_width=gripper_close,
            approach_direction=np.array([0, 0, -1]),  # 向下
            score=self._calculate_grasp_score(object_size, grasp_width),
            strategy=GraspStrategy.VERTICAL_TOP
        )

        # 创建完整计划
        plan = GraspPlan(
            grasp_point=grasp_point,
            pre_grasp_pose=pre_grasp_pose,
            grasp_pose=grasp_pose,
            lift_pose=lift_pose,
            gripper_open_width=gripper_open,
            gripper_close_width=gripper_close,
            gripper_force=self.default_force,
            object_class=object_class,
            confidence=grasp_point.score,
            metadata={
                "object_center": object_center,
                "object_size": object_size,
                "grasp_width": grasp_width
            }
        )

        return plan

    def plan_grasp_from_detection(
        self,
        detection: Dict[str, Any],
        depth_mm: float,
        table_height: Optional[float] = None
    ) -> Optional[GraspPlan]:
        """
        从物体检测结果规划抓取

        Args:
            detection: 物体检测结果字典，包含 bbox, class, pose等
            depth_mm: 物体深度（Z坐标）
            table_height: 工作台高度

        Returns:
            GraspPlan 或 None
        """
        # 提取物体信息
        if "pose" in detection and "position" in detection["pose"]:
            pos = detection["pose"]["position"]
            center = (pos["x"], pos["y"], pos["z"])
        else:
            # 从bbox估算中心
            bbox = detection.get("bbox", {})
            center_x = (bbox.get("x_min", 0) + bbox.get("x_max", 0)) / 2
            center_y = (bbox.get("y_min", 0) + bbox.get("y_max", 0)) / 2
            center = (center_x, center_y, depth_mm)

        # 提取尺寸
        if "pose" in detection and "dimensions" in detection["pose"]:
            dims = detection["pose"]["dimensions"]
            size = (dims.get("width", 50), dims.get("height", 50), dims.get("depth", 50))
        else:
            # 从bbox估算尺寸
            bbox = detection.get("bbox", {})
            width = bbox.get("x_max", 50) - bbox.get("x_min", 0)
            height = bbox.get("y_max", 50) - bbox.get("y_min", 0)
            size = (width, height, 30)  # 默认深度

        # 提取方向
        orientation = 0.0
        if "pose" in detection and "orientation" in detection["pose"]:
            orientation = detection["pose"]["orientation"].get("angle_deg", 0.0)

        # 物体类别
        object_class = detection.get("class", "unknown")

        return self.plan_vertical_grasp(
            object_center=center,
            object_size=size,
            object_orientation=orientation,
            table_height=table_height,
            object_class=object_class
        )

    def _calculate_grasp_score(
        self,
        object_size: Tuple[float, float, float],
        grasp_width: float
    ) -> float:
        """
        计算抓取质量评分

        Args:
            object_size: 物体尺寸
            grasp_width: 实际抓取宽度

        Returns:
            评分 0.0-1.0，越高越好
        """
        width, height, depth = object_size

        # 因素1: 夹爪利用率（太小或太大都不好）
        utilization = grasp_width / self.gripper_max_width
        if utilization < 0.2:
            util_score = utilization * 5  # 太小打折
        elif utilization > 0.9:
            util_score = (1.0 - utilization) * 10  # 太大打折
        else:
            util_score = 1.0

        # 因素2: 物体稳定性（高度/宽度比）
        aspect_ratio = height / max(width, depth)
        if aspect_ratio > 3.0:
            stability_score = 0.5  # 细长物体不稳定
        elif aspect_ratio > 2.0:
            stability_score = 0.7
        else:
            stability_score = 1.0

        # 综合评分
        score = (util_score * 0.6 + stability_score * 0.4)
        return max(0.0, min(1.0, score))

    def generate_approach_trajectory(
        self,
        plan: GraspPlan,
        num_waypoints: int = 5
    ) -> List[Pose6D]:
        """
        生成接近轨迹的中间点

        Args:
            plan: 抓取计划
            num_waypoints: 轨迹点数量

        Returns:
            轨迹点列表（从预抓位置到抓取位置）
        """
        waypoints = []
        pre = plan.pre_grasp_pose
        target = plan.grasp_pose

        for i in range(num_waypoints):
            t = i / (num_waypoints - 1)  # 0 到 1

            # 线性插值位置
            x = pre.x + t * (target.x - pre.x)
            y = pre.y + t * (target.y - pre.y)
            z = pre.z + t * (target.z - pre.z)

            # 姿态保持不变
            waypoints.append(Pose6D(
                x=x, y=y, z=z,
                rx=target.rx,
                ry=target.ry,
                rz=target.rz
            ))

        return waypoints

    def validate_plan(
        self,
        plan: GraspPlan,
        robot_workspace: Optional[Dict[str, Tuple[float, float]]] = None,
        obstacles: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, List[str]]:
        """
        验证抓取计划的可行性

        Args:
            plan: 抓取计划
            robot_workspace: 机器人工作空间 {"x": (min, max), "y": ..., "z": ...}
            obstacles: 障碍物列表

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # 检查夹爪开口
        if plan.gripper_open_width > self.gripper_max_width:
            errors.append(f"夹爪开口超限: {plan.gripper_open_width:.1f}mm > {self.gripper_max_width}mm")

        if plan.gripper_close_width < 5.0:
            errors.append(f"夹爪闭合过小: {plan.gripper_close_width:.1f}mm < 5mm")

        # 检查工作空间
        if robot_workspace:
            for pose_name, pose in [
                ("预抓位置", plan.pre_grasp_pose),
                ("抓取位置", plan.grasp_pose),
                ("提升位置", plan.lift_pose)
            ]:
                for axis in ["x", "y", "z"]:
                    value = getattr(pose, axis)
                    if axis in robot_workspace:
                        min_val, max_val = robot_workspace[axis]
                        if not (min_val <= value <= max_val):
                            errors.append(
                                f"{pose_name} {axis}轴超出工作空间: "
                                f"{value:.1f}mm 不在 [{min_val}, {max_val}]"
                            )

        # TODO: 障碍物碰撞检测

        return (len(errors) == 0, errors)
