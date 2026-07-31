# 视觉引导抓取系统 - 快速启动指南

## 📋 项目现状

### ✅ 已完成的功能
- [x] 相机采集和实时预览
- [x] 物体检测 (YOLOv8 - 80类COCO)
- [x] 物体姿态识别 (基于轮廓)
- [x] 机械臂状态读取 (JAKA)
- [x] 夹爪控制 (PGI)
- [x] Web UI 界面
- [x] 录制和截图功能

### ⚠️ 待实现的核心功能
- [ ] **手眼标定** (相机-机器人坐标系关联)
- [ ] **坐标转换** (像素→机器人坐标)
- [ ] **抓取规划** (计算抓取点和轨迹)
- [ ] **任务执行引擎** (自动化流程控制)
- [ ] **Pick & Place UI** (任务控制界面)

---

## 🎯 实现抓取功能的路线图

### 阶段 1: 坐标系统 (1-2周) - **最优先**
这是实现抓取的**必要前提**，没有标定就无法将像素坐标转换为机器人坐标。

**需要做的**:
1. 打印棋盘格标定板 (推荐 9x6, 25mm方格)
2. 实现相机内参标定
3. 实现手眼标定 (Eye-to-Hand)
4. 验证转换精度

**预期输出**:
- 相机内参矩阵
- 相机到机器人基座的变换矩阵
- 坐标转换函数 (测试精度 < 2mm)

### 阶段 2: 抓取规划 (2周)
有了坐标转换后，可以开发抓取规划器。

**需要做的**:
1. 简单垂直抓取规划
2. 接近和撤离轨迹生成
3. 夹爪宽度计算
4. 基本碰撞检测

**预期输出**:
- 给定物体位置 → 输出抓取计划
- 包含：预抓位置、抓取位置、夹爪开口

### 阶段 3: 任务执行 (2周)
将所有模块串联起来。

**需要做的**:
1. 状态机实现
2. 完整流程集成
3. 错误处理
4. 安全监控

**预期输出**:
- 一键执行完整抓取流程
- 成功率 > 60%

### 阶段 4: UI 和优化 (1周)
提升用户体验和成功率。

**需要做的**:
1. Pick & Place 控制面板
2. 实时状态显示
3. 参数调优
4. 成功率提升到 80%+

---

## 🚀 第一步：准备工作

### 硬件准备清单

- [ ] **Hikvision 相机**
  - 已连接并能采集图像
  - USB供电稳定
  - 焦距已调整
  
- [ ] **JAKA 机械臂**
  - 网络连接正常 (IP: `______`)
  - 能读取状态数据
  - 示教器可用
  
- [ ] **PGI 夹爪**
  - 已安装在机械臂末端
  - 网络连接正常 (IP: `______`)
  - 最大开口: `____mm`
  
- [ ] **工作台**
  - 平整稳固
  - 高度: `____mm`
  - 背景颜色: `______`
  
- [ ] **标定板**
  - 打印 9x6 棋盘格
  - 方格大小: 25mm
  - 粘贴在刚性平面上

### 软件准备清单

- [ ] 项目代码已克隆
- [ ] Python 环境已配置 (Poetry)
- [ ] 依赖已安装
- [ ] 配置文件已创建
- [ ] 能正常启动 Web 服务

---

## 📝 当前可以测试的功能

### 1. 视觉识别测试

```bash
# 启动服务
poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json

# 浏览器打开
http://127.0.0.1:8000
```

**测试项目**:
- [ ] 相机画面正常显示
- [ ] 物体检测框出现 (YOLOv8)
- [ ] 物体轮廓显示 (Object Pose)
- [ ] 置信度阈值调整生效
- [ ] 截图功能正常
- [ ] 录制功能正常

**如果检测不到物体**:
1. 降低置信度阈值 (在配置文件中改为 0.15-0.25)
2. 开启自动曝光
3. 调整光照，减少反光
4. 查看 `DETECTION_POSE_SETUP_GUIDE.md`

### 2. 机械臂状态监控

```bash
# 使用包含JAKA配置的文件
poetry run gripper-ai-controller web --config-file localstore/hikvision-jaka-web.local.json
```

**前提条件**:
- JAKA 机械臂已上电
- 网络连接正常
- IP地址已配置正确

**测试项目**:
- [ ] Web UI 中能看到机械臂状态
- [ ] 关节角度实时更新
- [ ] TCP位置显示
- [ ] 错误信息正常显示

**⚠️ 注意**: 当前只读模式，不会发送运动命令

### 3. 夹爪控制测试

