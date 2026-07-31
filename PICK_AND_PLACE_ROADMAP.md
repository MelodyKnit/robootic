# 视觉引导抓取系统实施方案

## 项目目标

实现一个完整的视觉引导机械臂抓取系统：
1. 识别画面中的物品（位置、姿态、类别）
2. 计算抓取点和抓取姿态
3. 控制JAKA机械臂和夹爪执行抓取
4. 将物品放置到指定位置

---

## 系统架构

### 模块划分

```
┌─────────────────────────────────────────────────────────┐
│                     Web UI 控制界面                        │
│  [相机视图] [物体列表] [任务控制] [状态监控]                 │
└─────────────────────────────────────────────────────────┘
                            ↕
┌─────────────────────────────────────────────────────────┐
│              任务执行引擎 (Task Executor)                  │
│    状态机管理 | 任务队列 | 错误处理 | 日志                  │
└─────────────────────────────────────────────────────────┘
         ↓                  ↓                  ↓
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  视觉模块     │   │  规划模块     │   │  控制模块     │
│  • 物体检测   │   │  • 抓取规划   │   │  • 机械臂     │
│  • 姿态估计   │   │  • 轨迹生成   │   │  • 夹爪       │
│  • 坐标转换   │   │  • 碰撞检测   │   │  • 安全监控   │
└──────────────┘   └──────────────┘   └──────────────┘
       ↓                                       ↓
  [Camera]                              [JAKA Robot]
```

---

## 工作流程

### 完整抓取流程

```
[开始] → [初始化系统] → [等待任务]
                           ↓
                      [触发抓取]
                           ↓
                   ┌───────────────┐
                   │  1. 视觉检测   │
                   │  • 拍摄图像   │
                   │  • 物体检测   │
                   │  • 姿态估计   │
                   │  • 坐标转换   │
                   └───────────────┘
                           ↓
                   ┌───────────────┐
                   │  2. 抓取规划   │
                   │  • 选择目标   │
                   │  • 计算抓取点 │
                   │  • 生成轨迹   │
                   │  • 碰撞检测   │
                   └───────────────┘
                           ↓
                   ┌───────────────┐
                   │  3. 移动到目标 │
                   │  • 移动到预抓 │
                   │  • 下降到物体 │
                   │  • 调整姿态   │
                   └───────────────┘
                           ↓
                   ┌───────────────┐
                   │  4. 执行抓取   │
                   │  • 闭合夹爪   │
                   │  • 检查力反馈 │
                   │  • 提升物体   │
                   └───────────────┘
                           ↓
                   ┌───────────────┐
                   │  5. 放置物品   │
                   │  • 移动到放置点│
                   │  • 下降        │
                   │  • 打开夹爪   │
                   │  • 撤离        │
                   └───────────────┘
                           ↓
                      [完成] ← [记录日志]
                           ↓
                      [返回Home]
```

### 状态机设计

```
IDLE (空闲)
  ↓ [启动任务]
DETECTING (检测中)
  ↓ [检测成功]
PLANNING (规划中)
  ↓ [规划成功]
APPROACHING (接近中)
  ↓ [到达预抓位置]
GRASPING (抓取中)
  ↓ [抓取成功]
LIFTING (提升中)
  ↓ [提升完成]
TRANSPORTING (运输中)
  ↓ [到达放置点]
PLACING (放置中)
  ↓ [放置完成]
RETURNING (返回中)
  ↓ [返回完成]
COMPLETED (完成)
  ↓
IDLE

ERROR (错误)
  → [错误恢复] → IDLE
```

---

## 详细实施计划

### 阶段 1: 坐标系统与标定 (1-2周)

#### 目标
建立从像素坐标到机器人基坐标的完整转换链。

#### 任务清单

**1.1 相机内参标定**
- [ ] 准备棋盘格标定板 (建议 9x6, 25mm方格)
- [ ] 实现标定数据采集工具
- [ ] 实现 Zhang's 标定算法 (或使用 OpenCV)
- [ ] 验证标定精度 (重投影误差 < 0.5 像素)
- [ ] 持久化标定参数

