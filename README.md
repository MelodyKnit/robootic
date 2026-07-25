# 夹爪 AI 控制器

本项目是一个基于 Python 3.7 的视觉引导机器人及夹爪工作站基础框架。其运行时采用模块化的分离架构：小型核心负责生命周期、类型化事件、安全授权和指令调度；适配器和插件均为可独立更新的项目内模块。

默认开发图是安全自洽的：使用内存中的机器人、夹爪、镜像、相机、规划和感知组件，从不导入厂商 SDK、打开端口、采集真实图像或控制物理设备。

## 环境要求

- Conda 环境：`robotic`
- Python：`>=3.7,<3.8`，64 位
- 包管理器：Poetry
- 仿真通信依赖：`pyzmq==25.1.2`、`cbor==1.0.0`
- 相机预览运行依赖：`FastAPI==0.99.1`、`uvicorn==0.22.0`、`Pillow==9.5.0`
- 人体姿态运行依赖：`torch==1.13.1`、`torchvision==0.14.1`，固定使用 PyTorch CUDA 11.7 软件源
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
poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
poetry run python -m unittest discover -s tests -v
python scripts/check_submission_paths.py
```

`[project.dependencies]` 是标准依赖声明；`[tool.poetry.dependencies]` 保留相同版本，供 Python 3.7 兼容的 Poetry 1.8.5 运行器使用。由于 Python 3.7 不兼容 Poetry 2.3.4 的解释器探测依赖，Poetry 1.8.5 运行器应将其 `packaging` 固定为 23.2、`virtualenv` 固定为 20.26.6。项目依赖仍必须通过 Poetry 安装，不得使用裸 `pip` 写入 `robotic` 环境。

若本机 Poetry 运行器在目标 Python 3.7 的 wheel 标签探测中报出 `packaging` 语法错误，可作为一次性的受控例外：从锁定的官方 PyTorch CUDA 11.7 地址下载精确 wheel，核验官方索引给出的 SHA-256 后放入 `localstore/packages/`，再使用 `python -m pip install --no-index --no-deps` 安装该单一离线文件。该例外仅用于已锁定的 CUDA Torch 运行时，不能用于普通项目依赖，不修改 `pyproject.toml` 或 `poetry.lock`，安装后必须运行 `gripper-ai-controller gpu-check --require-torch` 验证。

`reload` 仅在开发模式下可用。生产配置在有明确的本地硬件适配器实现之前，刻意保持"故障关闭"状态。

## 相机网页预览

网页预览是独立的相机服务，不会创建 `Runtime`、执行目标、安全策略、JAKA 连接或夹爪连接。它只启动配置中的一个 `VisionAdapter`，以单一采集循环把最新帧保留在内存中，并将同一 JPEG 帧复用给快照和多个 MJPEG 浏览器连接。主画面始终使用连续 MJPEG，不会因姿态结果切换成静态 JPEG；姿态来源帧与最新预览帧相差超过 `0.35` 秒时只隐藏骨架，实时画面继续播放。不记录或展示相机帧序号，也不写入磁盘。

先构建 Vue 前端，再通过显式 JSON 配置启动服务：

```powershell
pnpm --dir src/web install
pnpm --dir src/web build
poetry run gripper-ai-controller web --config-file configs/development.json
```

默认服务绑定 `0.0.0.0:8000`，浏览器访问 `http://本机地址:8000/`。可使用 `--host`、`--port` 和 `--frontend-dist-dir` 覆盖对应配置值；它们均不会写回配置文件。开发前端可在 `src/web/` 中执行 `pnpm dev`，Vite 会将 `/api` 代理到本机 `8000` 端口。

预览接口包括 `GET /api/cameras`、`GET /api/cameras/{camera_id}/status`、`GET /api/cameras/{camera_id}/frame` 和 `GET /api/cameras/{camera_id}/stream`。海康适配器还可以公开固定白名单的相机参数：`GET /api/cameras/{camera_id}/parameters` 用于读取实际设备能力，`PATCH /api/cameras/{camera_id}/parameters/{parameter_key}` 只允许立即生效参数，`POST /api/cameras/{camera_id}/parameters/apply` 用于提交需要暂停取流的参数并自动恢复采集。设备成功应用后，服务会将实际生效值写回显式传入 JSON 配置根对象的 `camera_parameters`；服务启动或相机重连后，会在首帧前恢复该对象中的参数。