**测试项目**:
- [ ] 能读取夹爪状态
- [ ] 能发送开合命令 (⚠️ 需要启用控制)
- [ ] 力反馈正常

---

## 🔧 下一步开发任务

### 任务 1: 相机内参标定 (优先级: P0)

**目标**: 获得相机内参矩阵和畸变系数

**步骤**:
1. 创建标定数据采集工具
2. 采集 15-20 张不同角度的棋盘格图像
3. 使用 OpenCV `calibrateCamera` 计算内参
4. 验证重投影误差 < 0.5 像素

**预计时间**: 2-3天

**输出**:
```json
{
  "camera_matrix": {
    "fx": 1000.0,
    "fy": 1000.0,
    "cx": 960.0,
    "cy": 540.0
  },
  "distortion": [k1, k2, p1, p2, k3],
  "image_size": [1920, 1080],
  "reprojection_error": 0.3
}
```

### 任务 2: 手眼标定 (优先级: P0)

**目标**: 获得相机相对机器人基座的变换矩阵

**步骤**:
1. 将标定板固定在工作台上
2. 使用示教器移动机械臂到 10+ 个不同位置
3. 每个位置记录：
   - 机械臂 TCP 位姿 (相对基座)
   - 标定板在相机中的位姿
4. 求解 AX=XB 方程
5. 验证转换精度 < 2mm

**预计时间**: 3-5天

**输出**:
```json
{
  "camera_to_base": {
    "rotation": [[r11, r12, r13], [r21, r22, r23], [r31, r32, r33]],
    "translation": [tx, ty, tz]
  },
  "calibration_error": 1.5,
  "num_samples": 12
}
```

### 任务 3: 坐标转换模块 (优先级: P0)

**目标**: 实现像素→机器人坐标的完整转换链

**代码框架**:
```python
# src/gripper_ai_controller/coordination/transformer.py

class CoordinateTransformer:
    def __init__(self, camera_params, hand_eye_transform):
        self.K = camera_params['camera_matrix']
        self.dist = camera_params['distortion']
        self.T_cam_to_base = hand_eye_transform
        
    def pixel_to_robot_base(self, px, py, depth_mm):
        """
        像素坐标 + 深度 → 机器人基坐标
        
        Args:
            px, py: 像素坐标
            depth_mm: 物体到相机的距离(mm)
            
        Returns:
            (x, y, z): 机器人基坐标系下的位置(mm)
        """
        # 1. 像素 → 相机坐标
        point_cam = self._pixel_to_camera(px, py, depth_mm)
        
        # 2. 相机坐标 → 机器人基坐标
        point_base = self._camera_to_base(point_cam)
        
        return point_base
```

**预计时间**: 2天

**测试方法**:
1. 在已知位置放置物体
2. 通过相机检测获得像素坐标
3. 转换为机器人坐标
4. 使用机械臂移动到该位置
5. 验证误差 < 5mm

### 任务 4: 简单抓取规划 (优先级: P0)

**目标**: 给定物体位置，生成抓取计划

**代码框架**:
```python
# src/gripper_ai_controller/planning/grasp_planner.py

class SimpleGraspPlanner:
    def plan_vertical_grasp(self, object_pose):
        """
        简单垂直抓取规划
        
        Args:
            object_pose: 物体的位置和姿态
            
        Returns:
            GraspPlan: 包含预抓、抓取、撤离位姿
        """
        # 1. 计算抓取点 (物体中心)
        grasp_point = object_pose.center
        
        # 2. 计算预抓位置 (上方 100mm)
        pre_grasp_pose = Pose(
            x=grasp_point.x,
            y=grasp_point.y,
            z=grasp_point.z + 100,
            rx=180, ry=0, rz=0  # 垂直向下
        )
        
        # 3. 计算抓取位置
        grasp_pose = Pose(
            x=grasp_point.x,
            y=grasp_point.y,
            z=grasp_point.z + 10,  # 略高于物体表面
            rx=180, ry=0, rz=0
        )
        
        # 4. 计算夹爪开口
        gripper_width = object_pose.width + 10  # 留 5mm 余量
        
        return GraspPlan(
            pre_grasp_pose=pre_grasp_pose,
            grasp_pose=grasp_pose,
            gripper_width=gripper_width
        )
```

**预计时间**: 3-4天

### 任务 5: 任务执行引擎 (优先级: P0)

**目标**: 自动化执行完整抓取流程

