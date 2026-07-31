# 底层硬件控制完善计划

## 📋 目标

在不产生实际运动的前提下，完善底层硬件控制层，包括：
1. 增强状态监控和遥测数据
2. 完善命令验证和安全检查
3. 改进错误处理和恢复机制
4. 增加更多命令类型支持（干运行）
5. 完善模拟器功能
6. 增强日志和调试能力

---

## ✅ 当前实现状态评估

### 机械臂控制（JAKA）

#### 已实现 ✓
- SDK 连接和登录
- 状态读取（关节角、TCP位姿、错误状态）
- 干运行模拟器（JakaDryRunRobotAdapter）
- 关节运动预览和验证
- 手动控制授权机制
- 运动约束检查

#### 待完善 ⚠️
1. **遥测数据不完整**：
   - 缺少关节速度反馈
   - 缺少关节力矩反馈
   - 缺少TCP速度信息
   - 缺少运动完成度估算

2. **命令类型有限**：
   - 只支持关节运动
   - 不支持直线运动（MOVE_LINEAR）
   - 不支持工具坐标系运动
   - 不支持 jog 运动

3. **错误恢复不足**：
   - 缺少碰撞恢复机制
   - 缺少网络断连自动重连
   - 缺少状态不一致检测

4. **安全检查不够细致**：
   - 缺少工作空间边界检查
   - 缺少速度/加速度限制验证
   - 缺少运动轨迹碰撞预检查

### 夹爪控制（PGI）

#### 已实现 ✓
- TCP 连接和通信
- 状态读取（位置、夹持状态、初始化状态）
- 位置控制和力控制
- 初始化超时机制
- 操作锁防止并发

#### 待完善 ⚠️
1. **反馈数据单一**：
   - 目标位置不是实时反馈位置
   - 缺少实际夹持力反馈
   - 缺少电流反馈

2. **命令支持不全**：
   - 不支持速度控制
   - 不支持软件停止
   - 不支持 HOLD 命令

3. **错误处理基础**：
   - 初始化失败后恢复策略简单
   - 夹取失败检测不足
   - 物体掉落检测依赖寄存器

### 模拟器

#### 已实现 ✓
- 基本状态模拟
- 命令应用到内存
- 镜像目标同步

#### 待完善 ⚠️
1. **物理模拟缺失**：
   - 没有运动时间模拟
   - 没有加速度模型
   - 没有惯性和阻尼

2. **碰撞检测缺失**：
   - 没有自碰撞检查
   - 没有环境碰撞检查
   - 没有工作空间限制

3. **传感器模拟不足**：
   - 没有力传感器模拟
   - 没有噪声模型

---

## 🎯 增强任务列表

### Phase 1: 增强遥测数据（1-2天）- 优先级 P0

#### 任务 1.1: 机械臂增强遥测
```python
# 新增字段到 RobotStatus
@dataclass(frozen=True)
class RobotStatus:
    # 现有字段...
    
    # 新增：
    joint_velocities_rad_per_sec: Optional[Tuple[float, ...]] = None
    joint_torques_nm: Optional[Tuple[float, ...]] = None
    tcp_velocity_mm_per_sec: Optional[Tuple[float, ...]] = None  # [vx, vy, vz, wx, wy, wz]
    motion_progress_percent: Optional[float] = None  # 0-100
    last_command_id: Optional[str] = None
    last_error_code: Optional[int] = None
    last_error_message: Optional[str] = None
    temperature_celsius: Optional[Tuple[float, ...]] = None  # 各关节温度
```

**实现步骤**：
- [ ] 修改 `domain/models.py` 添加新字段
- [ ] 更新 `JakaAdapter.get_status()` 读取更多寄存器
- [ ] 更新 `JakaDryRunRobotAdapter` 生成模拟数据
- [ ] 添加单元测试验证新字段

