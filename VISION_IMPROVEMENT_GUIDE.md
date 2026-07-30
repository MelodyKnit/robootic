# 视觉识别改进指南

## 当前问题诊断

从截图分析，物体识别失败的主要原因：

### 1. **模型类别不匹配** ⚠️ 最关键
- **当前使用**: Faster R-CNN (COCO 数据集，80 个类别)
- **问题**: COCO 数据集**没有** "wrench"（扳手）类别
- **COCO 包含的工具类**: 仅有 "scissors"（剪刀）、"knife"（刀）、"spoon"（勺子）、"fork"（叉子）
- **配置中的 YOLO-World 模型**（`yolo-world-tools.onnx`）支持 wrench，但**文件不存在**

### 2. 置信度阈值过高
- 当前设置: `confidence_threshold: 0.75` (75%)
- 即使模型勉强识别出扳手（可能识别为其他金属物体），75% 的阈值会过滤掉大部分结果

### 3. 光照条件
- 画面底部有强烈的**绿色反光**
- 当前曝光值 16188 较高，且自动曝光关闭
- 强反光会干扰边缘检测和特征提取

---

## 解决方案（按优先级）

### 方案 A：降低阈值 + 允许相近类别（快速验证）

修改 `localstore/hikvision-object-detection.local.json`：

```json
{
  "object_detection": {
    "enabled": true,
    "selected_model_id": "fasterrcnn-coco-local",
    "models": [
      {
        "model_id": "fasterrcnn-coco-local",
        "display_name": "Faster R-CNN COCO（本地权重）",
        "provider": "torchvision-faster-rcnn-resnet50-fpn",
        "model_path": "localstore/models/fasterrcnn_resnet50_fpn_coco.pth",
        "device": "cuda",
        "allowed_labels": [
          "scissors",
          "knife",
          "spoon",
          "fork",
          "bottle",
          "cup",
          "bowl"
        ],
        "confidence_threshold": 0.3,  // 从 0.75 降到 0.3
        "inference_max_side": 960,
        "max_detections": 30
      }
    ]
  }
}
```

**原理**: 
- Faster R-CNN 可能会把扳手误识别为 "scissors"（剪刀）或其他金属物体
- 降低阈值让更多候选框通过
- `allowed_labels` 限制只显示可能相关的类别

**优点**: 立即可测试，无需下载模型  
**缺点**: 准确率低，可能误检或漏检

---

### 方案 B：使用支持扳手的模型（推荐）

#### 选项 1：YOLO-World（零样本检测）

YOLO-World 支持文本提示的开放词汇检测，可以直接识别 "wrench"。

**步骤**：
1. 下载预训练的 YOLO-World ONNX 模型（需要你自己找或转换）
2. 放到 `localstore/models/yolo-world-tools.onnx`
3. 修改配置切换到 YOLO-World：

```json
{
  "object_detection": {
    "enabled": true,
    "selected_model_id": "yolo-world-tools-local",  // 切换模型
    "models": [
      {
        "model_id": "yolo-world-tools-local",
        "display_name": "YOLO-World 工件提示（本地 ONNX）",
        "provider": "yolo-world-onnx-opencv",
        "model_path": "localstore/models/yolo-world-tools.onnx",
        "class_names": [
          "wrench",
          "pliers",
          "screwdriver",
          "hammer",
          "metal tool"
        ],
        "input_size": [640, 640],
        "output_format": "official-nms",
        "backend": "cpu",
        "confidence_threshold": 0.25,
        "nms_iou_threshold": 0.45,
        "max_detections": 30
      }
    ]
  }
}
```

#### 选项 2：YOLOv8/YOLOv10 自定义训练

如果你有标注数据，可以训练 YOLOv8 模型专门识别你的工件。

---

### 方案 C：改善光照和相机参数

