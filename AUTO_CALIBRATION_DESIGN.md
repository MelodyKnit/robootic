# 自动标定系统设计方案

## 🎯 设计目标

实现**零人工干预**的全自动相机标定系统：
- ✓ 自动规划机械臂运动路径
- ✓ 自动采集标定数据
- ✓ 自动计算标定结果
- ✓ 自动验证标定精度
- ✓ 失败自动重试

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│             AutoCalibrationOrchestrator                 │
│  (总控制器 - 协调整个自动标定流程)                        │
└─────────────────────────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
┌────────▼────────┐ ┌───▼────────┐ ┌───▼─────────────┐
│  PathPlanner    │ │  Executor  │ │  QualityChecker │
│  (路径规划器)   │ │  (执行器)  │ │  (质量检查器)   │
└─────────────────┘ └────────────┘ └─────────────────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │
              ┌────────────┴─────────────┐
              │                          │
    ┌─────────▼─────────┐    ┌─────────▼──────────┐
    │  RobotAdapter     │    │  VisionAdapter     │
    │  (机械臂控制)     │    │  (相机采集)        │
    └───────────────────┘    └────────────────────┘
```

---

## 📋 实施方案

### **方案 1: 相机内参自动标定**

#### 步骤概述
```
1. 将标定板固定在机械臂末端（或工作台）
2. 机械臂自动移动到预定义的采集位姿序列
3. 每个位姿自动采集图像
4. 自动计算内参矩阵
5. 自动验证精度
```

#### 核心实现
```python
# 新增模块: src/gripper_ai_controller/calibration/auto_intrinsic.py

from typing import List, Tuple
from dataclasses import dataclass
import numpy as np
import cv2
import asyncio

@dataclass
class CalibrationPose:
    """标定采集位姿"""
    x: float  # TCP position (mm)
    y: float
    z: float
    rx: float  # TCP orientation (rad)
    ry: float
    rz: float
    description: str  # "正面中心", "左侧倾斜", etc.


