# 适配器

适配器将项目合约转换为具体的设备、模拟器或帧源。它拥有连接生命周期、厂商特定的值转换和状态获取。不得包含任务规划、LLM 提示词或直接的跨适配器编排。

每个适配器必须：

1. 实现 `domain/ports.py` 中的对应端口。
2. 声明唯一的 `ComponentManifest`。
3. 实现安全的异步启动和关闭行为。
4. 返回规范化状态、帧和错误，而非泄漏厂商 SDK 类型。
5. 仅通过 `configs/` 模块组装。

`base.py` 中的 `BaseAdapter` 统一处理幂等的异步启动、关闭和启动状态检查。厂商适配器只能在其生命周期钩子中建立或释放连接；不得让 `initialize()`、状态读取或默认运行图隐式改变硬件状态。

厂商 SDK 必须复制到对应适配器子目录，不能从工作区 `documents/` 动态导入。项目内本机副本不等同于可再分发资产：没有明确授权的厂商二进制或封装必须由 `.gitignore` 和 Poetry 排除规则保护，克隆后由使用者从官方 SDK 重新复制。

## 帧观察者

`VisionAdapter.on_frame()` 为每个相机实例注册异步帧观察者。所有项目内相机均通过 `FrameDispatchingVisionAdapter` 实现该契约；观察者在一次 `capture()` 成功构造出完整的 `ImageFrame` 后、返回给调用方前按注册顺序执行。

多相机运行时不存在隐式的全局默认相机，因此必须先绑定具体实例。下面的别名写法支持简洁的装饰器形式：

```python
on_frame = camera.on_frame

@on_frame()
async def _(frame):
    await consume(frame)

frame = await camera.capture()
```

注册观察者不会启动后台连续取流，也不会改变相机配置。观察者必须使用 `async def` 定义；其异常会返回给本次 `capture()` 调用方，但不会被误标为底层相机设备故障。运行时仍会在 `capture()` 返回后单独发布 `FrameCaptured` 事件，供插件和审计组件使用。

`RobotStatus` 还必须明确报告 `connected`、`powered` 和 `enabled`。安全策略仅会为已初始化、已连接、已上电、已使能且无故障、无急停的机器人授权未来运动；这些状态不能由规划器伪造。

`jaka/` 包含真实 `JakaAdapter` 和离线 `JakaDryRunRobotAdapter`。真实适配器将官方 Python SDK 保留在项目内，并将连接、使能和运动严格分离：默认仅允许连接与遥测读取，任何运动指令均被拒绝。干运行适配器不导入 SDK 或打开网络连接，只在内存中用 JAKA Zu 3 软件限位编译和预测六轴关节命令；它通过 `RobotMotionConstraint` 让运行时在授权前复用相同限制，不能作为真机控制实现。

`hikvision/` 是 USB3 Vision 帧源和受限参数适配器。它将 MVS Python 封装与 Windows x64 运行库保留在项目内，并通过独立的 `CameraParameterAdapter` 端口公开固定白名单的运行时参数；不包含感知算法，不接受任意 MVS 节点、触发设置或设备持久化相机配置。网页服务在设备更新成功后才将实际生效值写回显式本机 JSON 的 `camera_parameters`，并在启动和重连的首帧前恢复；适配器本身不调用厂商持久化命令。具体白名单、并发锁和网页访问许可见 [海康适配器说明](hikvision/README.md)。

支持网页物理设备切换的视觉适配器额外实现 `SelectableVisionAdapter`。该端口只返回硬件无关的 `CameraDeviceDescriptor`，并且只允许在适配器停止后改变选择；它不负责启动新设备或持久化配置。网页服务负责把发现、关闭、选择、启动、参数恢复、旧缓存清理和失败回滚串行化。适配器生成的 `device_id` 必须稳定且不暴露厂商序列号。

`pgi/` 使用标准库 socket 封装项目原始资料中已经展示的 PGI TCP 网关协议。它仅支持连接、普通初始化、目标力、目标位置和状态读取；未验证的速度、软件停止和全行程重新标定不会暴露给运行时或网页。具体生命周期、协议校验和真机安全边界见 [PGI TCP 适配器说明](pgi/README.md)。

未来的 CoppeliaSim 实现拥有自己的适配器子包。在实现前将其厂商库复制到本项目中；不要从 `documents/` 动态导入源码资产。
