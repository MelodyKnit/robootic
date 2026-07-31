# 视觉部分完善计划

## 📊 当前视觉系统状态评估

### ✅ 已完成的模块 (80%)

#### 1. **相机采集** - 95% 完成
**模块**: `adapters/hikvision/`
- ✓ 海康威视工业相机适配器
- ✓ 实时图像采集 (RGB8/Mono8)
- ✓ MJPEG 流预览
- ✓ 相机参数控制（曝光、增益、帧率）
- ✓ 设备选择和热切换
- ✓ 断线重连机制

**缺失功能**:
- ⚠️ 相机内参标定工具（仅有框架）
- ⚠️ 标定数据持久化和加载
- ⚠️ 畸变校正应用

#### 2. **物体检测** - 85% 完成
**模块**: `object_detection/`
- ✓ YOLOv8 ONNX 推理
- ✓ Faster R-CNN 支持
- ✓ COCO 80类检测
- ✓ 置信度过滤
- ✓ 二维边界框输出
- ✓ 模型热切换
- ✓ GPU/CPU 推理

**缺失功能**:
- ⚠️ 自定义类别训练流程
- ⚠️ 检测结果时序平滑
- ⚠️ 多目标跟踪（ID保持）
- ⚠️ 遮挡处理

#### 3. **物体姿态估计** - 75% 完成
**模块**: `object_pose/`
- ✓ 基于轮廓的平面工件识别
- ✓ X/Y/Yaw 估算（台面约束）
- ✓ 工件档案管理
- ✓ 背景差分

**缺失功能**:
- ❌ **完整的手眼标定流程**（最关键）
- ❌ **像素坐标→机器人坐标转换**
- ⚠️ Z轴精确测量（当前只是推导）
- ⚠️ 多工件场景处理
- ⚠️ 3D姿态估计

#### 4. **人体姿态跟踪** - 90% 完成
**模块**: `pose/`
- ✓ COCO 17关节点检测
- ✓ 单人选择和跟踪
- ✓ 关节置信度过滤
- ✓ 2D运动状态估计
- ✓ GPU加速推理

**缺失功能**:
- ⚠️ 多人跟踪
- ⚠️ 身份重识别
- ⚠️ 3D姿态恢复

#### 5. **图像质量分析** - 95% 完成
**模块**: `vision/`
- ✓ 亮度/对比度/清晰度评估
- ✓ 帧健康检查
- ✓ 离线评测工具
- ✓ 关节可见性评估

**缺失功能**:
- ⚠️ 自动曝光建议
- ⚠️ 光照变化适应

---

## 🎯 完善优先级路线图

### **阶段 1: 相机标定系统** (1-2周) - **最高优先级 P0**

> 这是实现抓取功能的**必要前提**，没有标定就无法将像素坐标转换为机器人坐标。

#### 任务 1.1: 相机内参标定
**目标**: 获得相机内参矩阵和畸变系数

**实施步骤**:
```python
# 新增模块: src/gripper_ai_controller/calibration/intrinsic.py

class IntrinsicCalibration:
    """相机内参标定器"""
    
    def __init__(self, board_size=(9, 6), square_size_mm=25.0):
        self.board_size = board_size
        self.square_size_mm = square_size_mm
        self.object_points = self._generate_object_points()
    
    def capture_calibration_images(
        self, 
        vision_adapter, 
        num_images=20,
        capture_interval_sec=2.0
    ):
        """采集标定图像"""
        images = []
        for i in range(num_images):
            frame = await vision_adapter.capture()
            if self._detect_chessboard(frame):
                images.append(frame)
                print(f"✓ 采集第 {i+1}/{num_images} 张标定图像")
            await asyncio.sleep(capture_interval_sec)
        return images
    
    def calibrate(self, images):
        """执行标定计算"""
        image_points = []
        for img in images:
            corners = self._find_chessboard_corners(img)
            if corners is not None:
                image_points.append(corners)
        
        # OpenCV 标定
        ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
            [self.object_points] * len(image_points),
            image_points,
            (img.width, img.height),
            None, None
        )
        
        return CalibrationResult(
            camera_matrix=camera_matrix,
            distortion_coeffs=dist_coeffs,
            reprojection_error=ret,
            num_images=len(image_points)
        )
    
    def save_calibration(self, result, filepath):
        """保存标定结果到JSON"""
        data = {
            "camera_matrix": {
                "fx": result.camera_matrix[0, 0],
                "fy": result.camera_matrix[1, 1],
                "cx": result.camera_matrix[0, 2],
                "cy": result.camera_matrix[1, 2],
            },
            "distortion_coeffs": result.distortion_coeffs.tolist(),
            "image_size": [result.width, result.height],
            "reprojection_error": result.reprojection_error,
            "calibration_date": datetime.now().isoformat()
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
```