版本化 `configs/` 中的海康文件仅是模板，默认将 `web.camera_controls_enabled` 设为 `false`。真实设备写入只能在被 Git 忽略的 `localstore/` 本机配置中显式设为 `true`；被传入 `--config-file` 的本机 JSON 也是相机参数持久化的唯一写入目标。若设备已成功应用参数但配置写回失败，接口会明确报告“设备已生效、配置未保存”的失败，操作者应修复本机文件权限或内容后重新提交。服务绑定 `0.0.0.0` 且没有认证、TLS、来源限制或访问审计；一旦启用参数写入，受信任局域网中的访问者可以修改已公开的相机参数，因此绝不能暴露到互联网。无论该开关如何设置，网页服务都不提供机器人或夹爪控制接口。

使用实物海康 USB 相机时，先将 `configs/hikvision-usb.example.json` 复制到 `localstore/`，填写本机标定标识；只有系统枚举到一台相机时才可省略序列号。该配置不应提交。详细接口、重试和图像格式规则见 [相机网页预览后端说明](src/gripper_ai_controller/web/README.md)。

## 人体关节与骨架预览

人体姿态是网页预览中的独立视觉能力，只处理相机帧和浏览器元数据，不构造 `Runtime`，不连接 JAKA、夹爪或任何运动接口。首版使用单人模式的 COCO 17 关节模型：页面可选择锁定关节，默认 `right_wrist`；当人物、目标关节或相机帧不满足置信度要求时，结果立即失效，页面不会继续使用旧坐标。

当同一锁定关节在两个连续有效帧中可见时，`/pose` 还会提供归一化图像坐标的位移、速度和“正在运动”状态。首帧按置信度选择主人体，后续帧必须通过人体框 IoU 或中心距离关联到同一人；关联失败、目标切换、人员丢失、低置信度、非递增时间戳或超出最大采集间隔时都会清除运动基线，避免把不同人或停顿后的画面误判为移动。默认人员阈值为 `0.80`，默认运动速度阈值为 `0.04` 个归一化图像坐标/秒。该状态只用于网页观察，既不是三维速度，也不会生成机器人、夹爪或相机运动指令。

先执行只读 GPU 检查，再通过 Poetry 安装固定的 CUDA 依赖：

```powershell
poetry run gripper-ai-controller gpu-check
poetry install
poetry run gripper-ai-controller gpu-check --require-torch
poetry run gripper-ai-controller pose-download-weights --weights-file localstore/models/keypointrcnn_resnet50_fpn_coco.pth
```

将 `configs/pose-preview.example.json` 复制到 `localstore/pose-preview.json`，填写实际相机设置、`weights_path` 并将 `pose.enabled` 设为 `true` 后启动网页服务。启用姿态时，服务会在启动前要求 NVIDIA 驱动和 CUDA 11.7 Torch 均可用，绝不回退到 CPU。浏览器选择的关节只会原子写回显式传入的本机 JSON 配置。

单色海康帧会由 Pillow 原生路径复制为三个相同亮度输入通道，因此模型可推理但不会获得真实颜色信息。为降低 2448×2048 等高分辨率输入的延迟，推理只处理最长边 `768` 的等比缩图，并将关节坐标映射回原始像素坐标；默认姿态频率为 `2 FPS`，相机采集与 MJPEG 不会等待模型完成。单一相机只输出 2D 图像坐标，不能直接驱动机械臂跟随。机械臂带动相机的后续工作必须另行实现标定、3D/图像伺服、速度与工作空间限制、目标丢失停机、仿真验证和明确真机授权。实现与 API 说明见 [姿态感知包说明](src/gripper_ai_controller/pose/README.md)。

## 分阶段成像与人体识别

在启用任何跟随或控制设计之前，网页预览会对最多每秒一帧的缩采样图计算分辨率、像素格式、亮度均值、对比度和清晰度警告，并复用 Keypoint R-CNN 已输出的人员框和 COCO 17 关节。`GET /api/cameras/{camera_id}/vision/analysis` 只读取内存缓存，不触发新的取帧或推理；无人或姿态过期时页面仍显示连续 MJPEG，只有新鲜姿态才在其上绘制骨架，避免慢速推理造成画面冻结或错位。

公开的离线素材位于 [data/vision-fixtures/](data/vision-fixtures/README.md)，覆盖完整人体、上半身、局部遮挡、无人和低亮度场景。通过 GPU 预检且本机权重已存在后，可运行：

```powershell
poetry run gripper-ai-controller gpu-check --require-torch
poetry run gripper-ai-controller vision-evaluate --config-file configs/vision-evaluation.example.json --report-file temp/gripper-ai-controller/vision-evaluation/report.json --save-overlays
```

