# 已知工件位姿只读接口

`GET /api/cameras/{camera_id}/objects` 只读取 `object-pose-analysis` 插件已经缓存的结果。请求不会触发相机采集、背景差分、几何投影、JAKA 连接、夹爪连接或任何设备命令。

未知逻辑相机返回标准 `404` 错误。未配置插件、插件关闭、尚无帧、标定无效或结果过期时都返回 `200` 和安全的空 `objects`，由 `enabled`、`valid`、`reason` 与 `overlay_fresh` 表示状态。

```json
{
  "camera_id": "hikvision-usb",
  "enabled": true,
  "captured_at": 1785316200.0,
  "latest_frame_at": 1785316200.1,
  "overlay_fresh": true,
  "valid": true,
  "reason": "object_pose_available",
  "inference_latency_ms": 12.4,
  "objects": [
    {
      "profile_id": "known-workpiece-v1",
      "confidence": 0.93,
      "bounding_box": { "x": 0.2, "y": 0.3, "width": 0.2, "height": 0.1 },
      "contour": [{ "x": 0.2, "y": 0.3 }],
      "pixel_center": { "x": 1224.0, "y": 1024.0 },
      "normalized_center": { "x": 0.5, "y": 0.5 },
      "coordinate_frame": "jaka_base",
      "translation_mm": { "x": 100.0, "y": -20.0, "z": 4.0 },
      "orientation_rpy_rad": { "roll": 0.0, "pitch": 0.0, "yaw": 0.25 },
      "observed_dof": ["x", "y", "yaw"],
      "derived_dof": ["z", "roll", "pitch"],
      "yaw_period_rad": 3.141592653589793,
      "orientation_defined": true,
      "warning": "orientation_pi_ambiguous"
    }
  ]
}
```

`translation_mm` 是档案指定抓取原点在 `jaka_base` 下的位置；它不是命令或抓取授权。视觉直接观测的自由度仅为 `X/Y/Yaw`。`Z/Roll/Pitch` 来自固定台面、工件厚度、抓取高度及标定变换，响应中的 `observed_dof` 与 `derived_dof` 必须由后续任何动作层分别检查。

`yaw_period_rad == pi` 表示头尾歧义，不能作为单向夹取依据；只有 `2*pi` 表示已通过档案头尾规则。`planarity_suspected` 表示投影尺寸与实测档案不一致，可能是倾斜、遮挡、粘连或错误物体，接口会返回空对象而非低可信坐标。
