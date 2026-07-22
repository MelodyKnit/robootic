# 夹爪 AI 控制器

本项目是一个基于 Python 3.7 的视觉引导机器人及夹爪工作站基础框架。其运行时采用模块化的分离架构：小型核心负责生命周期、类型化事件、安全授权和指令调度；适配器和插件均为可独立更新的项目内模块。

默认开发图是安全自洽的：使用内存中的机器人、夹爪、镜像、相机、规划和感知组件，从不导入厂商 SDK、打开端口、采集真实图像或控制物理设备。

## 环境要求

- Conda 环境：`robotic`
- Python：`>=3.7,<3.8`，64 位
- 包管理器：Poetry
- 仿真通信依赖：`pyzmq==25.1.2`、`cbor==1.0.0`
- 相机预览运行依赖：`FastAPI==0.99.1`、`uvicorn==0.22.0`、`Pillow==9.5.0`
- 前端构建环境：Node.js 20 或更高版本、pnpm 10

Python 版本限制有意比 `^3.7` 更严格：本机复制的 JAKA `jkrc` 扩展针对 Python 3.7 64 位编译。

CoppeliaSim 4.10 自带的 `coppeliasim-zmqremoteapi-client` 2.0.4 发布包要求 Python 3.8 或更高，因此不能直接加入本项目。已安装的 `pyzmq` 与 `cbor` 是其 ZeroMQ 协议所需的 Python 3.7 兼容传输依赖；后续实现 `CoppeliaSimAdapter` 时，必须将与本机 CoppeliaSim 版本匹配的官方客户端源码复制到本子项目内，不得从全局安装目录导入。

## 运行

```powershell
conda activate robotic
cd projects/gripper-ai-controller
poetry install
poetry run python -m gripper_ai_controller run --config-file configs/development.json --objective "Pick the detected workpiece"
poetry run python -m gripper_ai_controller run --config-file configs/tool-camera.json
poetry run python -m gripper_ai_controller reload --config-file configs/development.json --module gripper_ai_controller.plugins.audit
poetry run python -m unittest discover -s tests -v
python scripts/check_submission_paths.py
```

`[project.dependencies]` 是标准依赖声明；`[tool.poetry.dependencies]` 保留相同版本，供 Python 3.7 兼容的 Poetry 1.8.5 运行器使用。由于 Python 3.7 不兼容 Poetry 2.3.4 的解释器探测依赖，Poetry 1.8.5 运行器应将其 `packaging` 固定为 23.2、`virtualenv` 固定为 20.26.6。项目依赖仍必须通过 Poetry 安装，不得使用裸 `pip` 写入 `robotic` 环境。

`reload` 仅在开发模式下可用。生产配置在有明确的本地硬件适配器实现之前，刻意保持"故障关闭"状态。

## 相机网页预览

网页预览是独立的相机服务，不会创建 `Runtime`、执行目标、安全策略、JAKA 连接或夹爪连接。它只启动配置中的一个 `VisionAdapter`，以单一采集循环把最新帧保留在内存中，并将同一 JPEG 帧复用给快照和多个 MJPEG 浏览器连接。页面不记录或展示相机帧序号。

先构建 Vue 前端，再通过显式 JSON 配置启动服务：

```powershell
pnpm --dir src/web install
pnpm --dir src/web build
poetry run gripper-ai-controller web --config-file configs/development.json
```

默认服务绑定 `0.0.0.0:8000`，浏览器访问 `http://本机地址:8000/`。可使用 `--host`、`--port` 和 `--frontend-dist-dir` 覆盖对应配置值；它们均不会写回配置文件。开发前端可在 `src/web/` 中执行 `pnpm dev`，Vite 会将 `/api` 代理到本机 `8000` 端口。

预览接口包括 `GET /api/cameras`、`GET /api/cameras/{camera_id}/status`、`GET /api/cameras/{camera_id}/frame` 和 `GET /api/cameras/{camera_id}/stream`。海康适配器还可以公开固定白名单的相机参数：`GET /api/cameras/{camera_id}/parameters` 用于读取实际设备能力，`PATCH /api/cameras/{camera_id}/parameters/{parameter_key}` 只允许立即生效参数，`POST /api/cameras/{camera_id}/parameters/apply` 用于明确保存需要暂停取流的参数并自动恢复采集。