#### 任务 1.2: 夹爪增强遥测
```python
# 新增字段到 GripperStatus
@dataclass(frozen=True)
class GripperStatus:
    # 现有字段...
    
    # 新增：
    actual_position: Optional[int] = None  # 实时位置反馈
    actual_force_percent: Optional[float] = None  # 实时力反馈
    current_ma: Optional[float] = None  # 电流
    object_detected: bool = False  # 物体检测
    position_reached: bool = False  # 是否到达目标位置
    force_reached: bool = False  # 是否达到目标力
```

**实现步骤**：
- [ ] 修改 `domain/models.py` 添加新字段
- [ ] 研究 PGI 协议文档，找到对应寄存器
- [ ] 更新 `PgiTcpGripperAdapter._read_status_unlocked()`
- [ ] 添加单元测试

### Phase 2: 增强命令验证（2-3天）- 优先级 P0

#### 任务 2.1: 工作空间边界检查
```python
# src/gripper_ai_controller/services/workspace.py

@dataclass(frozen=True)
class WorkspaceConstraint:
    """工作空间约束定义"""
    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    z_min_mm: float
    z_max_mm: float
    name: str = "default"

class WorkspaceValidator:
    """工作空间验证器"""
    
    def __init__(self, constraint: WorkspaceConstraint):
        self.constraint = constraint
    
    def validate_tcp_pose(self, pose: Pose3D) -> Tuple[bool, str]:
        """验证 TCP 位姿是否在工作空间内"""
        if pose.x_mm < self.constraint.x_min_mm or pose.x_mm > self.constraint.x_max_mm:
            return False, f"TCP X坐标 {pose.x_mm}mm 超出范围 [{self.constraint.x_min_mm}, {self.constraint.x_max_mm}]"
        # ... Y, Z 检查
        return True, "OK"
    
    def validate_joint_positions(self, joint_pos_rad: Tuple[float, ...]) -> Tuple[bool, str]:
        """验证关节角是否会导致 TCP 超出工作空间"""
        # 需要正向运动学
        # 暂时返回 True，等待运动学模块
        return True, "Workspace validation requires forward kinematics"
```

**实现步骤**：
- [ ] 创建 `services/workspace.py`
- [ ] 集成到 `SafetyPolicy`
- [ ] 添加配置文件支持
- [ ] 编写单元测试

#### 任务 2.2: 运动速度和加速度验证
```python
# src/gripper_ai_controller/services/motion_limits.py

@dataclass(frozen=True)
class MotionLimits:
    """运动限制定义"""
    max_joint_velocity_rad_per_sec: Tuple[float, ...]  # 6个关节
    max_joint_acceleration_rad_per_sec2: Tuple[float, ...]
    max_tcp_velocity_mm_per_sec: float
    max_tcp_acceleration_mm_per_sec2: float

class MotionLimitsValidator:
    """运动限制验证器"""
    
    def __init__(self, limits: MotionLimits):
        self.limits = limits
    
    def validate_joint_command(
        self, 
        command: RobotCommand,
        current_status: RobotStatus
    ) -> Tuple[bool, str]:
        """验证关节命令的速度是否合理"""
        if command.speed_rad_per_second is None:
            return True, "No speed specified"
        
        # 检查每个关节的速度限制
        for i, max_vel in enumerate(self.limits.max_joint_velocity_rad_per_sec):
            if command.speed_rad_per_second > max_vel:
                return False, f"关节 {i+1} 速度 {command.speed_rad_per_second} 超过限制 {max_vel}"
        
        return True, "OK"
```

**实现步骤**：
- [ ] 创建 `services/motion_limits.py`
- [ ] 集成到 `SafetyPolicy`
- [ ] 添加配置支持
- [ ] 编写测试

### Phase 3: 命令类型扩展（3-4天）- 优先级 P1