**代码框架**:
```python
# src/gripper_ai_controller/execution/task_executor.py

class PickAndPlaceExecutor:
    async def execute(self):
        """执行一次拾取-放置任务"""
        
        # 1. 检测物体
        objects = await self.vision.detect()
        target = self.select_target(objects)
        
        # 2. 规划抓取
        grasp_plan = self.planner.plan(target)
        
        # 3. 移动到预抓位置
        await self.robot.move_to(grasp_plan.pre_grasp_pose)
        
        # 4. 下降到抓取位置
        await self.robot.move_linear(grasp_plan.grasp_pose)
        
        # 5. 闭合夹爪
        await self.gripper.grasp(grasp_plan.gripper_width, force=30)
        
        # 6. 提升
        await self.robot.move_linear(grasp_plan.lift_pose)
        
        # 7. 移动到放置点
        await self.robot.move_to(self.place_pose)
        
        # 8. 打开夹爪
        await self.gripper.release()
        
        # 9. 返回home
        await self.robot.move_to_home()
```

**预计时间**: 5-7天

---

## 📊 关键参数需要测量

在开始开发前，请测量以下参数：

### 相机参数
- [ ] 分辨率: `_______ x _______`
- [ ] 焦距: `_______ mm`
- [ ] 视场角: `_______ 度`
- [ ] 到工作台距离: `_______ mm`

### 机械臂参数
- [ ] IP地址: `_______`
- [ ] TCP到基座的Home位置: `(x:___, y:___, z:___)`
- [ ] 最大到达距离: `_______ mm`
- [ ] 工作空间范围:
  - X: `___ ~ ___ mm`
  - Y: `___ ~ ___ mm`
  - Z: `___ ~ ___ mm`

### 夹爪参数
- [ ] IP地址: `_______`
- [ ] 最大开口: `_______ mm`
- [ ] 最大夹持力: `_______ N`
- [ ] 指尖宽度: `_______ mm`

### 工作台参数
- [ ] 高度 (相对机器人基座): `_______ mm`
- [ ] 拾取区域中心: `(x:___, y:___)`
- [ ] 放置区域位置: `(x:___, y:___, z:___)`

---

## 🎓 学习资源

### 必读文档
1. `PICK_AND_PLACE_ROADMAP.md` - 完整实施方案
2. `DETECTION_POSE_SETUP_GUIDE.md` - 视觉识别配置
3. `RECORDING_INTEGRATION_GUIDE.md` - 录制功能使用

### 推荐教程
- **相机标定**: OpenCV 官方教程
- **手眼标定**: easy_handeye ROS包文档
- **机器人运动学**: Modern Robotics (Lynch & Park)
- **抓取规划**: Grasp Planning textbook

---

## 💡 常见问题

### Q1: 为什么必须先做标定？
**A**: 没有标定，相机看到的像素坐标无法转换为机器人坐标，机械臂不知道往哪里移动。

### Q2: 标定精度要求多高？
**A**: 建议 < 2mm。如果误差太大，机械臂可能抓不到物体或发生碰撞。

### Q3: 可以跳过标定直接手动示教吗？
**A**: 可以作为临时测试，但无法实现自动化。每次物体位置变化都需要重新示教。

### Q4: 估算深度信息怎么获得？
**A**: 
- 方法1: 假设物体在已知高度的平面上
- 方法2: 使用深度相机
- 方法3: 双目视觉
- 当前项目使用方法1（平面假设）

### Q5: 开发周期大概多久？
**A**: 
- 最小可用版本 (MVP): 2-3周
- 生产就绪版本: 2-3个月
- 持续优化: 持续进行

---

## 🔗 相关文档链接

- [完整路线图](PICK_AND_PLACE_ROADMAP.md)
- [视觉识别配置](DETECTION_POSE_SETUP_GUIDE.md)
- [录制功能集成](RECORDING_INTEGRATION_GUIDE.md)
- [工作总结](SESSION_SUMMARY.md)

---

## ✅ 准备就绪检查

在开始开发抓取功能前，确认以下项目：

**硬件**:
- [ ] 相机能正常采集图像
- [ ] 机械臂能读取状态
- [ ] 夹爪能控制开合
- [ ] 工作台已固定
- [ ] 标定板已准备

**软件**:
- [ ] 代码能正常运行
- [ ] 物体检测功能正常
- [ ] 配置文件已创建
- [ ] 已阅读路线图文档

**知识**:
- [ ] 理解坐标系转换原理
- [ ] 了解标定流程
- [ ] 熟悉机械臂运动控制
- [ ] 掌握基本的机器人学

**准备好了吗？开始第一步：相机内参标定！** 🚀
