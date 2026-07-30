# 视觉识别完整配置指南

本指南将帮助你配置两个识别模块：
1. **Object Detection** - 基于深度学习的物体检测（YOLOv8）
2. **Object Pose** - 基于轮廓检测的物品姿态识别

---

## 快速开始

### 方案 A：只使用物体检测（推荐新手）

**优点**：
- 无需背景图和标定
- 开箱即用
- 通用性强（80个COCO类别）

**步骤**：

1. **下载 YOLOv8 模型**
   ```bash
   cd D:\Nakamoto\Documents\Codes\Python\Robotic\projects\gripper-ai-controller
   python scripts/download_yolov8_models.py
   ```
   推荐选择：`1,2`（下载 nano 和 small 两个模型）

2. **配置检测模块**
   ```bash
   python scripts/configure_yolov8_detection.py
   ```
   - 设备选择：`1` (cuda GPU加速，推荐)
   - 置信度：`0.25` (默认，可以先测试再调整)
   - 类别：`1` (检测所有COCO类别)

3. **启动服务**
   ```bash
   poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json
   ```

4. **在浏览器中打开** `http://127.0.0.1:8000`
   - 左侧插件面板应该显示 `object-detection-analysis`
   - 相机画面中应该出现检测框和类别标签

**调试**：
- 如果没有检测框 → 降低置信度到 0.15，重启服务
- 如果误检太多 → 提高置信度到 0.4
- 如果帧率太低 → 切换到 yolov8n (nano) 模型

---

### 方案 B：物体检测 + 姿态识别（完整功能）

**优点**：
- 同时获得物体类别和精确姿态
- 姿态识别不依赖深度学习，更稳定
- 可以计算抓取点位置

**步骤**：

1. **先完成方案 A 的步骤 1-2**

2. **配置姿态识别模块**
   ```bash
   python scripts/setup_object_pose.py
   ```
   这会：
   - 创建必要的目录结构
   - 生成示例标定文件（占位符）
   - 配置通用工具轮廓参数

3. **拍摄空背景图** ⚠️ **关键步骤**
   
   a. 启动服务（用之前的命令）
   
   b. 在 Web UI 中：
      - 移除工作台上的所有物品
      - 确保光照与工作时一致
      - 在相机预览中右键保存图片
   
   c. 将图片保存为：
      ```
      localstore/object-pose/hikvision-usb/empty-table.png
      ```

4. **重启服务**
   ```bash
   poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json
   ```

5. **验证效果**
   - 左侧应该同时显示两个插件：
     - `object-detection-analysis` (已启用)
     - `object-pose-analysis` (已启用)
   - 画面中应该有：
     - 检测框（来自 YOLOv8）
     - 物品轮廓和中心点（来自姿态识别）

---

## 文件结构

完成配置后，你的目录结构应该是：

```
gripper-ai-controller/
├── localstore/
│   ├── models/
│   │   ├── yolov8n.onnx                    # YOLOv8 nano 模型
│   │   ├── yolov8s.onnx                    # YOLOv8 small 模型
│   │   └── fasterrcnn_resnet50_fpn_coco.pth
│   ├── object-pose/
│   │   └── hikvision-usb/
│   │       ├── empty-table.png             # 空背景参考图
│   │       ├── workcell-calibration.json   # 标定文件
│   │       └── profiles/
│   │           └── generic-tool.json       # 工具轮廓配置
│   └── hikvision-object-detection.local.json  # 主配置文件
└── scripts/
    ├── download_yolov8_models.py
    ├── configure_yolov8_detection.py
    └── setup_object_pose.py
```

---

## 配置文件说明

### hikvision-object-detection.local.json

这是主配置文件，包含三个部分：

#### 1. Object Detection 配置

```json
{
  "object_detection": {
    "enabled": true,
    "selected_model_id": "yolov8-yolov8s",  // 当前使用的模型
    "models": [
      {
        "model_id": "yolov8-yolov8n",
        "display_name": "YOLOv8 Nano (快速)",
        "provider": "yolo-world-onnx-opencv",
        "model_path": "localstore/models/yolov8n.onnx",
        "class_names": [...],  // 80个COCO类别
        "confidence_threshold": 0.25,
        "backend": "cuda"
      }
    ]
  }
}
```

**可调参数**：
- `confidence_threshold` (0.0-1.0): 检测置信度阈值，越高越严格
- `backend`: `cuda` (GPU) | `cpu` | `cuda-fp16` (GPU半精度)
- `class_names`: 只检测列表中的类别（空列表=全部）

#### 2. Object Pose 配置

```json
{
  "object_pose": {
    "enabled": true,
    "background_reference_path": "localstore/object-pose/hikvision-usb/empty-table.png",
    "difference_threshold": 25,  // 背景差分阈值
    "profile": {
      "minimum_area_px": 400,     // 最小物体面积
      "maximum_area_px": 10000,   // 最大物体面积
      "minimum_aspect_ratio": 1.0, // 最小长宽比
      "require_directional_yaw": false  // 是否检测方向
    }
  }
}
```