**1.2 手眼标定 (Eye-to-Hand)**
- [ ] 实现 AX=XB 求解算法
- [ ] 采集标定数据 (至少 10 组不同姿态)
- [ ] 计算相机相对机器人基座的变换矩阵
- [ ] 验证标定精度 (误差 < 2mm)

**1.3 坐标转换模块**
```python
# src/gripper_ai_controller/coordination/transformer.py

class CoordinateTransformer:
    """统一坐标转换接口"""
    
    def pixel_to_camera(self, px, py, depth_mm):
        """像素坐标 → 相机坐标"""
        
    def camera_to_robot_base(self, point_camera):
        """相机坐标 → 机器人基坐标"""
        
    def pixel_to_robot_base(self, px, py, depth_mm):
        """像素坐标 → 机器人基坐标 (组合调用)"""
        
    def robot_base_to_tcp(self, point_base):
        """机器人基坐标 → TCP坐标"""
```

**文件结构**:
```
src/gripper_ai_controller/coordination/
├── __init__.py
├── transformer.py          # 坐标转换核心
├── calibration.py          # 标定算法
├── models.py               # 坐标系数据结构
└── validation.py           # 转换精度验证
```

---

### 阶段 2: 抓取规划器 (2-3周)

#### 目标
根据物体检测结果生成可执行的抓取计划。

#### 任务清单

**2.1 抓取点生成**
- [ ] 基于物体中心的简单抓取点
- [ ] 基于物体长轴方向的定向抓取
- [ ] 基于轮廓特征的多候选抓取点
- [ ] 抓取质量评分 (稳定性、可达性)

**2.2 夹爪配置计算**
- [ ] 根据物体宽度计算夹爪开口
- [ ] 计算抓取力 (基于物体重量/材质)
- [ ] 碰撞检测 (夹爪与环境)

**2.3 接近轨迹规划**
- [ ] 垂直接近策略 (Z轴向下)
- [ ] 斜向接近策略 (避障)
- [ ] 预抓位置计算 (目标上方 50-100mm)
- [ ] 撤离轨迹规划

**2.4 放置点管理**
- [ ] 配置多个固定放置区域
- [ ] 动态选择空闲放置位置
- [ ] 避免放置区域碰撞

**核心类设计**:
```python
# src/gripper_ai_controller/planning/grasp_planner.py

class GraspPlanner:
    """抓取规划器"""
    
    def plan_grasp(self, detected_object):
        """
        输入: DetectedObject (位置、姿态、类别、置信度)
        输出: GraspPlan (抓取点、接近轨迹、夹爪配置)
        """
        
    def evaluate_grasp(self, grasp_pose):
        """评估抓取质量 (0-1)"""
        
    def check_reachability(self, target_pose):
        """检查机械臂可达性"""

class TrajectoryPlanner:
    """轨迹规划器"""
    
    def plan_approach(self, current_pose, target_pose):
        """规划接近轨迹"""
        
    def plan_retreat(self, current_pose):
        """规划撤离轨迹"""
        
    def check_collision(self, trajectory):
        """碰撞检测"""
```

**文件结构**:
```
src/gripper_ai_controller/planning/
├── __init__.py
├── grasp_planner.py        # 抓取规划
├── trajectory_planner.py   # 轨迹规划
├── collision_checker.py    # 碰撞检测
├── models.py               # 规划数据结构
└── config.py               # 规划参数配置
```

---

### 阶段 3: 任务执行引擎 (2-3周)

#### 目标
实现自动化任务执行、状态管理、错误恢复。

#### 任务清单

**3.1 状态机实现**
- [ ] 定义所有状态和转换条件
- [ ] 实现状态切换逻辑
- [ ] 状态持久化 (断电恢复)
- [ ] 状态日志记录

**3.2 任务队列**
- [ ] 任务FIFO队列
- [ ] 任务优先级
- [ ] 任务取消/暂停
- [ ] 批量任务执行

**3.3 异常处理**
- [ ] 检测失败 → 重试或跳过
- [ ] 规划失败 → 调整参数重新规划
- [ ] 抓取失败 → 重新抓取 (最多3次)
- [ ] 碰撞检测 → 紧急停止
- [ ] 超时保护

**3.4 安全监控**
- [ ] 工作空间边界检查
- [ ] 速度和加速度限制
- [ ] 力觉反馈监控
- [ ] 急停按钮集成

