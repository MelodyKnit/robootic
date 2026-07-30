"""配置 YOLOv8 ONNX 检测到项目中"""

import json
from pathlib import Path


# YOLOv8 COCO 类别名称（80个类别）
YOLOV8_COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
]

# 工具和常见工业物品类别（YOLOv8 可能识别的）
TOOL_RELATED_CLASSES = [
    "knife",           # 刀 - 可能识别金属工具
    "scissors",        # 剪刀 - 形状相似的工具
    "bottle",          # 瓶子 - 圆柱形物体
    "cup",             # 杯子 - 容器
    "bowl",            # 碗 - 容器
    "spoon",           # 勺子 - 金属餐具
    "fork",            # 叉子 - 金属餐具
    "cell phone",      # 手机 - 矩形物体参考
    "remote",          # 遥控器 - 矩形物体
    "mouse",           # 鼠标 - 小型物体
    "keyboard",        # 键盘 - 矩形物体
]


def create_yolov8_config(
    config_path: Path,
    model_filename: str,
    model_display_name: str,
    device: str = "cuda",
    confidence_threshold: float = 0.25,
    allowed_labels: list = None
) -> None:
    """创建或更新配置文件，添加 YOLOv8 模型"""

    if allowed_labels is None:
        # 默认允许所有类别
        allowed_labels = []

    yolov8_model = {
        "model_id": f"yolov8-{model_filename.replace('.onnx', '')}",
        "display_name": model_display_name,
        "provider": "yolo-world-onnx-opencv",  # 复用 YOLO-World provider
        "model_path": f"localstore/models/{model_filename}",
        "class_names": YOLOV8_COCO_CLASSES,
        "input_size": [640, 640],
        "output_format": "ultralytics",
        "backend": device if device in ["cpu", "cuda", "cuda-fp16"] else "cpu",
        "confidence_threshold": confidence_threshold,
        "nms_iou_threshold": 0.45,
        "max_detections": 50
    }

    if allowed_labels:
        yolov8_model["class_names"] = allowed_labels

    # 读取现有配置
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        print(f"配置文件不存在: {config_path}")
        return

    # 更新 object_detection 配置
    if "object_detection" not in config:
        config["object_detection"] = {
            "enabled": True,
            "selected_model_id": yolov8_model["model_id"],
            "max_analysis_fps": 2,
            "overlay_max_frame_lag_seconds": 0.5,
            "models": []
        }

    # 检查是否已存在该模型
    models = config["object_detection"].get("models", [])
    existing_ids = {m["model_id"] for m in models}

    if yolov8_model["model_id"] not in existing_ids:
        models.append(yolov8_model)
        config["object_detection"]["models"] = models
        print(f"✓ 添加模型: {yolov8_model['model_id']}")
    else:
        # 更新现有模型
        for i, m in enumerate(models):
            if m["model_id"] == yolov8_model["model_id"]:
                models[i] = yolov8_model
                print(f"✓ 更新模型: {yolov8_model['model_id']}")
                break

    # 设置为当前选中的模型
    config["object_detection"]["selected_model_id"] = yolov8_model["model_id"]
    config["object_detection"]["enabled"] = True

    # 保存配置
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"✓ 配置已更新: {config_path}")


