"""下载 YOLOv8 ONNX 模型到 localstore/models/"""

import sys
from pathlib import Path
from urllib.request import urlretrieve
from urllib.error import URLError


def download_with_progress(url: str, output_path: Path) -> None:
    """下载文件并显示进度"""
    def progress_hook(block_count, block_size, total_size):
        downloaded = block_count * block_size
        if total_size > 0:
            percent = min(100, downloaded * 100 // total_size)
            mb_downloaded = downloaded / (1024 * 1024)
            mb_total = total_size / (1024 * 1024)
            print(f"\r下载进度: {percent}% ({mb_downloaded:.1f}MB / {mb_total:.1f}MB)", end="")
        else:
            mb_downloaded = downloaded / (1024 * 1024)
            print(f"\r已下载: {mb_downloaded:.1f}MB", end="")

    try:
        print(f"正在下载: {url}")
        print(f"保存到: {output_path}")
        urlretrieve(url, output_path, reporthook=progress_hook)
        print("\n✓ 下载完成")
    except URLError as e:
        print(f"\n✗ 下载失败: {e}")
        raise


def main():
    # 项目根目录
    project_root = Path(__file__).parent.parent
    models_dir = project_root / "localstore" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("YOLOv8 ONNX 模型下载工具")
    print("=" * 60)
    print()

    # 定义要下载的模型
    models = [
        {
            "name": "YOLOv8n (Nano)",
            "description": "最轻量级，速度最快，适合实时检测",
            "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.onnx",
            "filename": "yolov8n.onnx",
            "size": "~6MB"
        },
        {
            "name": "YOLOv8s (Small)",
            "description": "平衡速度和精度，推荐用于一般场景",
            "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.onnx",
            "filename": "yolov8s.onnx",
            "size": "~22MB"
        },
        {
            "name": "YOLOv8m (Medium)",
            "description": "更高精度，适合对准确率要求高的场景",
            "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8m.onnx",
            "filename": "yolov8m.onnx",
            "size": "~52MB"
        }
    ]

    print("可选模型:")
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']} ({model['size']})")
        print(f"   {model['description']}")
        print()

    # 默认下载 nano 和 small
    default_choice = "1,2"
    choice = input(f"请选择要下载的模型 (用逗号分隔，默认 {default_choice}): ").strip()
    if not choice:
        choice = default_choice

    try:
        indices = [int(x.strip()) - 1 for x in choice.split(",")]
    except ValueError:
        print("输入无效，使用默认选择")
        indices = [0, 1]

    print()
    print("=" * 60)
    print("开始下载")
    print("=" * 60)
    print()

    success_count = 0
    for idx in indices:
        if 0 <= idx < len(models):
            model = models[idx]
            output_path = models_dir / model["filename"]

            if output_path.exists():
                print(f"⊙ {model['name']} 已存在，跳过")
                success_count += 1
                continue

            try:
                download_with_progress(model["url"], output_path)
                success_count += 1
            except Exception as e:
                print(f"跳过 {model['name']}")
            print()

    print("=" * 60)
    print(f"完成！成功: {success_count}/{len(indices)}")
    print("=" * 60)
    print()

    if success_count > 0:
        print("模型文件位置:")
        for file in models_dir.glob("yolov8*.onnx"):
            size_mb = file.stat().st_size / (1024 * 1024)
            print(f"  - {file.name} ({size_mb:.1f}MB)")
        print()
        print("下一步: 运行 scripts/configure_yolov8_detection.py 来配置项目")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)
