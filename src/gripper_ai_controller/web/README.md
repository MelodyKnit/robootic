# 相机网页预览后端

本包提供只读 FastAPI 相机预览服务。它只启动配置的 `VisionAdapter`，以单一采集循环获取帧、在内存中编码为 JPEG，并通过快照接口和 MJPEG 接口提供给浏览器。

该服务不会创建 `Runtime`，不会启动执行目标，不会连接 JAKA、夹爪或其他命令适配器，也不会将帧写入磁盘。

## 接口

- `GET /api/cameras`：配置的相机列表及预览状态。
- `GET /api/cameras/{camera_id}/status`：相机状态。
- `GET /api/cameras/{camera_id}/frame`：最新 JPEG 快照；首帧未准备好时返回 `503`。
- `GET /api/cameras/{camera_id}/stream`：MJPEG 连续流。

未知相机返回 `404`，错误 JSON 使用 `code` 和 `message` 字段。服务默认按配置绑定 `0.0.0.0`，首版没有认证，只能部署在受信任局域网。

## 图像契约

`ImageFrame` 必须提供 `pixel_payload`、`width`、`height` 与 `pixel_format`。网页编码器接受 `rgb8` 或 `mono8`，不保存原始数据。海康 MVS 客户端直接保留 `Mono8` 为 `mono8`、直接复制 `RGB8` 为 `rgb8`，并在 MVS 缓冲区释放前将其他受支持的彩色格式转换为 `rgb8`；仿真相机提供内存中的确定性 RGB 测试图。

相机在启动、取帧或 JPEG 编码失败时，服务保留最近一次成功帧并公开 `degraded` 状态，然后按 `capture_retry_seconds` 关闭并重新打开适配器。服务不将帧、错误或设备状态写入 `data/`、`localstore/` 或日志文件。

## 启动

先构建前端，再以显式配置启动：

```powershell
pnpm --dir src/web build
poetry run gripper-ai-controller web --config-file configs/development.json
```

CLI 可用 `--host`、`--port` 和 `--frontend-dist-dir` 临时覆盖对应 `web` 配置；端口必须为 `1` 至 `65535`。未构建前端目录时，服务仍提供 `/api`，但不会提供根网页入口。

使用真实海康相机时，从版本化模板复制本机配置到 `localstore/`，并确保官方 MVS SDK 已按项目文档复制到本机适配器目录。版本化配置、代码和接口文档不得包含真实相机序列号或采集帧。