def main():
    project_root = Path(__file__).parent.parent
    models_dir = project_root / "localstore" / "models"

    print("=" * 70)
    print("YOLOv8 检测配置工具")
    print("=" * 70)
    print()

    # 检查可用的 YOLOv8 模型
    available_models = list(models_dir.glob("yolov8*.onnx"))

    if not available_models:
        print("⚠ 未找到 YOLOv8 模型文件")
        print(f"请先运行: python scripts/download_yolov8_models.py")
        return

    print("找到的 YOLOv8 模型:")
    for model_path in available_models:
        size_mb = model_path.stat().st_size / (1024 * 1024)
        print(f"  - {model_path.name} ({size_mb:.1f}MB)")
    print()

    # 配置目标文件
    config_file = project_root / "localstore" / "hikvision-object-detection.local.json"

    if not config_file.exists():
        print(f"⚠ 配置文件不存在: {config_file}")
        print("请确保已有基础配置文件")
        return

    print(f"配置文件: {config_file}")
    print()

    # 选择设备
    print("选择运行设备:")
    print("1. cuda (GPU 加速，推荐)")
    print("2. cuda-fp16 (GPU FP16，更快但可能略降精度)")
    print("3. cpu (CPU 运行)")
    device_choice = input("请选择 [1-3，默认1]: ").strip() or "1"
    device_map = {"1": "cuda", "2": "cuda-fp16", "3": "cpu"}
    device = device_map.get(device_choice, "cuda")
    print(f"✓ 选择设备: {device}")
    print()

    # 选择置信度阈值
    print("设置置信度阈值:")
    print("  - 0.25 (低，检测更多物体，可能有误检)")
    print("  - 0.5  (中等，平衡检测和准确率，推荐)")
    print("  - 0.7  (高，只保留高置信度检测)")
    conf_input = input("请输入阈值 [0.0-1.0，默认0.25]: ").strip()
    try:
        confidence = float(conf_input) if conf_input else 0.25
        confidence = max(0.0, min(1.0, confidence))
    except ValueError:
        confidence = 0.25
    print(f"✓ 置信度阈值: {confidence}")
    print()

    # 选择类别过滤
    print("类别过滤选项:")
    print("1. 检测所有 80 个 COCO 类别（通用，推荐）")
    print("2. 仅检测工具相关类别（knife, scissors, bottle 等）")
    print("3. 自定义类别列表")
    filter_choice = input("请选择 [1-3，默认1]: ").strip() or "1"

    allowed_labels = []
    if filter_choice == "2":
        allowed_labels = TOOL_RELATED_CLASSES
        print(f"✓ 过滤到 {len(allowed_labels)} 个工具相关类别")
    elif filter_choice == "3":
        print("可用类别:")
        for i, cls in enumerate(YOLOV8_COCO_CLASSES, 1):
            print(f"{i:2d}. {cls}", end="  ")
            if i % 5 == 0:
                print()
        print()
        indices_input = input("输入类别编号（逗号分隔，如 1,2,5-8）: ").strip()
        # 简单解析，仅支持逗号分隔的数字
        try:
            indices = []
            for part in indices_input.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    indices.extend(range(start, end + 1))
                else:
                    indices.append(int(part))
            allowed_labels = [YOLOV8_COCO_CLASSES[i - 1] for i in indices if 1 <= i <= 80]
            print(f"✓ 选择了 {len(allowed_labels)} 个类别")
        except Exception as e:
            print(f"解析失败，使用全部类别: {e}")
            allowed_labels = []
    else:
        print("✓ 检测所有 COCO 类别")
    print()

    # 为每个找到的模型创建配置
    print("=" * 70)
    print("配置模型")
    print("=" * 70)
    print()

    model_names = {
        "yolov8n.onnx": "YOLOv8 Nano (快速)",
        "yolov8s.onnx": "YOLOv8 Small (平衡)",
        "yolov8m.onnx": "YOLOv8 Medium (精确)",
        "yolov8l.onnx": "YOLOv8 Large (高精度)",
        "yolov8x.onnx": "YOLOv8 XLarge (最高精度)",
    }

    for model_path in available_models:
        model_filename = model_path.name
        model_display_name = model_names.get(model_filename, f"YOLOv8 ({model_filename})")

        try:
            create_yolov8_config(
                config_file,
                model_filename,
                model_display_name,
                device,
                confidence,
                allowed_labels if allowed_labels else None
            )
        except Exception as e:
            print(f"✗ 配置失败 {model_filename}: {e}")

    print()
    print("=" * 70)
    print("配置完成！")
    print("=" * 70)
    print()
    print("下一步:")
    print("1. 重启 Web 服务:")
    print(f"   poetry run gripper-ai-controller web --config-file {config_file.relative_to(project_root)}")
    print()
    print("2. 在浏览器中查看检测效果")
    print()
    print("提示:")
    print("  - 如果检测不到物体，尝试降低置信度阈值")
    print("  - 如果误检太多，尝试提高置信度阈值")
    print("  - 可以在 Web UI 中切换不同的模型")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户取消")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