**核心类设计**:
```python
# src/gripper_ai_controller/execution/task_executor.py

class TaskExecutor:
    """任务执行器"""
    
    def __init__(self, vision_module, planner, robot_controller):
        self.vision = vision_module
        self.planner = planner
        self.robot = robot_controller
        self.state = TaskState.IDLE
        self.task_queue = asyncio.Queue()
        
    async def execute_pick_and_place(self, target_id=None):
        """执行一次完整的拾取-放置任务"""
        try:
            # 1. 检测物体
            self.state = TaskState.DETECTING
            objects = await self.vision.detect_objects()
            if not objects:
                raise NoObjectDetectedError()
            
            # 2. 选择目标
            target = self._select_target(objects, target_id)
            
            # 3. 规划抓取
            self.state = TaskState.PLANNING
            grasp_plan = self.planner.plan_grasp(target)
            
            # 4. 执行抓取
            self.state = TaskState.APPROACHING
            await self.robot.move_to(grasp_plan.pre_grasp_pose)
            
            self.state = TaskState.GRASPING
            await self.robot.execute_grasp(grasp_plan)
            
            # 5. 放置物体
            self.state = TaskState.TRANSPORTING
            await self.robot.move_to(self.config.place_pose)
            
            self.state = TaskState.PLACING
            await self.robot.release()
            
            # 6. 返回home
            self.state = TaskState.RETURNING
            await self.robot.move_to_home()
            
            self.state = TaskState.COMPLETED
            
        except Exception as e:
            self.state = TaskState.ERROR
            await self._handle_error(e)
            
    async def _handle_error(self, error):
        """错误处理和恢复"""
        
    def _select_target(self, objects, target_id):
        """选择抓取目标"""
```

**文件结构**:
```
src/gripper_ai_controller/execution/
├── __init__.py
├── task_executor.py        # 任务执行器
├── state_machine.py        # 状态机
├── error_handler.py        # 错误处理
├── safety_monitor.py       # 安全监控
└── models.py               # 执行相关数据结构
```

---

### 阶段 4: Web UI 集成 (1-2周)

#### 目标
提供友好的图形界面控制和监控抓取任务。

#### 任务清单

**4.1 任务控制面板**
- [ ] 开始/停止按钮
- [ ] 目标物体选择器
- [ ] 放置位置配置
- [ ] 参数调整 (速度、力度等)

**4.2 状态可视化**
- [ ] 当前状态显示
- [ ] 实时轨迹预览
- [ ] 抓取成功率统计
- [ ] 错误日志查看

**4.3 相机视图增强**
- [ ] 检测框和抓取点标注
- [ ] 坐标系可视化
- [ ] 轨迹叠加显示

**4.4 调试工具**
- [ ] 手动示教模式
- [ ] 单步执行
- [ ] 坐标转换测试
- [ ] 标定精度验证

**新增组件**:
```
src/web/src/components/
├── PickAndPlacePanel.vue       # 主控制面板
├── ObjectSelector.vue          # 物体选择器
├── GraspVisualization.vue      # 抓取可视化
├── TaskStatusMonitor.vue       # 状态监控
└── SafetyOverlay.vue           # 安全区域显示
```

---

### 阶段 5: 测试与优化 (1-2周)

#### 测试清单

**5.1 单元测试**
- [ ] 坐标转换精度测试
- [ ] 抓取规划算法测试
- [ ] 状态机逻辑测试
- [ ] 碰撞检测测试

**5.2 集成测试**
- [ ] 完整流程测试 (10次成功)
- [ ] 边界情况测试
- [ ] 异常恢复测试
- [ ] 长时间运行测试

**5.3 性能优化**
- [ ] 检测延迟 < 500ms
- [ ] 规划时间 < 1s
- [ ] 抓取成功率 > 80%
- [ ] 整体周期 < 20s

---

## 配置文件示例

### pick_and_place.json

