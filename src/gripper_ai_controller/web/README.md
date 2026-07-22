# 相机网页预览后端

本包提供独立的 FastAPI 相机预览和受限参数服务。它只启动配置的 `VisionAdapter`，以单一采集循环获取帧、在内存中编码为 JPEG，并通过快照和 MJPEG 接口提供给浏览器。

服务不会创建 `Runtime`，不会启动执行目标，不会连接 JAKA、夹爪或其他命令适配器，也不会将帧写入磁盘。即使启用了相机参数写入，网页也没有机器人或夹爪控制路径。

## 接口

- `GET /api/cameras`：配置的相机列表及预览状态。
- `GET /api/cameras/{camera_id}/status`：相机状态，字段为 `camera_id`、`state`、`latest_frame_at` 和 `error`；不提供帧序号。
- `GET /api/cameras/{camera_id}/frame`：最新 JPEG 快照；首帧未准备好时返回 `503`。
- `GET /api/cameras/{camera_id}/stream`：MJPEG 连续流；所有浏览器复用同一采集循环和最新 JPEG 缓存。
- `GET /api/cameras/{camera_id}/parameters`：读取当前设备实际支持的固定参数白名单、取值范围、选项及应用方式。
- `PATCH /api/cameras/{camera_id}/parameters/{parameter_key}`：立即应用一个 `live` 参数，请求体为 `{ "value": ... }`。设备成功后，实际生效值会写回显式配置的 `camera_parameters`。
- `POST /api/cameras/{camera_id}/parameters/apply`：提交一个或多个 `restart` 参数，请求体为 `{ "values": { "pixel_format": "Mono8" } }`。后端会停止取流、写入、更新配置并恢复取流。

参数列表响应包含 `camera_id`、`write_enabled` 和 `parameters`。每个参数带有 `key`、`kind`、`apply_mode`、`value`、可选的数值范围和单位，以及枚举选项。更新响应额外带有 `restarted_acquisition`。未知相机返回 `404`；写入开关未启用时返回 `403`；适配器未提供控制能力时返回 `409`；格式、范围或应用方式错误返回 `422`。设备成功应用参数但无法写回配置时返回 `503`，错误 `code` 为 `camera_parameter_persistence_failed`，含义是设备已生效而配置未保存。所有 JSON 错误均为 `code` 和 `message`。

## 参数安全边界

`CameraParameterAdapter` 是独立于 `VisionAdapter` 取帧职责的可选能力端口。网页服务仅通过该端口访问参数，不持有厂商 SDK 客户端，也不会接受任意节点名称。

海康首版白名单为自动曝光、曝光时间、自动增益、增益、帧率开关、帧率和像素格式。实际数值范围与枚举选项均从已连接设备读取；当前设备不支持或不可读的节点不会出现在列表中。自动曝光或自动增益开启时，后端与页面都会拒绝对应手动参数；帧率设置要求帧率控制已启用。像素格式被标记为 `restart`，只能通过明确的保存操作应用。

同一异步操作锁覆盖取帧、节点读取、节点写入、停止取流、恢复取流和关闭相机，避免 MVS 句柄同时被原生取帧和重配置访问。设备成功写入后，服务将适配器确认的本次实际值及其必要前置开关原子写回启动 CLI 显式传入 JSON 的根 `camera_parameters`；适配器启动和每次断连重连后，会在首帧前恢复该对象。恢复失败会使预览进入可重试的降级状态，而非静默使用默认参数。此配置持久化绝不调用 `FeatureSave`、`UserSetSave` 或其他厂商设备持久化命令。

`web.camera_controls_enabled` 同时约束浏览器写入和自动恢复。将它设为 `false` 后，服务仍可只读预览，但即使 JSON 中保留旧的 `camera_parameters`，启动或重连也不会向设备写入参数。

浏览器刷新仅重新读取状态、参数能力和视频流，不会重置设备参数或改写 `camera_parameters`。

`web.camera_controls_enabled` 默认为 `false`。应只在被 Git 忽略的 `localstore/` 实机配置中显式设为 `true`，并将该文件作为 `--config-file` 传入。受版本控制的 `configs/` 仅为模板；若用模板直接启动，成功参数写入会修改该模板并使 Git 工作区产生变更。服务默认可按配置绑定 `0.0.0.0`，且没有认证、TLS、来源限制或访问审计；启用写入后，受信任局域网中的访问者能够更改白名单内相机参数，不能暴露到互联网。

## 图像契约

`ImageFrame` 必须提供 `pixel_payload`、`width`、`height` 与 `pixel_format`。网页编码器接受 `rgb8` 或 `mono8`，不保存原始数据。海康 MVS 客户端直接保留 `Mono8` 为 `mono8`、直接复制 `RGB8` 为 `rgb8`，并在 MVS 缓冲区释放前将其他受支持的彩色格式转换为 `rgb8`；仿真相机提供内存中的确定性 RGB 和 Mono8 测试图。

相机在启动、取帧或 JPEG 编码失败时，服务保留最近一次成功帧并公开 `degraded` 状态，然后按 `capture_retry_seconds` 关闭并重新打开适配器。服务不将帧、错误或设备状态写入 `data/`、`localstore/` 或日志文件。

## 启动

先构建前端，再以显式配置启动：

```powershell
pnpm --dir src/web build
poetry run gripper-ai-controller web --config-file configs/development.json
```

CLI 可用 `--host`、`--port` 和 `--frontend-dist-dir` 临时覆盖对应 `web` 配置；端口必须为 `1` 至 `65535`。未构建前端目录时，服务仍提供 `/api`，但不会提供根网页入口。

使用真实海康相机时，从版本化模板复制本机配置到 `localstore/`，并确保官方 MVS SDK 已按项目文档复制到本机适配器目录。版本化配置、代码和接口文档不得包含真实相机序列号或采集帧。