**CLI命令**:
```bash
# 生成棋盘格标定板
scripts\run.bat calibration-generate-board --output temp/chessboard_9x6_25mm.png

# 采集标定图像
scripts\run.bat calibration-capture-intrinsic \
  --config localstore/camera.json \
  --num-images 20 \
  --output-dir localstore/calibration/intrinsic

# 执行标定计算
scripts\run.bat calibration-compute-intrinsic \
  --images-dir localstore/calibration/intrinsic \
  --output localstore/calibration/camera_intrinsic.json
```

**验证标准**:
- 重投影误差 < 0.5 像素
- 至少使用 15-20 张不同角度的图像
- 标定板覆盖画面的不同区域

**交付物**:
```json
// localstore/calibration/camera_intrinsic.json
{
  "camera_matrix": {
    "fx": 1000.0,
    "fy": 1000.0,
    "cx": 960.0,
    "cy": 540.0
  },
  "distortion_coeffs": [k1, k2, p1, p2, k3],
  "image_size": [1920, 1080],
  "reprojection_error": 0.3,
  "calibration_date": "2026-07-31T..."
}
```

---

#### 任务 1.2: 手眼标定（Eye-to-Hand）
**目标**: 获得相机相对机器人基座的刚体变换

**实施步骤**:
```python
# 新增模块: src/gripper_ai_controller/calibration/hand_eye.py

class HandEyeCalibration:
    """手眼标定器（Eye-to-Hand配置）"""
    
    def __init__(self, intrinsic_calibration):
        self.intrinsic = intrinsic_calibration
        self.robot_poses = []  # 机器人TCP位姿
        self.board_poses = []  # 标定板在相机中的位姿
    
    async def collect_sample(self, vision_adapter, robot_adapter):
        """采集一个标定样本"""
        # 1. 获取当前机器人TCP位姿
        robot_status = await robot_adapter.get_status()
        tcp_pose = robot_status.tcp_pose  # 相对于robot_base
        
        # 2. 检测标定板在相机中的位姿
        frame = await vision_adapter.capture()
        board_pose_in_camera = self._detect_board_pose(
            frame, 
            self.intrinsic
        )
        
        if board_pose_in_camera is None:
            return False, "未检测到标定板"
        
        self.robot_poses.append(tcp_pose)
        self.board_poses.append(board_pose_in_camera)
        
        return True, f"已采集 {len(self.robot_poses)} 个样本"
    
    def calibrate(self):
        """求解手眼标定 AX=XB"""
        if len(self.robot_poses) < 10:
            raise ValueError("至少需要10个样本")
        
        # 转换为齐次变换矩阵
        A = []  # 机器人运动
        B = []  # 标定板在相机中的运动
        
        for i in range(len(self.robot_poses) - 1):
            A.append(self._pose_to_matrix(self.robot_poses[i]))
            B.append(self._pose_to_matrix(self.board_poses[i]))
        
        # 求解 AX=XB (Tsai-Lenz 方法或其他)
        R_cam2base, t_cam2base = cv2.calibrateHandEye(
            R_gripper2base=...,
            t_gripper2base=...,
            R_target2cam=...,
            t_target2cam=...,
            method=cv2.CALIB_HAND_EYE_TSAI
        )
        
        return HandEyeResult(
            rotation=R_cam2base,
            translation=t_cam2base,
            error=self._compute_reprojection_error()
        )
```