**立即调整（在当前 Web UI 中）**：
1. **开启自动曝光**: 将右侧的"自动曝光"从 Off 改为 On
2. **降低曝光值**: 如果保持手动，把曝光值从 16188 降到 8000-10000
3. **调整增益**: 当前 1.902699，可以尝试降到 1.0-1.5

**物理环境**：
1. 调整光源角度，避免直射造成反光
2. 使用漫反射光源或加装扩散板
3. 如果可能，更换深色背景（当前绿色反光严重）

---

### 方案 D：使用 Object Pose 模式（如果只需要定位）

如果你只需要**定位扳手的位置和姿态**，而不需要识别类别：

1. 启用 `object-pose-analysis` 插件（截图中已启用但未配置）
2. 配置背景差分和轮廓检测
3. 这种方法不依赖深度学习模型，通过形状特征识别

**需要的配置**：
```json
{
  "object_pose": {
    "enabled": true,
    "background_reference_path": "localstore/object-pose/hikvision-usb/empty-table.png",
    "workcell_calibration_path": "localstore/object-pose/hikvision-usb/workcell-calibration.json",
    "expected_calibration_id": "local-preview-unverified",
    "profile": {
      "profile_id": "wrench-profile",
      "minimum_area_px": 800,
      "maximum_area_px": 5000,
      "minimum_aspect_ratio": 2.0,  // 扳手长宽比通常 > 2
      "maximum_aspect_ratio": 8.0,
      "minimum_fill_ratio": 0.3,
      "require_directional_yaw": true,
      "directional_feature": "moment_weighted_end"
    }
  }
}
```

**需要的步骤**：
1. 拍摄一张**空桌面**背景图
2. 执行相机-工作台标定
3. 配置扳手的形状特征参数

---

## 快速验证步骤

### 最快方案（5 分钟）：

1. **降低阈值**：
   ```bash
   # 编辑配置
   code localstore/hikvision-object-detection.local.json
   # 把 confidence_threshold 从 0.75 改成 0.3
   ```

2. **开启自动曝光**：
   在 Web UI 右侧点击"自动曝光" Off → On

3. **重启服务**：
   ```bash
   poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json
   ```

4. **查看结果**：
   如果仍然没有检测框，说明 COCO 模型确实不认识扳手，需要切换模型（方案 B）

---

## 模型文件获取

### YOLO-World ONNX 模型
- 官方仓库: https://github.com/AILab-CVC/YOLO-World
- 需要转换为 ONNX 格式
- 或者搜索已转换的 ONNX 权重

### 其他开放词汇检测模型
- **GroundingDINO**: 支持文本提示，效果好但较慢
- **OWL-ViT**: Google 的开放词汇模型
- **YOLOv8**: 可以用自定义数据集训练

---

## 调试技巧

### 1. 查看原始模型输出
修改代码临时打印所有检测结果（不过滤 `allowed_labels`），看模型是否有输出：

```python
# 在 src/gripper_ai_controller/object_detection/providers.py 中
# 找到 detect() 方法，在过滤前打印：
print(f"Raw detections: {[(box, score, label) for box, score, label in zip(boxes, scores, labels)]}")
```

### 2. 测试其他 COCO 类别
先用模型能识别的物体测试（如手机、杯子、剪刀），确认管道是通的。

### 3. 检查日志
```bash
# 查看服务日志，看是否有模型加载错误
poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json 2>&1 | tee detection.log
```

---

## 总结

**立即可做**（不需要额外资源）：
- ✅ 降低置信度阈值到 0.3
- ✅ 开启自动曝光或降低曝光值
- ✅ 调整光源减少反光

**需要下载/训练模型**（最有效）：
- 🔄 获取 YOLO-World ONNX 模型
- 🔄 或者用 YOLOv8 + 自定义数据集训练

**需要标定**（如果用 object-pose）：
- 📐 拍摄空背景图
- 📐 执行相机-工作台标定
- 📐 配置扳手形状参数
