# 插件相机绑定

## 目的

网页预览 Plugin 通过 `Plugin` 基类的正式相机输入契约声明需要的逻辑相机来源，避免前端根据 Plugin 名称猜测某个模块是否依赖相机。该契约只描述被动帧输入，不授予 Plugin 相机 SDK、设备选择、采集、参数写入或任何机器人/夹爪权限。

## 核心契约

- `CameraBindingRequirement`：由 Plugin 类声明输入模式、最小来源数与最大来源数。
- `CameraBinding`：由 `PluginFactoryDescriptor` 持有实际绑定的逻辑 `camera_id` 列表。
- `PluginHost`：启动时核对 Plugin 声明与受信任描述符一致；分发 `FrameCaptured` 前按绑定的逻辑 `camera_id` 过滤帧。
- `PluginStatus` 与 `GET /api/plugins`：只读返回 `camera_binding`。字段为 `mode`、`camera_ids`、`minimum_sources`、`maximum_sources` 和 `state`。

逻辑 `camera_id` 是应用配置中的稳定采集源标识。它不是厂商 SDK 的物理 `device_id`、序列号或传输层句柄。

## 当前运行边界

当前网页服务只创建一条采集循环、一个 `FrameHub` 和一个逻辑 `camera_id`。三个内置视觉 Plugin 都声明：

- `mode: "shared_single_source"`
- `minimum_sources: 1`
- `maximum_sources: 1`

左侧每个视觉 Plugin 的“相机输入”面板可调用现有物理设备选择接口，但它选择的是这条共享采集流当前使用的设备。一次成功切换会同时影响中央 MJPEG、人体姿态、工件位姿和通用检测，并会清空旧分析缓存。该控件不是 Plugin 私有相机绑定，也不会同时打开两台相机。

无相机需求的 Plugin 保持 `mode: "none"`，`camera_binding` 在 HTTP 响应中为 `null`，页面不显示相机输入面板。

## 多相机扩展约束

`plugin_sources` 模式与多个逻辑 `camera_id` 的数据契约已预留，但当前运行时不提供独立多相机采集。实现真正多相机前必须同时完成：

1. 每个逻辑源独立拥有 `VisionAdapter`、采集任务、帧缓存与来源版本。
2. 配置和 API 只接受已注册的逻辑 `camera_id`，不得接受厂商物理 `device_id` 作为 Plugin 绑定。
3. `PluginHost` 对更新后的绑定执行数量、可用性和标定要求校验，并在切换时只清理受影响 Plugin 的缓存。
4. 工件位姿为每台相机维护独立背景、内参、平面标定与 `board -> jaka_base` 映射。
5. 中央预览与 Canvas 叠加按来源隔离，禁止把另一台相机的结果绘制到当前画面。

在上述采集和隔离能力完成前，页面不会提供看似可用的多选相机控件。

## 验证

Windows 下从项目根目录使用项目批处理入口：

```cmd
scripts\test.bat tests.test_plugin_host tests.test_plugin_web
scripts\frontend.bat build
scripts\frontend.bat test object-pose.spec.ts
```