**CLI命令**:
```bash
# 交互式手眼标定采集
scripts\run.bat calibration-handeye \
  --config localstore/camera-jaka.json \
  --intrinsic localstore/calibration/camera_intrinsic.json \
  --output localstore/calibration/hand_eye.json
  
# 采集流程：
# 1. 将标定板固定在工作台上
# 2. 使用示教器移动机械臂到不同位置（10-15个）
# 3. 每个位置按Enter采集一次
# 4. 自动计算并保存结果
```

**验证标准**:
- 转换误差 < 2mm
- 至少采集 10 个不同位姿
- 覆盖工作空间的不同区域和角度

**交付物**:
```json
// localstore/calibration/hand_eye.json
{
  "camera_to_base": {
    "rotation": [[r11, r12, r13], [r21, r22, r23], [r31, r32, r33]],
    "translation": [tx, ty, tz],  // mm
    "quaternion": [qx, qy, qz, qw]
  },
  "calibration_error_mm": 1.5,
  "num_samples": 12,
  "calibration_date": "2026-07-31T..."
}
```

---

#### 任务 1.3: 坐标转换模块
**目标**: 实现像素→机器人坐标的完整转换链

**实施步骤**:
```python
# 新增模块: src/gripper_ai_controller/coordination/transformer.py

class CoordinateTransformer:
    """坐标系转换器"""
    
    def __init__(self, camera_intrinsic, hand_eye_transform):
        self.K = self._load_camera_matrix(camera_intrinsic)
        self.dist = camera_intrinsic['distortion_coeffs']
        self.T_cam2base = self._load_transform(hand_eye_transform)
    
    def pixel_to_robot_base(
        self, 
        pixel_x: float, 
        pixel_y: float, 
        depth_mm: float
    ) -> Tuple[float, float, float]:
        """
        像素坐标 + 深度 → 机器人基座坐标
        
        Args:
            pixel_x, pixel_y: 像素坐标
            depth_mm: 物体到相机的距离(mm)，可以从：
                     - 平面假设（Z = 工作台高度）
                     - 深度相机
                     - 立体视觉
        
        Returns:
            (x, y, z): 机器人基座坐标系下的位置(mm)
        """
        # 1. 畸变校正（可选，如果畸变小可跳过）
        pixel_undistorted = self._undistort_point(pixel_x, pixel_y)
        
        # 2. 像素坐标 → 相机坐标（归一化平面）
        x_norm = (pixel_undistorted[0] - self.K[0, 2]) / self.K[0, 0]
        y_norm = (pixel_undistorted[1] - self.K[1, 2]) / self.K[1, 1]
        
        # 3. 乘以深度得到相机坐标系下的3D点
        point_cam = np.array([
            x_norm * depth_mm,
            y_norm * depth_mm,
            depth_mm
        ])
        
        # 4. 相机坐标 → 机器人基座坐标
        point_base = self.T_cam2base @ np.append(point_cam, 1)[:3]
        
        return tuple(point_base)
    
    def robot_base_to_pixel(
        self, 
        x_mm: float, 
        y_mm: float, 
        z_mm: float
    ) -> Tuple[float, float]:
        """机器人基座坐标 → 像素坐标（反向投影）"""
        # 1. 基座坐标 → 相机坐标
        point_base_homo = np.array([x_mm, y_mm, z_mm, 1.0])
        point_cam = np.linalg.inv(self.T_cam2base) @ point_base_homo
        
        # 2. 3D → 2D投影
        x_norm = point_cam[0] / point_cam[2]
        y_norm = point_cam[1] / point_cam[2]
        
        # 3. 归一化坐标 → 像素坐标
        pixel_x = x_norm * self.K[0, 0] + self.K[0, 2]
        pixel_y = y_norm * self.K[1, 1] + self.K[1, 2]
        
        # 4. 应用畸变（如果需要）
        pixel_distorted = self._distort_point(pixel_x, pixel_y)
        
        return pixel_distorted
    
    def estimate_depth_from_table(self, table_height_mm: float) -> float:
        """
        从工作台平面假设估算深度
        
        Args:
            table_height_mm: 工作台相对于机器人基座的高度(mm)
        
        Returns:
            物体到相机的距离(mm)
        """
        # 相机在基座坐标系中的高度
        camera_height = self.T_cam2base[2, 3]
        
        # 假设物体在工作台上，计算相机到物体的距离
        # 这是简化计算，实际需要考虑相机俯角
        depth_mm = camera_height - table_height_mm
        
        return depth_mm
```