#### 任务 3.1: 直线运动支持（干运行）
```python
# 扩展 JakaDryRunRobotAdapter

class JakaDryRunRobotAdapter(BaseAdapter, RobotAdapter):
    # ... 现有代码
    
    def preview_linear_move(self, command: RobotCommand) -> LinearMovePreview:
        """预览直线运动（不实际执行）"""
        if command.action != RobotAction.MOVE_LINEAR:
            raise ValueError("This method only previews linear moves")
        
        if command.target_pose is None:
            raise ValueError("Linear move requires target_pose")
        
        # 计算直线路径关键点
        # 暂时简化：只返回起点和终点
        return LinearMovePreview(
            start_tcp=self.status.tcp_pose,
            end_tcp=command.target_pose,
            estimated_duration_sec=self._estimate_linear_duration(
                self.status.tcp_pose, 
                command.target_pose,
                command.speed_mm_per_second or 100.0
            ),
            path_points=[],  # 未来添加插值点
        )
    
    def _estimate_linear_duration(
        self, 
        start: Pose3D, 
        end: Pose3D, 
        speed: float
    ) -> float:
        """估算直线运动时间"""
        dx = end.x_mm - start.x_mm
        dy = end.y_mm - start.y_mm
        dz = end.z_mm - start.z_mm
        distance = (dx**2 + dy**2 + dz**2) ** 0.5
        return distance / speed
```

**实现步骤**：
- [ ] 添加 `LinearMovePreview` 数据类
- [ ] 实现 `preview_linear_move()`
- [ ] 更新 `execute()` 支持 MOVE_LINEAR（仅更新TCP）
- [ ] 编写测试

#### 任务 3.2: STOP 命令完善
```python
# 增强 STOP 命令处理

async def execute(self, command: RobotCommand) -> RobotStatus:
    """处理 STOP 命令"""
    if command.action == RobotAction.STOP:
        # 标记运动中止
        self.status = replace(
            self.status,
            moving=False,
            last_command_id=command.command_id if hasattr(command, 'command_id') else None,
        )
        return self.status
    # ... 其他命令处理
```

### Phase 4: 错误处理和恢复（2-3天）- 优先级 P1

#### 任务 4.1: 网络断连自动重连
```python
# 扩展 JakaAdapter

class JakaAdapter(BaseAdapter, RobotAdapter):
    # ... 现有代码
    
    def __init__(self, ..., auto_reconnect: bool = True, max_reconnect_attempts: int = 3):
        # ... 现有初始化
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_attempts = 0
    
    async def _call_client_method_with_retry(self, method_name: str, *arguments: Any) -> Any:
        """带自动重连的客户端调用"""
        while True:
            try:
                return await self._call_client_method(method_name, *arguments)
            except JakaAdapterError as error:
                if not self.auto_reconnect or self._reconnect_attempts >= self.max_reconnect_attempts:
                    raise
                
                self._reconnect_attempts += 1
                logger.warning(f"JAKA 调用失败，尝试重连 ({self._reconnect_attempts}/{self.max_reconnect_attempts})")
                
                try:
                    await self.reconnect()
                    self._reconnect_attempts = 0  # 重连成功，重置计数
                except Exception as reconnect_error:
                    logger.error(f"重连失败: {reconnect_error}")
                    if self._reconnect_attempts >= self.max_reconnect_attempts:
                        raise
```

**实现步骤**：
- [ ] 添加重连逻辑
- [ ] 添加重连事件通知
- [ ] 添加配置选项
- [ ] 编写测试（模拟断连）