版本化海康模板默认将 `web.camera_controls_enabled` 设为 `false`。真实设备写入只能在被 Git 忽略的 `localstore/` 本机配置中显式设为 `true`。服务绑定 `0.0.0.0` 且没有认证、TLS、来源限制或访问审计；一旦启用参数写入，受信任局域网中的访问者可以修改已公开的相机参数，因此绝不能暴露到互联网。无论该开关如何设置，网页服务都不提供机器人或夹爪控制接口。

使用实物海康 USB 相机时，先将 `configs/hikvision-usb.example.json` 复制到 `localstore/`，填写本机标定标识；只有系统枚举到一台相机时才可省略序列号。该配置不应提交。详细接口、重试和图像格式规则见 [相机网页预览后端说明](src/gripper_ai_controller/web/README.md)。

## 提交前路径检查

`python scripts/check_submission_paths.py` 只扫描 Git 已跟踪或未被忽略的新文件，并在发现文件系统绝对路径时以非零状态退出。它检查 Windows 盘符路径、UNC 路径、`file:` 后接两个斜杠的 URI 和常见 POSIX 绝对路径；URL、CoppeliaSim 场景对象路径和相对路径不会被视为违规。

本地日志、真实标定、采集数据和本机覆盖内容分别位于 `logs/` 与 `localstore/`，均不参与 Git 提交。

## 组件模型

- `core/`：异步运行时、注册表、类型化事件总线、目标调度器和重载生命周期。
- `adapters/`：机器人、夹爪或相机集成。适配器负责获取数据或执行已授权指令，不含规划算法。
- `plugins/`：感知、规划和审计模块。插件可观察类型化事件并提出规范化指令，但不可调度硬件指令。
- `configs/`：版本化 JSON 运行配置文件的统一入口。
- `bootstrap/`：读取、校验 JSON 并组装运行时图的 Python 代码。
- `data/`：应随代码提交的小型、非敏感、可复现数据和模板。
- `localstore/`：Git 忽略的本机运行数据、标定结果、采集物和私密覆盖。

一个执行目标包含一个机器人适配器和一个夹爪适配器。其中一个目标是 `primary`（主目标），其遥测数据具有权威性。镜像目标接收相同的已批准指令 ID，并通过主目标遥测数据进行修正，从而允许未来的 CoppeliaSim 数字孪生安全地跟随真实执行。

## JAKA 安全接入

项目内的 `adapters/jaka/` 提供 JAKA Python SDK 2.1.2 的连接与使能适配器。它已注册为可选的机器人适配器，但不在默认开发配置中启用，也不接受版本化配置中的真实 IP 地址。`configs/jaka-hardware.example.json` 只提供占位模板；真实副本必须保存在 `localstore/`。启动只连接并读取遥测；运动指令在适配器内被拒绝。真实使能必须通过本机私有配置显式允许，并要求控制器已人工上电、急停可用且工作区已清空。`jkrc.pyd` 与 `jakaAPI.dll` 必须从官方 SDK 复制到该适配器目录，但因再分发授权未确认，Git 与 Poetry 构建均不会包含它们。

详细边界和使用方式见 [JAKA 适配器说明](src/gripper_ai_controller/adapters/jaka/README.md)。

## 海康 USB 相机接入

项目内的 `adapters/hikvision/` 提供 MVS USB3 Vision 帧源和受限参数适配器。它在启动时打开指定相机，在 `capture()` 时复制原始帧，并在关闭时释放 MVS 资源。只有网页服务经本机配置显式允许时，它才会通过固定白名单读取或修改自动曝光、曝光时间、自动增益、增益、帧率开关、帧率和像素格式；不会公开任意 MVS 节点、触发模式或持久化 UserSet 设置。像素格式属于暂停取流后保存并自动恢复采集的参数。版本化配置只提供占位模板，真实相机序列号和标定标识必须保存于 `localstore/`。MVS Python 封装和 Windows 运行库必须从官方安装包复制到该适配器目录；在取得再分发授权前，它们仅作为本机资产，不随 Git 或 Poetry 构建发布。

详细边界、SDK 文件和验证方式见 [海康适配器说明](src/gripper_ai_controller/adapters/hikvision/README.md)。

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
