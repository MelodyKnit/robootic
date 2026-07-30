# 核心运行时

`core` 是唯一允许协调状态转换和调度指令的层级。它实现了本项目所用的外观、观察者、命令和适配器编排模式。

## 职责

- `runtime.py`：启动和停止组件、收集感知数据、调用规划器、应用 `SafetyPolicy`、应用主机器人可选的专用运动约束，以及调度已批准的指令。
- `events.py`：定义类型化生命周期和执行事件，以及确定性的顺序事件投递。
- `targets.py`：将一个机器人适配器和一个夹爪适配器绑定为主目标或镜像目标。
- `registry.py`：按稳定的清单名称记录已配置的组件实例。
- `components.py`：定义受约束的插件合约，以及 Plugin 的逻辑相机输入声明。
- `plugin_host.py`：管理网页预览 Plugin 的启动、关闭、帧事件投递、状态查询和受限开发重载。

插件可以观察事件并提出规范化指令；但它们不能访问目标或绕过安全策略。主目标具有权威性。镜像目标是可视化或分析目标，通过主目标遥测数据进行修正。

机器人适配器可为执行目标提供 `RobotMotionConstraint`。运行时仅在通用安全策略已通过后调用该约束，并且在发布 `CommandAuthorized` 前拒绝越界、速度异常、步长异常或不受支持的动作。JAKA 干运行目标用同一个纯命令编译器同时完成预览和约束检查，因此规划器无法绕过其关节限制；没有专用约束的适配器维持现有通用安全策略。机器人 `STOP` 是恢复动作：连接且已初始化的目标可在感知结果过期或无效时接受停止，但真实 JAKA 适配器依旧拒绝实际运动接口。

网页预览 Plugin 使用独立的 `PluginHost`，不加入 `Runtime` 指令调度图。它只接收 JPEG 发布后的 `FrameCaptured` 事件，不能访问执行目标、适配器客户端或人工控制门面。当前受信任注册表包含 `visual-pose-analysis`、`object-pose-analysis` 与 `object-detection-analysis`；新增网页 Plugin 必须先在服务端注册固定工厂。每个 Plugin 通过 `CameraBindingRequirement` 声明所需的逻辑相机输入，宿主依据受信任 `CameraBinding` 路由帧，而不是让每个模块自行选择物理设备。当前只有一条共享采集流，具体边界见 [插件相机绑定](../../../docs/plugin-camera-binding.md)。采集循环、JPEG 缓存和 MJPEG 流不因 Plugin 刷新或重载而重建。重载只允许 `development` 模式下的本机回环服务，且必须由 `web.plugin_reload_enabled` 显式开启；生产模式始终要求完整进程重启。

从项目根目录使用 `scripts\test.bat` 运行包测试套件。