#### 任务 4.2: 状态不一致检测
```python
# src/gripper_ai_controller/services/state_monitor.py

class StateConsistencyMonitor:
    """状态一致性监控"""
    
    def __init__(self):
        self.expected_state = None
        self.last_command = None
    
    def record_command(self, command: CommandEnvelope):
        """记录发送的命令"""
        self.last_command = command
        self.expected_state = self._predict_state(command)
    
    def check_consistency(self, actual_status: RobotStatus) -> Tuple[bool, str]:
        """检查实际状态是否与预期一致"""
        if self.expected_state is None:
            return True, "No expected state"
        
        # 检查关节位置是否在容差内
        if self.expected_state.joint_positions_rad is not None:
            for i, (expected, actual) in enumerate(
                zip(self.expected_state.joint_positions_rad, actual_status.joint_positions_rad)
            ):
                if abs(expected - actual) > 0.01:  # 0.01 rad 容差
                    return False, f"关节{i+1}位置不一致: 期望{expected:.3f}, 实际{actual:.3f}"
        
        return True, "OK"
```

**实现步骤**：
- [ ] 创建状态监控模块
- [ ] 集成到 Runtime
- [ ] 添加异常事件发布
- [ ] 编写测试

### Phase 5: 完善模拟器（3-4天）- 优先级 P2

#### 任务 5.1: 运动时间模拟
```python
# 增强 SimulatedRobotAdapter

class SimulatedRobotAdapter(BaseAdapter, RobotAdapter):
    # ... 现有代码
    
    def __init__(self, ..., simulate_motion_time: bool = True):
        # ... 现有初始化
        self.simulate_motion_time = simulate_motion_time
        self._motion_start_time = None
        self._motion_end_time = None
        self._motion_target_state = None
    
    async def execute(self, command: RobotCommand) -> RobotStatus:
        """模拟运动时间的命令执行"""
        if command.action == RobotAction.MOVE_JOINTS:
            if self.simulate_motion_time:
                # 计算运动时间
                duration = self._calculate_motion_duration(
                    self.status.joint_positions_rad,
                    command.joint_positions_rad,
                    command.speed_rad_per_second or 0.5
                )
                
                # 标记运动中
                self.status = replace(self.status, moving=True)
                self._motion_start_time = time.time()
                self._motion_end_time = self._motion_start_time + duration
                self._motion_target_state = command.joint_positions_rad
                
                # 等待模拟运动完成
                await asyncio.sleep(duration)
                
                # 更新到目标位置
                self.status = replace(
                    self.status,
                    joint_positions_rad=command.joint_positions_rad,
                    moving=False
                )
            else:
                # 即时更新
                self.status = replace(
                    self.status,
                    joint_positions_rad=command.joint_positions_rad
                )
        
        return self.status
    
    def _calculate_motion_duration(
        self, 
        start: Tuple[float, ...], 
        end: Tuple[float, ...], 
        speed: float
    ) -> float:
        """计算关节运动时间（最慢关节决定）"""
        max_duration = 0.0
        for s, e in zip(start, end):
            angle_diff = abs(e - s)
            duration = angle_diff / speed
            max_duration = max(max_duration, duration)
        return max_duration
```

**实现步骤**：
- [ ] 添加运动时间计算
- [ ] 添加运动进度查询
- [ ] 支持运动中止
- [ ] 编写测试

#### 任务 5.2: 基本碰撞检测（自碰撞）
```python
# src/gripper_ai_controller/services/collision.py

class SelfCollisionChecker:
    """自碰撞检查器（简化版）"""
    
    def __init__(self, robot_model: str = "zu3"):
        self.robot_model = robot_model
        self._load_collision_rules()
    
    def _load_collision_rules(self):
        """加载碰撞规则（简化为关节角度限制组合）"""
        if self.robot_model == "zu3":
            # 简化规则：某些关节角度组合会导致碰撞
            self.forbidden_combinations = [
                # (joint_index_1, joint_index_2, min_angle_1, max_angle_1, min_angle_2, max_angle_2)
                # 例如：关节2和关节3某些角度组合会碰撞
                # 这需要根据实际机器人模型定义
            ]
    
    def check_collision(self, joint_positions_rad: Tuple[float, ...]) -> Tuple[bool, str]:
        """检查给定关节角是否会自碰撞"""
        # 简化实现：检查预定义的禁止组合
        for rule in self.forbidden_combinations:
            j1, j2, min1, max1, min2, max2 = rule
            if (min1 <= joint_positions_rad[j1] <= max1 and 
                min2 <= joint_positions_rad[j2] <= max2):
                return True, f"关节{j1+1}和关节{j2+1}可能发生碰撞"
        
        return False, "No self-collision detected"
```

