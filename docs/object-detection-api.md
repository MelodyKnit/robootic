# 通用二维目标检测接口

本文说明 `object-detection-analysis` 的浏览器接口。该 Plugin 被动消费共享相机采集循环发布的帧，并维护最新检测快照；接口不会主动取帧、运行推理、下载模型、切换物理相机或调用机器人和夹爪。Faster R-CNN 权重只能由操作者在接口之外显式运行 `scripts\run.bat object-detection-download-fasterrcnn` 安装，并在完整 SHA-256 校验通过后写入 `localstore/`。

## 能力边界

通用检测只返回类别、置信度和画面归一化外接框，不返回轮廓、抓取点、深度、姿态或 `jaka_base` 坐标。需要已知单工件 `X/Y/Yaw` 和台面约束位姿时，应使用独立的 [`GET /api/cameras/{camera_id}/objects`](object-pose-api.md) 接口，并完成对应相机的背景与工作单元标定。

网页服务只有一个逻辑 `camera_id`、一个物理设备选择状态和一条采集链路。Plugin 详情中的相机选择器复用全局相机目录；切换设备会清空旧设备的检测框并影响所有预览 Plugin，不会为检测模型单独打开相机。

## 读取检测快照

`GET /api/cameras/{camera_id}/detections`

成功时返回 `200`。禁用、尚未收到帧、模型不可用、没有检测结果或推理失败时，接口仍以安全快照描述状态，并保持 `detections` 为空；读取本身不会触发采集或推理。未知逻辑相机返回 `404` 和 `camera_not_found`。

响应字段：

- `camera_id`：当前网页服务的逻辑相机标识。
- `enabled`：通用检测配置是否启用。
- `selected_model_id`：当前进程会话选择的模型，未选择时为 `null`。
- `models`：配置允许的模型目录。每项包含 `model_id`、`display_name`、`provider`、`available` 和 `selected`。`available` 只表示配置路径当前是本地文件，不代表文件内容已经完成加载或现场验证。
- `captured_at`：当前检测结果来源帧的时间戳；没有结果时为 `null`。
- `latest_frame_at`：MJPEG 最新帧时间戳；尚无画面时为 `null`。
- `overlay_fresh`：检测结果是否仍可与最新画面叠加。前端仅在该值为 `true` 时绘制框，过期时保留 MJPEG 并隐藏旧框。
- `valid`、`reason`：当前检测快照是否有效及其机器可读原因。
- `inference_latency_ms`：本次推理延迟；尚未推理时为 `null`。
- `detections`：当前来源帧的检测列表，数量受所选模型的 `max_detections` 限制。

每个检测项包含稳定于该快照的 `detection_id`、`label`、可空的 `class_id`、`confidence` 和 `bounding_box`。`bounding_box` 的 `x`、`y`、`width`、`height` 都是相对于源画面宽高的归一化数值；`x/y` 表示左上角，不是毫米坐标。

以下仅为结构示例，不表示仓库已包含模型或现场识别成功：

```json
{
  "camera_id": "hikvision-usb",
  "enabled": true,
  "selected_model_id": "yolo-world-tools-local",
  "models": [
    {
      "model_id": "yolo-world-tools-local",
      "display_name": "YOLO-World 工件提示（本地 ONNX）",
      "provider": "yolo-world-onnx-opencv",
      "available": true,
      "selected": true
    }
  ],
  "captured_at": 1785316800.125,
  "latest_frame_at": 1785316800.125,
  "overlay_fresh": true,
  "valid": true,
  "reason": "detection_available",
  "inference_latency_ms": 48.2,
  "detections": [
    {
      "detection_id": "1785316800.125000-0",
      "label": "wrench",
      "class_id": 0,
      "confidence": 0.91,
      "bounding_box": {
        "x": 0.25,
        "y": 0.30,
        "width": 0.20,
        "height": 0.35
      }
    }
  ]
}
```

## 选择模型

`PUT /api/cameras/{camera_id}/detections/model-selection`

请求体：

```json
{
  "model_id": "yolo-world-tools-local"
}
```

接口只接受当前配置 `object_detection.models` 中已经声明的模型 ID，并要求该模型的本地文件存在。成功返回 `200` 和与读取接口相同的快照结构；切换时会等待旧模型正在执行的工作结束，丢弃待处理旧帧并清空旧检测框。接口本身不运行新模型，后续结果由新到达的共享相机帧异步产生。

模型选择只在**当前服务进程会话**中生效，不写回 `selected_model_id`，也不下载或转换模型。服务重启或 Plugin 重载后会重新使用启动配置中的选择；需要持久改变默认值时，应由操作者修改显式本机配置后重启服务。

错误响应继续使用统一的 `{ "code", "message" }`：

- 未知逻辑相机：`404 camera_not_found`。
- 模型 ID 未配置：`404 detection_model_not_found`。
- Plugin/检测功能禁用，或本地模型文件不存在：`409 detection_model_unavailable`。
- `model_id` 缺失、不是字符串、为空或只有空白：FastAPI `422` 校验响应。

该接口不是设备控制接口，不选择物理相机、不修改相机参数，也不授予 JAKA 或夹爪动作权限。

## 访问与部署边界

这两个接口不提供独立认证、TLS 或访问审计，访问范围由网页服务的 `web.bind_host` 和部署网络共同决定。检测画面元数据不应直接暴露到互联网。重复选择当前模型不会重复加载或产生额外状态变化；不同模型的切换由服务串行处理，但会改变后续共享帧使用的推理提供器和计算资源占用。

响应不会返回 `model_path`、模型张量、原始图像、厂商设备序列号或任何控制令牌。页面中的二维框只是视觉候选，不得绕过后续标定、抓取规划与安全策略直接转换为机械臂或夹爪动作。