**验证方法**:
```python
# 测试脚本: scripts/tools/test_coordinate_transform.py

async def test_transform_accuracy():
    """测试坐标转换精度"""
    
    # 1. 在已知位置放置标定物体
    known_positions = [
        (300, 200, 50),   # (x, y, z) in mm
        (-200, 300, 50),
        (100, -100, 50),
    ]
    
    errors = []
    for true_pos in known_positions:
        # 2. 通过相机检测获得像素坐标
        pixel_x, pixel_y = detect_object_center(frame)
        
        # 3. 转换为机器人坐标
        estimated_pos = transformer.pixel_to_robot_base(
            pixel_x, pixel_y, 
            depth_mm=50.0  # 平面假设
        )
        
        # 4. 计算误差
        error = np.linalg.norm(
            np.array(true_pos) - np.array(estimated_pos)
        )
        errors.append(error)
        
        print(f"真实位置: {true_pos}")
        print(f"估算位置: {estimated_pos}")
        print(f"误差: {error:.2f} mm\n")
    
    avg_error = np.mean(errors)
    print(f"平均误差: {avg_error:.2f} mm")
    
    assert avg_error < 5.0, "转换误差过大！"
```

---

### **阶段 2: 深度估计增强** (1周) - **优先级 P1**

#### 任务 2.1: 平面假设深度估计（短期方案）
**当前实现**: 假设所有物体在同一平面
**改进方向**:
```python
class PlaneDepthEstimator:
    """基于平面约束的深度估计器"""
    
    def __init__(self, transformer, table_height_mm):
        self.transformer = transformer
        self.table_height_mm = table_height_mm
    
    def estimate_depth(
        self, 
        pixel_x: float, 
        pixel_y: float,
        object_height_mm: float = 0.0
    ) -> float:
        """
        估算物体深度
        
        Args:
            pixel_x, pixel_y: 像素坐标
            object_height_mm: 物体高度（可选，用于修正）
        
        Returns:
            深度(mm)
        """
        # 基础深度（到工作台平面）
        base_depth = self.transformer.estimate_depth_from_table(
            self.table_height_mm
        )
        
        # 如果知道物体高度，修正到物体中心
        depth = base_depth - object_height_mm / 2
        
        return depth
```

#### 任务 2.2: 深度相机集成（中期方案）
**实施计划**:
1. 调研深度相机选型（Intel RealSense / Azure Kinect）
2. 实现深度相机适配器
3. RGB-D配准
4. 点云处理

---

### **阶段 3: 检测增强** (1-2周) - **优先级 P1**

#### 任务 3.1: 检测结果时序平滑
**问题**: 检测框抖动，ID不稳定

**解决方案**:
```python
# 扩展: src/gripper_ai_controller/object_detection/tracker.py

class DetectionSmoother:
    """检测结果时序平滑器"""
    
    def __init__(self, smoothing_window=5, iou_threshold=0.5):
        self.window = smoothing_window
        self.iou_threshold = iou_threshold
        self.history = deque(maxlen=smoothing_window)
    
    def smooth(self, detections):
        """对检测结果进行时序平滑"""
        self.history.append(detections)
        
        if len(self.history) < self.window:
            return detections  # 数据不足，返回原始结果
        
        # 对每个检测框进行平滑
        smoothed = []
        for det in detections:
            # 在历史中找到匹配的框
            matches = self._find_matches(det)
            
            if len(matches) >= self.window * 0.6:  # 至少60%帧出现
                # 计算平滑后的框
                smooth_box = self._average_boxes(matches)
                smoothed.append(smooth_box)
        
        return smoothed
```