**实现步骤**：
- [ ] 创建碰撞检查模块
- [ ] 定义 JAKA Zu 3 碰撞规则
- [ ] 集成到 SafetyPolicy
- [ ] 编写测试

### Phase 6: 增强日志和调试（1-2天）- 优先级 P2

#### 任务 6.1: 结构化日志
```python
# src/gripper_ai_controller/logging/structured_logger.py

import logging
import json
from datetime import datetime
from typing import Any, Dict

class StructuredLogger:
    """结构化日志记录器"""
    
    def __init__(self, name: str, log_file: str = "logs/hardware_control.jsonl"):
        self.logger = logging.getLogger(name)
        self.log_file = log_file
    
    def log_command(self, command: CommandEnvelope, status_before: Dict, status_after: Dict):
        """记录命令执行"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "command_executed",
            "command_id": command.command_id,
            "command_type": command.payload.action.value if hasattr(command.payload, 'action') else "unknown",
            "status_before": status_before,
            "status_after": status_after,
        }
        self._write_log(entry)
    
    def log_error(self, component: str, error: Exception, context: Dict):
        """记录错误"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "error",
            "component": component,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
        }
        self._write_log(entry)
    
    def _write_log(self, entry: Dict[str, Any]):
        """写入JSONL格式日志"""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
```

**实现步骤**：
- [ ] 创建结构化日志模块
- [ ] 集成到 Runtime 和适配器
- [ ] 添加日志查看工具
- [ ] 编写文档

#### 任务 6.2: 命令回放工具
```python
# scripts/tools/replay_commands.py

"""从日志文件回放命令序列用于调试"""

class CommandReplayer:
    """命令回放器"""
    
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.commands = []
        self._load_commands()
    
    def _load_commands(self):
        """从日志加载命令"""
        with open(self.log_file, 'r') as f:
            for line in f:
                entry = json.loads(line)
                if entry.get('event') == 'command_executed':
                    self.commands.append(entry)
    
    async def replay(self, runtime: Runtime, delay_sec: float = 1.0):
        """回放命令序列"""
        for cmd_entry in self.commands:
            print(f"回放命令: {cmd_entry['command_type']}")
            # 重建命令对象
            # ... 执行命令
            await asyncio.sleep(delay_sec)
```

---

## 🚀 实施优先级

### 本周（P0）
1. ✅ 增强遥测数据（机械臂和夹爪）
2. ✅ 工作空间边界检查
3. ✅ 运动限制验证

### 下周（P1）
4. 直线运动支持（干运行）
5. 错误处理和自动重连
6. 状态一致性检测

### 两周后（P2）
7. 完善模拟器运动时间
8. 自碰撞检测
9. 结构化日志和回放工具

---

## 📊 测试策略

### 单元测试
- 每个新模块都有对应的单元测试
- 测试覆盖率目标：> 80%

### 集成测试
- 使用干运行适配器测试完整流程
- 模拟各种错误场景

### 硬件测试（最后阶段）
- 在安全环境下测试真实硬件
- 验证遥测数据准确性
- 验证安全机制有效性

---

## 📝 文档更新

每个阶段完成后更新：
- API 文档
- 配置示例
- 故障排除指南
- 测试报告

---

## ⚠️ 安全注意事项

1. **所有新功能先在干运行模式测试**
2. **硬件测试必须清空工作区**
3. **保持物理急停可达**
4. **新增安全检查不能降低现有安全级别**
5. **所有运动命令必须经过多层验证**
