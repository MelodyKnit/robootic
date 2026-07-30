"""从 HuggingFace 下载 YOLOv8n ONNX 模型"""
import urllib.request
import os

url = "https://huggingface.co/Ultralytics/YOLOv8/resolve/main/yolov8n.onnx"
output = "localstore/models/yolov8n.onnx"

print(f"正在从 HuggingFace 下载 YOLOv8n...")
print(f"URL: {url}")

try:
    urllib.request.urlretrieve(url, output)
    size_mb = os.path.getsize(output) / (1024 * 1024)
    print(f"✓ 下载完成: {size_mb:.1f} MB")
    print(f"保存到: {output}")
except Exception as e:
    print(f"✗ 下载失败: {e}")
    exit(1)