```json
{
  "camera": {
    "camera_id": "hikvision-usb",
    "calibration_id": "hand-eye-calibrated"
  },
  "robot": {
    "ip": "192.168.1.100",
    "port": 10000,
    "home_pose": {
      "x": 300,
      "y": 0,
      "z": 400,
      "rx": 180,
      "ry": 0,
      "rz": 0
    }
  },
  "gripper": {
    "max_width_mm": 100,
    "grasp_force_percent": 30,
    "speed_percent": 50
  },
  "workspace": {
    "min_x": 100,
    "max_x": 500,
    "min_y": -300,
    "max_y": 300,
    "min_z": 0,
    "max_z": 500
  },
  "pick_zone": {
    "center_x": 300,
    "center_y": 0,
    "radius": 200
  },
  "place_zones": [
    {
      "zone_id": "zone_1",
      "pose": {
        "x": 400,
        "y": -200,
        "z": 100,
        "rx": 180,
        "ry": 0,
        "rz": 0
      }
    },
    {
      "zone_id": "zone_2",
      "pose": {
        "x": 400,
        "y": 200,
        "z": 100,
        "rx": 180,
        "ry": 0,
        "rz": 0
      }
    }
  ],
  "motion_planning": {
    "approach_height_mm": 100,
    "retreat_height_mm": 150,
    "linear_speed_mm_s": 50,
    "joint_speed_deg_s": 30,
    "acceleration": 50
  },
  "safety": {
    "max_velocity": 100,
    "max_force_n": 30,
    "collision_threshold": 10,
    "emergency_stop_enabled": true
  },
  "task_execution": {
    "max_retries": 3,
    "detection_timeout_s": 10,
    "grasp_timeout_s": 30,
    "task_timeout_s": 120
  }
}
```

---

## API 设计

### 新增 REST API

```python
# 任务控制
POST   /api/tasks/pick-and-place/start     # 开始抓取任务
POST   /api/tasks/pick-and-place/stop      # 停止当前任务
GET    /api/tasks/pick-and-place/status    # 获取任务状态

# 目标管理
GET    /api/objects/detected               # 获取检测到的物体列表
POST   /api/objects/{object_id}/select     # 选择抓取目标

# 配置管理
GET    /api/pick-and-place/config          # 获取配置
PUT    /api/pick-and-place/config          # 更新配置
POST   /api/pick-and-place/config/validate # 验证配置

# 标定工具
POST   /api/calibration/capture            # 采集标定数据
POST   /api/calibration/compute            # 计算标定结果
GET    /api/calibration/validate           # 验证标定精度

# 调试工具
POST   /api/debug/coordinate/transform     # 测试坐标转换
POST   /api/debug/grasp/plan               # 测试抓取规划
POST   /api/debug/motion/simulate          # 模拟运动轨迹
```

---

## 开发优先级

### P0 (核心功能，必须实现)

1. ✅ 物体检测 (已有 YOLOv8/Object Pose)
2. ⚠️ 坐标转换 (需要标定)
3. ⚠️ 基本抓取规划 (简单垂直抓取)
4. ⚠️ 机械臂运动控制 (已有 JAKA adapter)
5. ⚠️ 任务执行引擎 (状态机)

### P1 (重要功能，提升成功率)

6. 轨迹规划 (避障)
7. 碰撞检测
8. 错误处理和重试
9. Web UI 控制面板
10. 抓取质量评估

### P2 (优化功能，改善体验)

11. 多候选抓取点
12. 定向抓取 (基于姿态)
13. 实时轨迹可视化
14. 性能统计和分析
15. 调试工具集

---

## 当前项目状态

### 已完成 ✅
- 相机采集和预览
- 物体检测 (YOLOv8)
- 物体姿态识别 (Object Pose)
- 机械臂基础控制 (JAKA adapter)
- 夹爪控制 (PGI adapter)
- Web UI 框架
- 录制和截图功能

### 待实现 ⚠️
- 手眼标定
- 坐标转换模块
- 抓取规划器
- 轨迹规划器
- 任务执行引擎
- Pick & Place UI

### 缺失的硬件信息 ❓
- 相机到机器人基座的物理距离和角度
- 工作台高度
- 实际工作空间范围
- 夹爪最大开口和闭合力

---

## 下一步行动

### 立即可做 (本周)

1. **验证现有功能**
   ```bash
   # 测试物体检测
   poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json
   
   # 测试JAKA控制 (只读模式)
   # 在Web UI中查看机械臂状态
   ```