**可调参数**：
- `difference_threshold` (1-255): 背景差分阈值，越低越敏感
- `minimum_area_px`: 过滤小噪点
- `aspect_ratio`: 根据物体形状设定（扳手通常>2.5）

---

## 常见问题

### 1. YOLOv8 检测不到物体

**原因**：置信度阈值过高 或 COCO类别不匹配

**解决**：
```python
# 编辑 localstore/hikvision-object-detection.local.json
# 找到 object_detection.models[0].confidence_threshold
# 从 0.25 改为 0.15
```

重启服务后再测试。

---

### 2. 姿态识别检测不到物体

**原因**：没有背景图 或 背景差分阈值不对

**检查清单**：
- [ ] 确认背景图存在：`localstore/object-pose/hikvision-usb/empty-table.png`
- [ ] 背景图是空工作台（无物品）
- [ ] 光照条件与拍摄背景时一致

**调整阈值**：
```json
// 在配置文件中
"object_pose": {
  "difference_threshold": 15  // 从 25 降低到 15，更敏感
}
```

---

### 3. 画面中绿色反光严重

**物理改善**：
- 调整光源角度，避免直射
- 使用漫反射光源
- 更换深色工作台面

**相机参数**（在 Web UI 中调整）：
- 开启"自动曝光"
- 或手动降低曝光值（从 16188 降到 8000-10000）
- 降低增益（从 1.9 降到 1.0-1.5）

---

### 4. 两个插件冲突或性能差

**优化**：

1. **降低分析帧率**：
   ```json
   {
     "object_detection": {
       "max_analysis_fps": 1  // 从 2 降到 1
     },
     "object_pose": {
       "max_analysis_fps": 1
     }
   }
   ```

2. **使用更轻量的模型**：
   ```json
   {
     "object_detection": {
       "selected_model_id": "yolov8-yolov8n"  // 使用 nano 版本
     }
   }
   ```

3. **禁用不需要的插件**：
   在 Web UI 左侧面板，点击插件名称右侧的开关

---

### 5. COCO 没有我需要的类别

**选项 1**：降低阈值，让模型"误识别"
- 扳手可能被识别为 knife 或 scissors
- 设置 `confidence_threshold: 0.2`

**选项 2**：训练自定义 YOLOv8 模型
1. 收集你的物品图片（200+ 张）
2. 用 LabelImg 标注
3. 用 Ultralytics 训练：
   ```bash
   yolo train data=custom.yaml model=yolov8n.pt epochs=100
   ```
4. 导出 ONNX：
   ```bash
   yolo export model=runs/train/exp/weights/best.pt format=onnx
   ```
5. 将导出的 `.onnx` 放到 `localstore/models/`
6. 在配置文件中添加模型配置

---

## 性能对比

| 模型 | 大小 | 速度 (FPS) | 精度 | 推荐场景 |
|------|------|-----------|------|---------|
| YOLOv8n | 6MB | ~30 FPS (GPU) | 中 | 实时检测、资源受限 |
| YOLOv8s | 22MB | ~20 FPS (GPU) | 高 | 平衡性能和精度（推荐）|
| YOLOv8m | 52MB | ~12 FPS (GPU) | 很高 | 离线处理、高精度需求 |
| Faster R-CNN | 160MB | ~5 FPS (GPU) | 高 | 已有模型，不推荐新用途 |

Object Pose 轮廓检测：~50 FPS (CPU)，不依赖 GPU

---

## 下一步

### 相机标定（可选，提高精度）

如果你需要精确的3D位置信息：

```bash
poetry run gripper-ai-controller calibration \
  --config-file localstore/hikvision-object-detection.local.json \
  --calibration-board checkerboard \
  --output localstore/object-pose/hikvision-usb/workcell-calibration.json
```

### 集成到自动化流程

识别结果可以通过 Web API 获取：
- `GET /api/plugins/object-detection-analysis/state` - 检测结果
- `GET /api/plugins/object-pose-analysis/state` - 姿态结果

返回的 JSON 包含：
- 物体类别、置信度
- 边界框坐标
- 中心点位置（像素 + 毫米）
- 旋转角度

---

## 技术支持

如果遇到问题：

1. 查看服务日志：
   ```bash
   poetry run gripper-ai-controller web --config-file ... 2>&1 | tee debug.log
   ```

2. 检查配置文件语法：
   ```bash
   python -m json.tool localstore/hikvision-object-detection.local.json
   ```

3. 验证模型文件：
   ```bash
   ls -lh localstore/models/
   ```

4. 测试相机连接：
   ```bash
   poetry run gripper-ai-controller web --config-file localstore/hikvision-web.local.json
   ```
   （只启动相机，不加载检测模块）