#### 任务 3.2: 多目标跟踪（SORT/DeepSORT）
**实施方案**: 集成简化的SORT算法

---

### **阶段 4: 工件姿态增强** (1周) - **优先级 P2**

#### 任务 4.1: 3D姿态估计（6D Pose）
**候选方案**:
- PVNet
- CosyPose
- DOPE (Deep Object Pose Estimation)

#### 任务 4.2: 多工件场景处理
**实施内容**:
- 工件分割
- 堆叠检测
- 遮挡处理

---

## 📋 实施检查清单

### Phase 1: 标定系统（必须完成）
- [ ] 相机内参标定工具完成
- [ ] 手眼标定工具完成
- [ ] 坐标转换模块实现
- [ ] 转换精度验证（< 5mm）
- [ ] 标定流程文档
- [ ] CLI命令集成

### Phase 2: 深度估计（推荐完成）
- [ ] 平面假设深度估计优化
- [ ] 深度相机选型调研
- [ ] 深度相机适配器（如果采购）

### Phase 3: 检测增强（推荐完成）
- [ ] 时序平滑实现
- [ ] 多目标跟踪集成
- [ ] 自定义类别训练文档

### Phase 4: 姿态增强（可选）
- [ ] 3D姿态估计调研
- [ ] 多工件处理

---

## 🔗 与其他模块的集成

### 集成到 Runtime
```python
# src/gripper_ai_controller/core/runtime.py

class Runtime:
    def __init__(self, config: RuntimeConfig):
        # ... 现有代码
        
        # 新增：坐标转换器
        self.coordinate_transformer = self._build_transformer(config)
    
    async def run_objective(self, objective: str):
        # ... 采集和感知
        perception = await self.perception_plugin.perceive(...)
        
        # 新增：坐标转换
        for obj in perception.objects:
            if obj.pose is not None:
                # 将相机坐标转换为机器人坐标
                obj.pose = self.coordinate_transformer.camera_to_robot(
                    obj.pose
                )
```

### 集成到 SafetyPolicy
```python
# src/gripper_ai_controller/services/safety.py

class SafetyPolicy:
    def evaluate(self, command, robot_status, gripper_status, perception):
        # ... 现有检查
        
        # 新增：基于视觉的碰撞预警
        if self._vision_collision_risk(perception, command):
            return SafetyDecision(False, "视觉检测到潜在碰撞风险")
```

---

## 📊 预期成果

完成视觉增强后，系统将具备：

1. **精确的坐标转换** - 像素→机器人坐标误差 < 5mm
2. **稳定的目标跟踪** - 检测框抖动显著降低
3. **可靠的深度估计** - 平面假设误差 < 10mm
4. **完整的标定流程** - 自动化标定工具链

**项目整体完成度预期**: 从70% → 85%

---

## 📅 时间估算

| 阶段 | 任务 | 预计时间 | 优先级 |
|------|------|----------|--------|
| Phase 1.1 | 相机内参标定 | 3天 | P0 |
| Phase 1.2 | 手眼标定 | 5天 | P0 |
| Phase 1.3 | 坐标转换模块 | 2天 | P0 |
| Phase 2.1 | 深度估计优化 | 2天 | P1 |
| Phase 3.1 | 时序平滑 | 2天 | P1 |
| Phase 3.2 | 多目标跟踪 | 3天 | P1 |
| **总计** | | **17天** | |

---

## 🎯 下一步行动

请确认您希望从哪个任务开始：

1. **立即开始相机内参标定** - 实现标定工具和CLI命令
2. **立即开始手眼标定** - 实现手眼标定采集和计算
3. **立即开始坐标转换模块** - 实现转换器核心逻辑
4. **全部一起开始** - 按顺序实施完整的Phase 1

请指示具体要执行的任务！
