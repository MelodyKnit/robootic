# 夹爪 AI 控制器

本项目是一个基于 Python 3.7、无硬件依赖的视觉引导机器人及夹爪工作站基础框架。其运行时采用 NoneBot2 风格的分离架构：小型核心负责生命周期、类型化事件、安全授权和指令调度；适配器和插件均为可独立更新的项目内模块。

默认开发图是安全自洽的：使用内存中的机器人、夹爪、镜像、相机、规划和感知组件，从不导入厂商 SDK、打开端口、采集真实图像或控制物理设备。

## 环境要求

- Conda 环境：`robotic`
- Python：`>=3.7,<3.8`，64 位
- 包管理器：Poetry
- 仿真通信依赖：`pyzmq==25.1.2`、`cbor==1.0.0`

Python 版本限制有意比 `^3.7` 更严格：随附的 JAKA `jkrc` 扩展针对 Python 3.7 64 位编译。

CoppeliaSim 4.10 自带的 `coppeliasim-zmqremoteapi-client` 2.0.4 发布包要求 Python 3.8 或更高，因此不能直接加入本项目。已安装的 `pyzmq` 与 `cbor` 是其 ZeroMQ 协议所需的 Python 3.7 兼容传输依赖；后续实现 `CoppeliaSimAdapter` 时，必须将与本机 CoppeliaSim 版本匹配的官方客户端源码复制到本子项目内，不得从全局安装目录导入。

## 运行

```powershell
conda activate robotic
cd projects/gripper-ai-controller
pipx run --spec "poetry==1.8.5" poetry install
poetry run python -m gripper_ai_controller run --config-file configs/development.json --objective "Pick the detected workpiece"
poetry run python -m gripper_ai_controller run --config-file configs/tool-camera.json
poetry run python -m gripper_ai_controller reload --config-file configs/development.json --module gripper_ai_controller.plugins.audit
poetry run python -m unittest discover -s tests -v
```

当前全局 Poetry 2.3.4 无法向 Python 3.7 目标环境注入其解释器探测脚本。项目依赖安装应使用上面的 Poetry 1.8.5 一次性运行命令；它仍由 Poetry 管理依赖，不会使用裸 `pip` 修改项目环境。

`reload` 仅在开发模式下可用。生产配置在有明确的本地硬件适配器实现之前，刻意保持"故障关闭"状态。

## 组件模型

- `core/`：异步运行时、注册表、类型化事件总线、目标调度器和重载生命周期。
- `adapters/`：机器人、夹爪或相机集成。适配器负责获取数据或执行已授权指令，不含规划算法。
- `plugins/`：感知、规划和审计模块。插件可观察类型化事件并提出规范化指令，但不可调度硬件指令。
- `configs/`：版本化 JSON 运行配置文件的统一入口。
- `bootstrap/`：读取、校验 JSON 并组装运行时图的 Python 代码。
- `data/`：应随代码提交的小型、非敏感、可复现数据和模板。
- `localstore/`：Git 忽略的本机运行数据、标定结果、采集物和私密覆盖。

一个执行目标包含一个机器人适配器和一个夹爪适配器。其中一个目标是 `primary`（主目标），其遥测数据具有权威性。镜像目标接收相同的已批准指令 ID，并通过主目标遥测数据进行修正，从而允许未来的 CoppeliaSim 数字孪生安全地跟随真实执行。

## 视觉边界

`VisionAdapter` 仅返回 `ImageFrame` 数据。感知插件将帧转换为检测到的物体、2D 框、置信度、3D 姿态和机器人基座抓取候选。

内置确定性插件支持两种标定拓扑：

- 固定外部相机：`camera -> robot_base`；
- 工具端安装相机：`camera -> tool0`，结合抓取时的机器人位姿进行合成。

只有经过标定、时效内、置信度足够的 `robot_base` 抓取候选才能进入规划和安全授权阶段。

## 添加真实组件

1. 将所需的 JAKA、DH Robotics、Hikvision 或 CoppeliaSim SDK 文件复制到本子项目中。严禁从 `documents/` 导入。
2. 在专用的 `adapters/<provider>/` 包中实现相应的生命周期适配器。
3. 为其赋予唯一的 `ComponentManifest` 并在 `configs/` 的 JSON 文件中显式启用。
4. 将所有运动路径置于 `Runtime` 和 `SafetyPolicy` 保护之下。
5. 在允许物理连接之前，添加离线测试和一个显式启用的硬件冒烟测试。

有关事件和生命周期合约，请参见 [architecture.md](docs/architecture.md)。
