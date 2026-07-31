"""配置 YOLOv8 物体检测"""
import json
from pathlib import Path

# COCO 80类标签
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
]

def main():
    project_root = Path(__file__).parent.parent
    config_dir = project_root / "localstore"
    config_dir.mkdir(exist_ok=True)

    print("=" * 60)
    print("YOLOv8 检测配置工具")
    print("=" * 60)
    print()

    # 检查模型文件
    models_dir = project_root / "localstore" / "models"
    available_models = list(models_dir.glob("yolov8*.onnx")) if models_dir.exists() else []

    if not available_models:
        print("⚠️  未找到 YOLOv8 ONNX 模型")
        print("请先下载模型或手动放置到 localstore/models/")
        print("推荐模型:")
        print("  - yolov8n.onnx (6MB, 快速)")
        print("  - yolov8s.onnx (22MB, 平衡)")
        print()
        model_path = "localstore/models/yolov8n.onnx"
    else:
        print("发现模型:")
        for m in available_models:
            size_mb = m.stat().st_size / (1024 * 1024)
            print(f"  ✓ {m.name} ({size_mb:.1f}MB)")
        model_path = f"localstore/models/{available_models[0].name}"
        print()

    # 创建配置
    config = {
        "camera": {
            "type": "hikvision",
            "device_index": 0,
            "width": 1920,
            "height": 1080,
            "fps": 30
        },
        "detection": {
            "engine": "yolov8-onnx",
            "model_path": model_path,
            "confidence_threshold": 0.25,
            "device": "cpu",
            "classes": list(range(80)),  # 所有COCO类
            "class_names": COCO_CLASSES
        },
        "object_pose": {
            "enabled": True,
            "min_contour_area": 500,
            "output_format": "json"
        },
        "web": {
            "host": "0.0.0.0",
            "port": 8000,
            "enable_cors": True
        }
    }

    # 常用物品配置（工业场景）
    config_industrial = config.copy()
    config_industrial["detection"]["classes"] = [39, 40, 43, 44, 73, 76]  # bottle, cup, knife, spoon, scissors, toothbrush (代表工具)
    config_industrial["detection"]["confidence_threshold"] = 0.20

    output_path = config_dir / "yolov8-detection.local.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"✓ 配置已保存: {output_path.relative_to(project_root)}")

    output_industrial = config_dir / "yolov8-industrial.local.json"
    with open(output_industrial, 'w', encoding='utf-8') as f:
        json.dump(config_industrial, f, indent=2, ensure_ascii=False)
    print(f"✓ 工业配置: {output_industrial.relative_to(project_root)}")

    print()
    print("=" * 60)
    print("配置完成！")
    print("=" * 60)
    print()
    print("启动命令:")
    print(f"  poetry run gripper-ai-controller web --config-file {output_path.relative_to(project_root)}")
    print()
    print("常用COCO类别:")
    print("  39: bottle    40: wine_glass  41: cup")
    print("  43: knife     44: spoon       45: bowl")
    print("  73: scissors  76: toothbrush")
    print()
    print("如需自定义类别，编辑配置文件中的 'classes' 字段")

if __name__ == "__main__":
    main()