class AutoIntrinsicCalibration:
    """全自动相机内参标定器"""
    
    def __init__(
        self,
        robot_adapter,
        vision_adapter,
        board_size=(9, 6),
        square_size_mm=25.0,
        workspace_center=(300, 0, 100),  # 工作空间中心点
    ):
        self.robot = robot_adapter
        self.vision = vision_adapter
        self.board_size = board_size
        self.square_size_mm = square_size_mm
        self.workspace_center = workspace_center
        
        # 自动生成采集位姿序列
        self.capture_poses = self._generate_capture_poses()
    
    def _generate_capture_poses(self) -> List[CalibrationPose]:
        """
        自动生成采集位姿序列
        
        策略：在工作空间中心周围生成多个观测角度
        - 5个不同距离（近→远）
        - 每个距离4个角度（正面、左、右、上）
        - 覆盖画面的不同区域
        """
        poses = []
        cx, cy, cz = self.workspace_center
        
        # 距离序列：从近到远
        distances = [150, 200, 250, 300, 350]  # mm
        
        for i, dist in enumerate(distances):
            # 1. 正面中心位置
            poses.append(CalibrationPose(
                x=cx,
                y=cy,
                z=cz + dist,
                rx=np.pi,  # 向下看
                ry=0.0,
                rz=0.0,
                description=f"正面中心 - 距离{dist}mm"
            ))
            
            # 2. 左侧倾斜
            poses.append(CalibrationPose(
                x=cx - 100,
                y=cy,
                z=cz + dist,
                rx=np.pi - 0.3,  # 向下并向右倾斜
                ry=0.0,
                rz=0.3,
                description=f"左侧倾斜 - 距离{dist}mm"
            ))
            
            # 3. 右侧倾斜
            poses.append(CalibrationPose(
                x=cx + 100,
                y=cy,
                z=cz + dist,
                rx=np.pi - 0.3,
                ry=0.0,
                rz=-0.3,
                description=f"右侧倾斜 - 距离{dist}mm"
            ))
            
            # 4. 前方倾斜
            poses.append(CalibrationPose(
                x=cx,
                y=cy - 100,
                z=cz + dist,
                rx=np.pi - 0.3,
                ry=0.3,
                rz=0.0,
                description=f"前方倾斜 - 距离{dist}mm"
            ))
        
        return poses
    
    async def run(self, output_path: str) -> dict:
        """
        执行全自动标定流程
        
        Returns:
            标定结果字典，包含相机矩阵、畸变系数、误差等
        """
        print("=" * 60)
        print("🤖 开始自动相机内参标定")
        print("=" * 60)
        
        # 阶段1: 准备
        print("\n[阶段1] 准备机械臂和相机...")
        await self._prepare()
        
        # 阶段2: 自动采集
        print(f"\n[阶段2] 自动采集 {len(self.capture_poses)} 个位姿...")
        images, valid_poses = await self._auto_capture()
        
        if len(images) < 10:
            raise ValueError(f"采集失败：只获得 {len(images)} 张有效图像（需要至少10张）")
        
        print(f"✓ 成功采集 {len(images)} 张标定图像")
        
        # 阶段3: 计算标定
        print("\n[阶段3] 计算相机内参...")
        result = self._calibrate(images)
        
        # 阶段4: 验证精度
        print("\n[阶段4] 验证标定精度...")
        validation = await self._validate(result)
        
        # 阶段5: 保存结果
        print(f"\n[阶段5] 保存标定结果到 {output_path}...")
        self._save_result(result, output_path)
        
        # 阶段6: 返回原点
        print("\n[阶段6] 机械臂返回原点...")
        await self._return_home()
        
        print("\n" + "=" * 60)
        print("✅ 自动标定完成！")
        print("=" * 60)
        print(f"重投影误差: {result['reprojection_error']:.3f} 像素")
        print(f"有效图像数: {len(images)}")
        print(f"焦距 fx: {result['camera_matrix']['fx']:.2f}")
        print(f"焦距 fy: {result['camera_matrix']['fy']:.2f}")
        print(f"主点 cx: {result['camera_matrix']['cx']:.2f}")
        print(f"主点 cy: {result['camera_matrix']['cy']:.2f}")
        print("=" * 60)
        
        return result
    
    async def _prepare(self):
        """准备阶段：检查机械臂和相机状态"""
        # 1. 检查机械臂
        status = await self.robot.get_status()
        if not status.initialized or not status.enabled:
            raise RuntimeError("机械臂未初始化或未使能")
        
        # 2. 检查相机
        frame = await self.vision.capture()
        if frame is None or frame.data is None:
            raise RuntimeError("相机无法采集图像")
        
        print("✓ 机械臂和相机准备就绪")
    
    async def _auto_capture(self) -> Tuple[List[np.ndarray], List[CalibrationPose]]:
        """
        自动采集标定图像
        
        Returns:
            (有效图像列表, 对应的位姿列表)
        """
        images = []
        valid_poses = []
        
        for i, pose in enumerate(self.capture_poses):
            print(f"\n[{i+1}/{len(self.capture_poses)}] 移动到: {pose.description}")
            
            # 1. 移动到目标位姿
            success = await self._move_to_pose(pose)
            if not success:
                print(f"  ⚠️ 移动失败，跳过此位姿")
                continue
            
            # 2. 等待稳定
            await asyncio.sleep(0.5)
            
            # 3. 采集图像
            frame = await self.vision.capture()
            if frame is None or frame.data is None:
                print(f"  ⚠️ 图像采集失败，跳过此位姿")
                continue
            
            # 4. 检测标定板
            img_array = self._frame_to_array(frame)
            found, corners = self._detect_chessboard(img_array)
            
            if found:
                images.append(img_array)
                valid_poses.append(pose)
                print(f"  ✓ 检测到标定板，图像有效")
            else:
                print(f"  ⚠️ 未检测到标定板，跳过此位姿")
        
        return images, valid_poses
    
    async def _move_to_pose(self, pose: CalibrationPose) -> bool:
        """
        移动机械臂到指定位姿
        
        Returns:
            是否成功
        """
        try:
            # 构造运动命令
            from gripper_ai_controller.domain.models import RobotCommand, Pose3D
            
            target_pose = Pose3D(
                x=pose.x,
                y=pose.y,
                z=pose.z,
                rx=pose.rx,
                ry=pose.ry,
                rz=pose.rz,
                frame_id="robot_base"
            )
            
            command = RobotCommand(
                command_type="MOVE_LINEAR",
                target_pose=target_pose,
                velocity=0.1,  # 慢速移动，保证安全
                acceleration=0.1
            )
            
            # 执行运动
            await self.robot.execute_command(command)
            
            # 等待运动完成
            timeout = 10.0  # 10秒超时
            start_time = asyncio.get_event_loop().time()
            
            while True:
                status = await self.robot.get_status()
                
                # 检查是否到达
                current_pose = status.tcp_pose
                distance = np.linalg.norm([
                    current_pose.x - target_pose.x,
                    current_pose.y - target_pose.y,
                    current_pose.z - target_pose.z
                ])
                
                if distance < 5.0:  # 5mm误差内
                    return True
                
                # 检查超时
                if asyncio.get_event_loop().time() - start_time > timeout:
                    print(f"  ⚠️ 运动超时")
                    return False
                
                await asyncio.sleep(0.1)
        
        except Exception as e:
            print(f"  ⚠️ 运动异常: {e}")
            return False
    
    def _detect_chessboard(self, image: np.ndarray) -> Tuple[bool, np.ndarray]:
        """检测棋盘格角点"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        
        # 使用自适应阈值提高检测鲁棒性
        found, corners = cv2.findChessboardCorners(
            gray,
            self.board_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH | 
            cv2.CALIB_CB_NORMALIZE_IMAGE |
            cv2.CALIB_CB_FAST_CHECK
        )
        
        if found:
            # 亚像素角点优化
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        
        return found, corners
    
    def _calibrate(self, images: List[np.ndarray]) -> dict:
        """计算相机内参"""
        # 生成标定板的3D点
        objp = np.zeros((self.board_size[0] * self.board_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[
            0:self.board_size[0], 
            0:self.board_size[1]
        ].T.reshape(-1, 2)
        objp *= self.square_size_mm
        
        # 收集所有角点
        obj_points = []
        img_points = []
        
        for img in images:
            found, corners = self._detect_chessboard(img)
            if found:
                obj_points.append(objp)
                img_points.append(corners)
        
        # 执行标定
        ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
            obj_points,
            img_points,
            (images[0].shape[1], images[0].shape[0]),
            None,
            None
        )
        
        return {
            "camera_matrix": {
                "fx": float(camera_matrix[0, 0]),
                "fy": float(camera_matrix[1, 1]),
                "cx": float(camera_matrix[0, 2]),
                "cy": float(camera_matrix[1, 2]),
            },
            "distortion_coeffs": dist_coeffs.flatten().tolist(),
            "image_size": [images[0].shape[1], images[0].shape[0]],
            "reprojection_error": float(ret),
            "num_images": len(images),
            "calibration_date": datetime.now().isoformat()
        }
    
    async def _validate(self, result: dict) -> dict:
        """验证标定精度"""
        # TODO: 移动到测试位姿，检测实际误差
        return {"status": "passed"}
    
    def _save_result(self, result: dict, output_path: str):
        """保存标定结果"""
        import json
        from pathlib import Path
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
    
    async def _return_home(self):
        """返回原点"""
        # TODO: 移动到安全位置
        pass
    
    def _frame_to_array(self, frame) -> np.ndarray:
        """将Frame对象转换为numpy数组"""
        # 根据实际Frame结构实现
        return frame.data
```

---

### **方案 2: 手眼标定自动化**

#### 核心实现
```python
# 新增模块: src/gripper_ai_controller/calibration/auto_hand_eye.py

class AutoHandEyeCalibration:
    """全自动手眼标定器"""
    
    def __init__(
        self,
        robot_adapter,
        vision_adapter,
        intrinsic_calibration,  # 必须先完成内参标定
        board_size=(9, 6),
        square_size_mm=25.0,
    ):
        self.robot = robot_adapter
        self.vision = vision_adapter
        self.intrinsic = intrinsic_calibration
        self.board_size = board_size
        self.square_size_mm = square_size_mm
    
    def _generate_calibration_poses(self) -> List[CalibrationPose]:
        """
        生成手眼标定的机械臂位姿序列
        
        要求：
        1. 标定板固定在工作台上
        2. 机械臂移动到不同位置观察标定板
        3. 需要覆盖不同的距离、角度、方位
        """
        poses = []
        
        # 假设标定板中心在 (0, 0, 0) 相对于机器人基座
        board_center = np.array([0.0, 0.0, 0.0])
        
        # 生成观测位姿：球面采样
        # 距离: 300-500mm
        # 俯仰角: 30-70度
        # 方位角: 0-360度
        
        distances = [300, 400, 500]  # mm
        elevation_angles = [30, 45, 60]  # 度
        azimuth_angles = [0, 60, 120, 180, 240, 300]  # 度
        
        for dist in distances:
            for elev in elevation_angles:
                for azim in azimuth_angles:
                    # 球坐标 → 笛卡尔坐标
                    elev_rad = np.radians(elev)
                    azim_rad = np.radians(azim)
                    
                    x = board_center[0] + dist * np.cos(elev_rad) * np.cos(azim_rad)
                    y = board_center[1] + dist * np.cos(elev_rad) * np.sin(azim_rad)
                    z = board_center[2] + dist * np.sin(elev_rad)
                    
                    # 计算朝向（相机朝向标定板）
                    direction = board_center - np.array([x, y, z])
                    direction /= np.linalg.norm(direction)
                    
                    # 转换为欧拉角（简化）
                    rx = np.arctan2(direction[1], direction[2])
                    ry = np.arctan2(-direction[0], np.sqrt(direction[1]**2 + direction[2]**2))
                    rz = 0.0
                    
                    poses.append(CalibrationPose(
                        x=x, y=y, z=z,
                        rx=rx, ry=ry, rz=rz,
                        description=f"距离{dist}mm_俯仰{elev}°_方位{azim}°"
                    ))
        
        return poses
    
    async def run(self, output_path: str) -> dict:
        """执行全自动手眼标定"""
        print("=" * 60)
        print("🤖 开始自动手眼标定")
        print("=" * 60)
        print("⚠️ 请确保标定板已固定在工作台上")
        print("")
        
        # 生成采集位姿
        poses = self._generate_calibration_poses()
        print(f"将采集 {len(poses)} 个样本...")
        
        # 自动采集
        robot_poses = []
        board_poses = []
        
        for i, pose in enumerate(poses):
            print(f"\n[{i+1}/{len(poses)}] {pose.description}")
            
            # 移动机械臂
            success = await self._move_to_pose(pose)
            if not success:
                continue
            
            await asyncio.sleep(0.5)
            
            # 采集图像并检测标定板位姿
            frame = await self.vision.capture()
            board_pose = self._detect_board_pose_3d(frame)
            
            if board_pose is not None:
                robot_poses.append(pose)
                board_poses.append(board_pose)
                print(f"  ✓ 采集成功 ({len(robot_poses)} / {len(poses)})")
            else:
                print(f"  ⚠️ 未检测到标定板")
        
        if len(robot_poses) < 10:
            raise ValueError(f"采集失败：只获得 {len(robot_poses)} 个有效样本")
        
        print(f"\n✓ 成功采集 {len(robot_poses)} 个样本")
        
        # 计算手眼标定
        print("\n计算手眼标定...")
        result = self._calibrate_hand_eye(robot_poses, board_poses)
        
        # 保存结果
        self._save_result(result, output_path)
        
        print("\n✅ 手眼标定完成！")
        print(f"转换误差: {result['calibration_error_mm']:.2f} mm")
        
        return result
    
    def _detect_board_pose_3d(self, frame) -> Optional[Pose3D]:
        """检测标定板在相机坐标系中的3D位姿"""
        img = self._frame_to_array(frame)
        found, corners = self._detect_chessboard(img)
        
        if not found:
            return None
        
        # 生成标定板3D点
        objp = np.zeros((self.board_size[0] * self.board_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:self.board_size[0], 0:self.board_size[1]].T.reshape(-1, 2)
        objp *= self.square_size_mm
        
        # PnP求解
        camera_matrix = self._build_camera_matrix()
        dist_coeffs = np.array(self.intrinsic['distortion_coeffs'])
        
        success, rvec, tvec = cv2.solvePnP(
            objp,
            corners,
            camera_matrix,
            dist_coeffs
        )
        
        if not success:
            return None
        
        # 转换为Pose3D
        rotation_matrix, _ = cv2.Rodrigues(rvec)
        # ... 转换为欧拉角
        
        return Pose3D(...)
    
    def _calibrate_hand_eye(self, robot_poses, board_poses) -> dict:
        """求解手眼标定 AX=XB"""
        # 使用 cv2.calibrateHandEye
        R_cam2base, t_cam2base = cv2.calibrateHandEye(
            # ... 参数
            method=cv2.CALIB_HAND_EYE_TSAI
        )
        
        return {
            "camera_to_base": {
                "rotation": R_cam2base.tolist(),
                "translation": t_cam2base.flatten().tolist(),
            },
            "calibration_error_mm": self._compute_error(...),
            "num_samples": len(robot_poses),
            "calibration_date": datetime.now().isoformat()
        }
```

---

## 🎮 CLI 命令集成

```bash
# 全自动相机内参标定
scripts\run.bat auto-calibrate-intrinsic \
  --config localstore/camera-jaka.json \
  --output localstore/calibration/camera_intrinsic.json

# 全自动手眼标定
scripts\run.bat auto-calibrate-handeye \
  --config localstore/camera-jaka.json \
  --intrinsic localstore/calibration/camera_intrinsic.json \
  --output localstore/calibration/hand_eye.json

# 一键完整标定（内参 + 手眼）
scripts\run.bat auto-calibrate-full \
  --config localstore/camera-jaka.json \
  --output-dir localstore/calibration
```

---

## ✅ 自动标定的优势

| 方面 | 手动标定 | 自动标定 |
|------|----------|----------|
| **时间成本** | 30-60分钟 | 5-10分钟 |
| **人工干预** | 需要持续操作示教器 | 零干预（一键启动） |
| **可重复性** | 低（依赖操作者） | 高（固定算法） |
| **位姿覆盖** | 不完整（操作者主观） | 完整（算法规划） |
| **精度** | 0.5-2.0mm | < 1.0mm |
| **失败处理** | 需要重新开始 | 自动跳过失败位姿 |
| **新手友好** | 需要培训 | 开箱即用 |

---

## 🚀 实施路线图

### Phase 1: 自动内参标定（3天）
- [ ] 实现 `AutoIntrinsicCalibration` 类
- [ ] 路径规划算法
- [ ] 自动采集循环
- [ ] CLI命令集成
- [ ] 测试验证

### Phase 2: 自动手眼标定（5天）
- [ ] 实现 `AutoHandEyeCalibration` 类
- [ ] 球面位姿生成
- [ ] 3D位姿检测
- [ ] AX=XB求解
- [ ] 精度验证

### Phase 3: 完整流水线（2天）
- [ ] 一键完整标定命令
- [ ] 断点续传（标定失败重试）
- [ ] 可视化界面（可选）
- [ ] 用户文档

---

## 🎯 下一步行动

请选择：

1. **立即实施自动内参标定** - 实现 `AutoIntrinsicCalibration` 核心代码
2. **立即实施自动手眼标定** - 实现 `AutoHandEyeCalibration` 核心代码
3. **完整实施** - 按顺序实现完整的自动标定系统
4. **先做原型测试** - 先用手动标定验证流程，再自动化

您希望从哪个开始？
