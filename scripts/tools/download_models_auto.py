"""自动下载 YOLOv8 模型（非交互模式）"""
from pathlib import Path
from urllib.request import urlretrieve
import sys

def main():
    models_dir = Path(__file__).parent.parent / 'localstore' / 'models'
    models_dir.mkdir(parents=True, exist_ok=True)

    models = [
        ('yolov8n.onnx', 'https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.onnx'),
        ('yolov8s.onnx', 'https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8s.onnx'),
    ]

    print("开始下载 YOLOv8 模型...\n")

    for filename, url in models:
        output_path = models_dir / filename
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f'✓ {filename} 已存在 ({size_mb:.1f}MB)')
        else:
            print(f'下载 {filename}...')
            try:
                urlretrieve(url, output_path)
                size_mb = output_path.stat().st_size / (1024 * 1024)
                print(f'✓ {filename} 下载完成 ({size_mb:.1f}MB)')
            except Exception as e:
                print(f'✗ {filename} 下载失败: {e}')
                return 1

    print('\n所有模型准备就绪！')
    print(f'模型位置: {models_dir}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
