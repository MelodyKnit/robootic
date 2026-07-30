"""下载 YOLOv8 ONNX 模型用于通用物体检测。

YOLOv8 是 Ultralytics 的最新 YOLO 系列模型，在 COCO 数据集上预训练。
虽然 COCO 不包含 wrench，但 YOLOv8 的特征提取器更强，可能将扳手识别为相似物体。
"""

import urllib.request
from pathlib import Path
import sys

def download_yolov8_onnx(output_dir: str = "localstore/models") -> None:
    """下载 YOLOv8n ONNX 模型（最小版本，适合 CPU 推理）。"""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # YOLOv8n 是最轻量的版本，适合 CPU 实时推理
    model_url = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.onnx"
    model_file = output_path / "yolov8n.onnx"

    if model_file.exists():
        print(f"✓ 模型已存在: {model_file}")
        return

    print(f"正在下载 YOLOv8n ONNX 模型...")
    print(f"URL: {model_url}")
    print(f"目标: {model_file}")

    try:
        # 使用 urllib 下载（避免依赖 requests）
        with urllib.request.urlopen(model_url, timeout=300) as response:
            total_size = int(response.headers.get('content-length', 0))
            print(f"文件大小: {total_size / (1024*1024):.1f} MB")

            downloaded = 0
            chunk_size = 8192
            with open(model_file, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = downloaded / total_size * 100
                        print(f"\r进度: {percent:.1f}% ({downloaded/(1024*1024):.1f} MB)", end='')

        print(f"\n✓ 下载完成: {model_file}")
        print(f"✓ 文件大小: {model_file.stat().st_size / (1024*1024):.1f} MB")

    except urllib.error.URLError as e:
        print(f"\n✗ 下载失败: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 发生错误: {e}", file=sys.stderr)
        if model_file.exists():
            model_file.unlink()  # 删除不完整的文件
        sys.exit(1)

if __name__ == "__main__":
    download_yolov8_onnx()
