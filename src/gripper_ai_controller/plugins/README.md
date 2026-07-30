# 插件

插件通过受约束的生命周期钩子和类型化事件扩展行为。它们不导入适配器实例，不能直接调度硬件指令，也不能把任何指令标记为已授权。

## 运行时插件

- 感知插件：将 `ImageFrame` 数据转换为物体、姿态和抓取候选。
- 规划器插件：将有效的 `PerceptionResult` 转换为规范化指令提案。
- 观察者插件：记录或报告类型化运行时事件。

这些插件由完整 `Runtime` 组装，并通过 `components.plugins.perception`、`planners` 和 `observers` 显式选择。生产更新需要重启；开发重载由运行时生命周期协调，且不能在指令调度期间发生。

## 网页预览插件

`components.plugins.preview` 只用于独立的网页预览服务。当前受信任注册表包含内置模块 `visual-pose-analysis`、`object-pose-analysis` 与 `object-detection-analysis`；配置其他标识会被服务端拒绝。三个模块都只接收采集循环发布的 `FrameCaptured` 事件：

- `visual-pose-analysis` 组合 `PoseTrackingService` 和 `VisionAnalysisService`，维护供 `/pose` 与 `/vision/analysis` 读取的人体姿态和成像分析快照。
- `object-pose-analysis` 组合空桌背景差分、已知工件几何档案与离线平面标定，维护供 `/objects` 读取的已知平放工件 `X/Y/Yaw`；`Z/Roll/Pitch` 仅由台面约束推导。
- `object-detection-analysis` 调用配置白名单中的本地 Faster R-CNN 或提示类别已固化的 YOLO-World ONNX，维护供 `/detections` 读取的二维类别框。它不输出轮廓、抓取点、深度、位姿或机器人坐标。

三个模块均不持有 `VisionAdapter`、厂商 SDK、夹爪/JAKA 适配器、人工控制门面或命令权限，因此无法读取或更改硬件状态、设备参数，也不能发送控制命令。通用检测的模型文件必须由操作者显式置于被 Git 忽略的 `localstore/`；Plugin 不自动下载、转换或更新权重。

网页 Plugin 通过 `PluginHost` 保持独立生命周期。`components.plugins.preview` 只声明固定可用集合；显式 `localstore/` 启动 JSON 根对象的 `plugin_runtime.enabled` 才保存每个已配置 Plugin 的运行状态。该映射的值必须是严格布尔值，省略映射或某个 ID 时默认开启，以兼容已有本机配置。页面刷新与 `GET` 状态查询只读取状态，不能启动、停止或重置任何 Plugin。

当 `web.plugin_lifecycle_controls_enabled: true`、服务绑定 `127.0.0.1` 且使用显式 `localstore/` JSON 时，网页可持久化更新已配置 Plugin 的启停状态。关闭一个 Plugin 仅停止它的被动分析任务和新帧投递；不会停止相机、重建 MJPEG、重新连接适配器，或访问夹爪/JAKA 控制路径。重新开启时只启动同一受信任工厂创建的已配置 Plugin，浏览器不能提供模块路径、工厂名或其他任意代码。

运行中的慢分析只保留最新待处理帧，重载时暂停该 Plugin 的新帧投递而不停止相机或重建 MJPEG；新实例启动失败时保留旧实例。每个 Plugin 声明 `ComponentManifest`，配置使用稳定组件标识符而不是模块路径；新增标识还必须同步加入服务端受信任工厂注册表。仅本机回环地址的开发模式，且 `web.plugin_reload_enabled` 为真时，网页重载接口才可使用；生产环境必须重启服务。生命周期启停与代码重载是独立操作。

网页服务只有一个逻辑相机、一个 `FrameHub` 和一条物理采集链路。三个内置视觉 Plugin 都通过 `CameraBindingRequirement` 声明一个 `shared_single_source` 输入，`PluginHost` 在分发前按受信任的逻辑 `camera_id` 路由帧。Plugin 详情中的相机选择器复用全局 `CameraCatalog`；切换设备会清空各分析模块的旧帧状态并影响中央 MJPEG 和所有 Plugin，不能把该选择理解为 Plugin 独占绑定，也不会同时打开两台相机。契约和多相机前置条件见 [插件相机绑定](../../../docs/plugin-camera-binding.md)。

`object-detection-analysis` 的模型选择也不是配置持久化接口。它只在当前服务进程中切换已配置且本地文件存在的提供器，并在切换时清除旧模型框；服务重启或 Plugin 重载后重新使用 `object_detection.selected_model_id`。详细模型边界和 HTTP 契约见 [通用二维目标检测说明](../object_detection/README.md) 与 [通用二维目标检测接口](../../../docs/object-detection-api.md)。
