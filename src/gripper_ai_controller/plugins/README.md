# 插件

插件通过受约束的生命周期钩子和类型化事件扩展行为。它们不导入适配器实例，不能直接调度硬件指令，也不能把任何指令标记为已授权。

## 运行时插件

- 感知插件：将 `ImageFrame` 数据转换为物体、姿态和抓取候选。
- 规划器插件：将有效的 `PerceptionResult` 转换为规范化指令提案。
- 观察者插件：记录或报告类型化运行时事件。

这些插件由完整 `Runtime` 组装，并通过 `components.plugins.perception`、`planners` 和 `observers` 显式选择。生产更新需要重启；开发重载由运行时生命周期协调，且不能在指令调度期间发生。

## 网页预览插件

`components.plugins.preview` 只用于独立的网页预览服务。当前受信任注册表只包含内置模块 `visual-pose-analysis`；配置其他标识会被服务端拒绝。该模块接收采集循环发布的 `FrameCaptured` 事件，组合 `PoseTrackingService` 和 `VisionAnalysisService`，维护供 `/pose` 与 `/vision/analysis` 读取的快照。它不持有 `VisionAdapter`、厂商 SDK、夹爪/JAKA 适配器或人工控制门面，因此无法读取或更改硬件状态、设备参数，也不能发送任何控制命令。

网页 Plugin 通过 `PluginHost` 保持独立生命周期。运行中的慢分析只保留最新待处理帧，重载时暂停该 Plugin 的新帧投递而不停止相机或重建 MJPEG；新实例启动失败时保留旧实例。每个 Plugin 声明 `ComponentManifest`，配置使用稳定组件标识符而不是模块路径；新增标识还必须同步加入服务端受信任工厂注册表。仅本机回环地址的开发模式，且 `web.plugin_reload_enabled` 为真时，网页重载接口才可使用；生产环境必须重启服务。
