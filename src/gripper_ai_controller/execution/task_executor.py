"""Pick and Place 任务执行器"""
import asyncio
from enum import Enum
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field
from datetime import datetime
import logging

from ..planning import GraspPlanner, GraspPlan, Pose6D
from ..coordination import CoordinateTransformer


logger = logging.getLogger(__name__)


class TaskState(Enum):
    """任务执行状态"""
    IDLE = "idle"
    DETECTING = "detecting"
    PLANNING = "planning"
    APPROACHING = "approaching"
    GRASPING = "grasping"
    LIFTING = "lifting"
    TRANSPORTING = "transporting"
    PLACING = "placing"
    RETURNING = "returning"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    state: TaskState
    error_message: Optional[str] = None
    execution_time: float = 0.0  # 秒
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state.value,
            "error_message": self.error_message,
            "execution_time": self.execution_time,
            "metadata": self.metadata
        }


class PickAndPlaceExecutor:
    """
    Pick and Place 任务执行器

    状态机驱动的抓取-放置流程执行引擎
    """

    def __init__(
        self,
        vision_service: Any,  # 视觉服务
        planner: GraspPlanner,
        transformer: CoordinateTransformer,
        robot_controller: Any,  # 机器人控制器
        gripper_controller: Any,  # 夹爪控制器
        place_position: Pose6D,  # 放置位置
        home_position: Optional[Pose6D] = None,  # Home位置
        table_height: float = 0.0,  # 工作台高度(mm)
        max_retries: int = 3
    ):
        """
        初始化执行器

        Args:
            vision_service: 视觉检测服务
            planner: 抓取规划器
            transformer: 坐标转换器
            robot_controller: 机器人控制器
            gripper_controller: 夹爪控制器
            place_position: 放置目标位置
            home_position: Home位置（可选）
            table_height: 工作台高度
            max_retries: 最大重试次数
        """
        self.vision = vision_service
        self.planner = planner
        self.transformer = transformer
        self.robot = robot_controller
        self.gripper = gripper_controller
        self.place_position = place_position
        self.home_position = home_position
        self.table_height = table_height
        self.max_retries = max_retries

        self.state = TaskState.IDLE
        self.current_plan: Optional[GraspPlan] = None
        self.state_callbacks: List[Callable[[TaskState], None]] = []

        # 统计信息
        self.total_attempts = 0
        self.successful_picks = 0
        self.failed_picks = 0

    def register_state_callback(self, callback: Callable[[TaskState], None]):
        """注册状态变化回调"""
        self.state_callbacks.append(callback)

    def _set_state(self, new_state: TaskState):
        """更新状态并触发回调"""
        old_state = self.state
        self.state = new_state
        logger.info(f"状态转换: {old_state.value} -> {new_state.value}")

        for callback in self.state_callbacks:
            try:
                callback(new_state)
            except Exception as e:
                logger.error(f"状态回调错误: {e}")

    async def execute_pick_and_place(
        self,
        target_class: Optional[str] = None,
        min_confidence: float = 0.5
    ) -> ExecutionResult:
        """
        执行完整的拾取-放置任务

        Args:
            target_class: 目标物体类别（None表示任意物体）
            min_confidence: 最小检测置信度

        Returns:
            执行结果
        """
        start_time = datetime.now()
        self.total_attempts += 1

        try:
            # 1. 检测物体
            self._set_state(TaskState.DETECTING)
            detected_object = await self._detect_target(target_class, min_confidence)
            if detected_object is None:
                return ExecutionResult(
                    success=False,
                    state=TaskState.FAILED,
                    error_message="未检测到目标物体"
                )

            # 2. 规划抓取
            self._set_state(TaskState.PLANNING)
            plan = await self._plan_grasp(detected_object)
            if plan is None:
                return ExecutionResult(
                    success=False,
                    state=TaskState.FAILED,
                    error_message="无法规划有效的抓取方案"
                )

            self.current_plan = plan

            # 3. 执行抓取流程
            await self._execute_grasp_sequence(plan)

            # 4. 运送到放置点
            self._set_state(TaskState.TRANSPORTING)
            await self._transport_to_place()

            # 5. 放置物体
            self._set_state(TaskState.PLACING)
            await self._place_object()

            # 6. 返回Home
            self._set_state(TaskState.RETURNING)
            await self._return_home()

            # 完成
            self._set_state(TaskState.COMPLETED)
            self.successful_picks += 1

            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                success=True,
                state=TaskState.COMPLETED,
                execution_time=execution_time,
                metadata={
                    "object": detected_object,
                    "plan": plan.to_dict()
                }
            )

        except Exception as e:
            logger.error(f"执行失败: {e}", exc_info=True)
            self._set_state(TaskState.FAILED)
            self.failed_picks += 1

            # 尝试紧急停止和返回安全位置
            try:
                await self._emergency_recovery()
            except Exception as recovery_error:
                logger.error(f"恢复失败: {recovery_error}")

            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                state=TaskState.FAILED,
                error_message=str(e),
                execution_time=execution_time
            )

    async def _detect_target(
        self,
        target_class: Optional[str],
        min_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """检测目标物体"""
        logger.info(f"检测目标: class={target_class}, min_conf={min_confidence}")

        # 调用视觉服务
        detections = await self.vision.detect_objects()

        # 过滤结果
        candidates = []
        for det in detections:
            if det.get("confidence", 0) < min_confidence:
                continue
            if target_class and det.get("class") != target_class:
                continue
            candidates.append(det)

        if not candidates:
            return None

        # 选择置信度最高的
        best = max(candidates, key=lambda d: d.get("confidence", 0))
        logger.info(f"选中物体: class={best.get('class')}, conf={best.get('confidence'):.2f}")

        return best

    async def _plan_grasp(self, detection: Dict[str, Any]) -> Optional[GraspPlan]:
        """规划抓取"""
        logger.info("规划抓取方案")

        # 从检测结果提取坐标并转换到机器人坐标系
        # 假设检测结果包含像素坐标
        if "centroid" in detection:
            pixel_x = detection["centroid"]["x"]
            pixel_y = detection["centroid"]["y"]

            # 使用工作台高度估算深度
            depth = self.transformer.estimate_depth_from_plane(
                pixel_x, pixel_y, self.table_height
            )

            # 转换到机器人坐标
            robot_pos = self.transformer.pixel_to_robot_base(
                pixel_x, pixel_y, depth
            )

            detection["robot_position"] = {
                "x": robot_pos.x,
                "y": robot_pos.y,
                "z": robot_pos.z
            }

        # 调用规划器
        plan = self.planner.plan_grasp_from_detection(
            detection,
            depth_mm=detection.get("robot_position", {}).get("z", self.table_height),
            table_height=self.table_height
        )

        if plan:
            # 验证计划
            is_valid, errors = self.planner.validate_plan(plan)
            if not is_valid:
                logger.warning(f"计划验证失败: {errors}")
                return None

            logger.info(f"抓取计划: score={plan.confidence:.2f}")

        return plan

    async def _execute_grasp_sequence(self, plan: GraspPlan):
        """执行抓取序列"""
        # 1. 打开夹爪
        logger.info(f"打开夹爪: {plan.gripper_open_width:.1f}mm")
        await self.gripper.move_to(plan.gripper_open_width)

        # 2. 移动到预抓位置
        self._set_state(TaskState.APPROACHING)
        logger.info(f"移动到预抓位置: {plan.pre_grasp_pose.to_dict()}")
        await self.robot.move_to_pose(plan.pre_grasp_pose)

        # 3. 下降到抓取位置
        logger.info(f"下降到抓取位置: {plan.grasp_pose.to_dict()}")
        await self.robot.move_linear(plan.grasp_pose)

        # 4. 闭合夹爪抓取
        self._set_state(TaskState.GRASPING)
        logger.info(f"闭合夹爪: {plan.gripper_close_width:.1f}mm, 力: {plan.gripper_force:.1f}N")
        await self.gripper.grasp(plan.gripper_close_width, force=plan.gripper_force)

        # 等待稳定
        await asyncio.sleep(0.5)

        # 5. 提升
        self._set_state(TaskState.LIFTING)
        logger.info(f"提升: {plan.lift_pose.to_dict()}")
        await self.robot.move_linear(plan.lift_pose)

        # 检查是否成功抓取（通过夹爪位置反馈）
        gripper_pos = await self.gripper.get_position()
        if abs(gripper_pos - plan.gripper_close_width) > 10.0:
            raise RuntimeError(f"抓取失败: 夹爪位置异常 {gripper_pos:.1f}mm")

        logger.info("抓取成功")

    async def _transport_to_place(self):
        """运送到放置位置"""
        logger.info(f"运送到放置位置: {self.place_position.to_dict()}")

        # 先提升到安全高度
        if self.current_plan:
            safe_height = max(
                self.current_plan.lift_pose.z,
                self.place_position.z + 100
            )
            safe_pose = Pose6D(
                x=self.current_plan.lift_pose.x,
                y=self.current_plan.lift_pose.y,
                z=safe_height,
                rx=self.current_plan.lift_pose.rx,
                ry=self.current_plan.lift_pose.ry,
                rz=self.current_plan.lift_pose.rz
            )
            await self.robot.move_to_pose(safe_pose)

        # 移动到放置点上方
        approach_place = Pose6D(
            x=self.place_position.x,
            y=self.place_position.y,
            z=self.place_position.z + 100,
            rx=self.place_position.rx,
            ry=self.place_position.ry,
            rz=self.place_position.rz
        )
        await self.robot.move_to_pose(approach_place)

    async def _place_object(self):
        """放置物体"""
        logger.info("放置物体")

        # 下降到放置位置
        await self.robot.move_linear(self.place_position)

        # 打开夹爪释放
        await self.gripper.release()
        await asyncio.sleep(0.5)

        # 后退
        retreat_pose = Pose6D(
            x=self.place_position.x,
            y=self.place_position.y,
            z=self.place_position.z + 50,
            rx=self.place_position.rx,
            ry=self.place_position.ry,
            rz=self.place_position.rz
        )
        await self.robot.move_linear(retreat_pose)

        logger.info("放置完成")

    async def _return_home(self):
        """返回Home位置"""
        if self.home_position:
            logger.info(f"返回Home: {self.home_position.to_dict()}")
            await self.robot.move_to_pose(self.home_position)
        else:
            logger.info("未配置Home位置，保持当前位置")

        self._set_state(TaskState.IDLE)

    async def _emergency_recovery(self):
        """紧急恢复"""
        logger.warning("执行紧急恢复")

        try:
            # 停止所有运动
            await self.robot.stop()

            # 打开夹爪
            await self.gripper.release()

            # 尝试返回安全位置
            if self.home_position:
                await self.robot.move_to_pose(self.home_position)

        except Exception as e:
            logger.error(f"紧急恢复失败: {e}")
            raise

    async def abort(self):
        """中止当前任务"""
        logger.warning("任务被中止")
        self._set_state(TaskState.ABORTED)
        await self._emergency_recovery()

    def get_statistics(self) -> Dict[str, Any]:
        """获取执行统计"""
        return {
            "total_attempts": self.total_attempts,
            "successful_picks": self.successful_picks,
            "failed_picks": self.failed_picks,
            "success_rate": (
                self.successful_picks / self.total_attempts
                if self.total_attempts > 0 else 0.0
            ),
            "current_state": self.state.value
        }