2. **准备标定工具**
   - 打印棋盘格标定板 (9x6, 25mm方格)
   - 测量相机到机器人的大致位置

3. **创建配置文件**
   - 复制 `pick_and_place.example.json` 
   - 填写实际的机器人IP、工作空间参数

### 本月目标

1. **完成手眼标定** (Week 1-2)
   - 实现标定数据采集工具
   - 完成相机内参和手眼标定
   - 验证转换精度

2. **实现基础抓取** (Week 3-4)
   - 实现简单的垂直抓取规划
   - 集成到任务执行引擎
   - 完成第一次成功抓取测试

3. **开发控制界面** (Week 4)
   - 创建 Pick & Place 控制面板
   - 集成到现有 Web UI
   - 添加状态监控和日志

### 三个月目标

- 抓取成功率 > 80%
- 支持 3 种以上物体类型
- 完整的错误处理和恢复
- 生产环境部署就绪

---

## 参考资料

### 相关论文
- **Grasp Quality Metrics**: "Grasp Quality Measures" (Ferrari & Canny)
- **Hand-Eye Calibration**: "A New Technique for Fully Autonomous and Efficient 3D Robotics Hand/Eye Calibration" (Tsai & Lenz)
- **Trajectory Planning**: "Real-time trajectory planning for robotic manipulators in dynamic environments" 

### 开源项目
- **MoveIt**: ROS motion planning framework
- **GraspIt!**: Grasp planning simulator
- **Easy Hand-Eye**: 手眼标定工具库

### 相关库
- **OpenCV**: 计算机视觉
- **SciPy**: 优化算法 (标定、IK求解)
- **NumPy**: 矩阵运算
- **Transforms3d**: 3D变换库

---

## 附录：关键算法

### A. 手眼标定 (AX=XB)

```python
def calibrate_hand_eye(robot_poses, camera_poses):
    """
    求解 robot_base -> camera 的变换矩阵
    
    输入:
        robot_poses: List[(R, t)] 机械臂TCP相对基座的位姿
        camera_poses: List[(R, t)] 标定板相对相机的位姿
    
    输出:
        (R_cam2base, t_cam2base) 相机相对基座的变换
    """
    # 使用 Tsai-Lenz 方法或 Park-Martin 方法
    pass
```

### B. 抓取点评分

```python
def score_grasp(grasp_pose, object_geometry):
    """
    评估抓取质量 (0-1)
    
    考虑因素:
    - 力闭合性 (Force Closure)
    - 可达性 (Reachability)
    - 碰撞风险 (Collision Risk)
    - 稳定性 (Stability)
    """
    score = 0.0
    
    # 力闭合性检查
    if check_force_closure(grasp_pose, object_geometry):
        score += 0.4
    
    # 可达性检查
    if check_reachability(grasp_pose):
        score += 0.3
    
    # 碰撞检查
    if not check_collision(grasp_pose):
        score += 0.2
    
    # 稳定性检查
    score += compute_stability(grasp_pose) * 0.1
    
    return score
```

### C. 轨迹插值

```python
def interpolate_trajectory(start_pose, end_pose, num_points=50):
    """
    笛卡尔空间直线插值
    
    使用 SLERP (Spherical Linear Interpolation) 插值旋转
    使用 LERP (Linear Interpolation) 插值平移
    """
    trajectory = []
    for i in range(num_points):
        t = i / (num_points - 1)
        
        # 插值位置
        pos = start_pose.position * (1 - t) + end_pose.position * t
        
        # 插值旋转 (四元数SLERP)
        rot = slerp(start_pose.rotation, end_pose.rotation, t)
        
        trajectory.append(Pose(pos, rot))
    
    return trajectory
```

---

## 结语

这个路线图提供了一个系统化的实施方案。建议按阶段逐步实现，每个阶段都进行充分测试后再进入下一阶段。

**关键成功因素**:
1. **精确的标定** - 这是基础，标定精度直接影响抓取成功率
2. **鲁棒的错误处理** - 工业环境中异常情况频繁，必须有完善的恢复机制
3. **安全第一** - 在任何情况下都要优先保证人员和设备安全
4. **迭代优化** - 先实现基本功能,再逐步优化性能

有任何问题随时询问！