离线命令只读取本地权重和已校验哈希的公开图片，不创建相机、网页、机器人、夹爪或运动调度。完整实现、验收门槛和模型扩展边界见 [视觉分析包说明](src/gripper_ai_controller/vision/README.md)。

## 图像居中虚拟仿真

`image-centering-simulate` 在公开图片中识别当前锁定关节，并用纯内存的六轴虚拟模型计算“让该关节向画面中心收敛”时的预测关节变化。默认模板使用 `full-body-front` 的 `right_wrist`，并按确定性 `Mono8` 路径运行 Keypoint R-CNN，因此可先验证单色成像输入、关节定位、误差方向、单步限幅、虚拟关节限位与收敛过程。

```powershell
poetry run gripper-ai-controller image-centering-simulate --config-file configs/image-centering-simulation.example.json
```

输出固定写入控制台，包括初始/预测目标坐标、每一步参与变化的虚拟关节、完整六轴状态和停止原因。`simulation_only=true` 表示命令没有创建相机、网页、`Runtime`、JAKA、夹爪、网络客户端或机器人指令；它不会执行真实或仿真软件中的机械臂运动。

纯算法会话也可按采集时间顺序接收同一相机、同一关节的后续二维观测，在模拟人物手部位移后重新计算居中修正；重复时间戳、跨相机或跨关节输入会被拒绝。当前命令只使用单张公开图片验证这条计算链，不订阅真实相机或网页姿态流。

虚拟模型的二维雅可比仅用于离线数学验收，不是 JAKA 运动学、工具相机标定或碰撞仿真。未来将它接入真机仍需相机内参、手眼标定、真实运动学和可达性、在线图像反馈、速度/加速度/工作空间/人机距离限制、丢失目标停机与 `Runtime` 的安全授权。详细设计和扩展边界见 [图像居中仿真说明](src/gripper_ai_controller/image_servo_simulation/README.md)。

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

项目内的 `adapters/jaka/` 提供 JAKA Python SDK 2.1.2 的连接、只读状态和只读关节角适配器。它已注册为可选的机器人适配器，但不在默认开发配置中启用，也不接受版本化配置中的真实 IP 地址。`configs/jaka-hardware.example.json` 只提供占位模板；真实副本必须保存在 `localstore/`。`jaka-joints` 只执行 `login -> get_joint_position -> logout` 并输出 J1 至 J6 的弧度和角度制数值；它不会上电、使能、去使能或移动。该数据是关节空间角度，不能代替实体关节的三维坐标。运动指令在适配器内被拒绝。真实使能必须通过本机私有配置显式允许，并要求控制器已人工上电、急停可用且工作区已清空。`jkrc.pyd` 与 `jakaAPI.dll` 必须从官方 SDK 复制到该适配器目录，但因再分发授权未确认，Git 与 Poetry 构建均不会包含它们。

详细边界和使用方式见 [JAKA 适配器说明](src/gripper_ai_controller/adapters/jaka/README.md)。

## 海康 USB 相机接入

项目内的 `adapters/hikvision/` 提供 MVS USB3 Vision 帧源和受限参数适配器。它在启动时打开指定相机，在 `capture()` 时复制原始帧，并在关闭时释放 MVS 资源。默认 `frame_delivery_mode` 为 `latest_only`：在相机句柄已打开、MVS 开始取流前通过 MVS 设置策略，使后续取帧清除旧队列并交付最新图像，避免网页预览追赶 FIFO 历史帧。已连接 USB 相机在开始取流后设置策略会返回调用顺序错误，因此不能延后该调用。只有网页服务经本机配置显式允许时，它才会通过固定白名单读取或修改自动曝光、曝光时间、自动增益、增益、帧率开关、帧率和像素格式；不会公开任意 MVS 节点、触发模式或设备持久化的 UserSet 设置。像素格式属于暂停取流后应用并自动恢复采集的参数。网页服务会在设备更新成功后把实际生效值写回启动时显式传入 JSON 的 `camera_parameters`，并在启动或重连的首帧前恢复；这不是对相机设备执行 `FeatureSave` 或 `UserSetSave`。版本化配置只提供占位模板，真实相机序列号、标定标识和可写本机配置必须保存于 `localstore/`。MVS Python 封装和 Windows 运行库必须从官方安装包复制到该适配器目录；在取得再分发授权前，它们仅作为本机资产，不随 Git 或 Poetry 构建发布。

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
