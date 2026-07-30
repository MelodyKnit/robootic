# 夹爪 AI 控制器 (Gripper AI Controller)

本项目是一个基于 Python 3.7 的视觉引导机器人及夹爪工作站基础保障框架。其采用模块化分离架构，小型核心（Core）负责生命周期、类型化事件、安全授权和指令调度，而感知与控制的具体实现则由适配器（Adapters）及插件（Plugins）承载。

⚠️ **安全警示 (Crucial Safety Warning)**
* **硬件动作防碰撞**：包含机械臂上电（`robot.power_on()`、`enable_robot()`）、直线/关节运动、以及夹爪强力夹紧动作的测试与演示，必须在清空工作区的前提下进行，并配备现场独立物理急停装置。
* **网页远程控制隔离**：在配置中启用网页夹爪或机械臂手动控制时，配置文件的 `web.bind_host` 必须限制为 `127.0.0.1` 环回物理接口。若检测到任何非环回绑定，服务将拒绝启动，避免未经授权的局域网客户端直接操作现场实体设备。
* **开发图安全自洽性**：默认的开发拓扑使用内存模拟的虚拟设备（机器人、夹爪、相机适配器），在不改变配置至物理端口或真实 SDK 时，绝不会导入外部 SDK 或操作硬件实物。

---

## 1. 软件环境与包管理 (Environments & SDKs)

物理节卡机械臂 `jkrc` 二进制扩展模块与动态链接库原生在 Python 3.7 (64-bit) 下编译。为确保通联层向下兼容，本项目固定使用 Python 3.7。

### 基础环境要求
* **Python 隔离环境**：Conda 环境 `robotic`（包含 `Python >=3.7,<3.8`，64位）
* **包管理器**：执行入口支持 Poetry `>=1.7,<3`。Poetry 1.7/1.8 可安装、构建、检查和启动项目；Poetry 2.x 可检查并启动已经由 Poetry 1.x 建立的 Python 3.7 环境。为维持旧版可读取的锁格式，统一使用 Poetry 1.8.5 更新 `poetry.lock`。
* **图像与服务依赖**：`pyzmq==25.1.2`、`cbor==1.0.0`、`FastAPI==0.99.1`、`uvicorn==0.22.0`、`Pillow==9.5.0`、`opencv-contrib-python-headless==4.9.0.80`
* **姿态学习依赖**：`torch==1.13.1` 与 `torchvision==0.14.1`（固定锁定 PyTorch CUDA 11.7 软件源包）
* **Web 前端环境**：Node.js 20+ 以及 pnpm 10

Poetry CLI 自身应通过 `pipx` 或官方安装器运行在它支持的宿主 Python 中，不要安装到 `robotic` 的 Python 3.7 环境。激活 `robotic` 后首次安装必须使用 Poetry 1.7/1.8 与 `scripts\\install.bat`，以让项目虚拟环境使用当前 Python 3.7；项目构建后端固定为仍兼容 Python 3.7 的 `poetry-core==1.6.1`。当前 Poetry 2.3 的解释器探测代码要求 Python 3.8+ 语法，因此不能用于首次创建或构建本项目的 Python 3.7 环境，但可通过 `run.bat`、`test.bat` 和 `frontend.bat` 使用已经建立的环境。

### 官方 SDK 引入规范
由于再分发授权约束，各硬件厂商 SDK 的物理资产不打包随 Git 提交。部署真机前，需从静态资料库 [documents/](../../documents) 将相应依赖复制到子项目的适配器目录中（**严禁跨目录直接在代码中引用外部共享库**）：
* **JAKA SDK**：需将 `jkrc.pyd` 与 `jakaAPI.dll` 复制放置在 [src/gripper_ai_controller/adapters/jaka/](src/gripper_ai_controller/adapters/jaka) 目录下。
* **Hikvision SDK**：需将海康官方 MVS Python 封装和 Windows 运行库复制放置在 [src/gripper_ai_controller/adapters/hikvision/](src/gripper_ai_controller/adapters/hikvision) 目录下。

---

## 2. 系统组件架构 (Architecture & Directory Layout)

本项目贯彻“高内聚低耦合”原则，各模块承担明确职责：

```
├── configs/          # 版本化运行配置模版。默认关闭硬件使能与外网绑定
├── docs/             # 业务设计及组件契约说明 ── 见 [docs/architecture.md](docs/architecture.md)
├── data/             # 随仓提交的小型测试样本、视觉 Fixture 集
├── localstore/       # 被 .gitignore 忽略的本地标定、真实 IP、私有覆盖与模型权重
│   ├── models/       # 本机人体姿态与二维检测模型文件
│   └── packages/     # 离线下载的 wheel 包受控例外包
├── logs/             # 运行期产生的系统日志与标定记录（被 Git 忽略）
│   └── work/         # 开发者工作日志（按 YYYYMMDDHHmmss-内容.md 命名）
├── src/              # 核心源代码包
│   ├── gripper_ai_controller/
│   │   ├── bootstrap/      # 运行时环境的构建器与配置校验器
│   │   ├── core/           # 运行时调度核心、事件总线、任务目标 ── 见 [src/gripper_ai_controller/core/README.md](src/gripper_ai_controller/core/README.md)
│   │   ├── adapters/       # 基础物理/仿真硬件的驱动解耦层 ── 见 [src/gripper_ai_controller/adapters/README.md](src/gripper_ai_controller/adapters/README.md)
│   │   ├── plugins/        # 独立的路径规划、控制算法或策略过滤器
│   │   ├── object_detection/ # 通用二维类别框与本地模型提供器 ── 见 [src/gripper_ai_controller/object_detection/README.md](src/gripper_ai_controller/object_detection/README.md)
│   │   ├── object_pose/    # 已知平放工件的轮廓、标定投影与平面位姿
│   │   ├── pose/           # 姿态估算与运动关联模块 ── 见 [src/gripper_ai_controller/pose/README.md](src/gripper_ai_controller/pose/README.md)
│   │   ├── vision/         # 清晰度/亮度/成像质量评估层 ── 见 [src/gripper_ai_controller/vision/README.md](src/gripper_ai_controller/vision/README.md)
│   │   └── web/            # 局域网调试与设备人工操作服务后端 ── 见 [src/gripper_ai_controller/web/README.md](src/gripper_ai_controller/web/README.md)
│   └── web/          # Vue 3 / Vite 调试前端 ── 见 [src/web/README.md](src/web/README.md)
├── tests/            # 单元与集成测试集合（基于 Python 标准 unittest）
└── scripts/          # 本地自测、格式校验脚本工具
```

---

## 3. 标准操作与常用命令 (CLI Usage Handbook)

在终端已执行并激活 Conda 环境 `robotic` 的前提下，在项目根目录下，以下为标准的指令集：

### 3.1 环境检视与权重整备
```powershell
# 1. GPU 及 PyTorch CUDA 推理就绪状态检视
.\scripts\run.bat gpu-check --require-torch

# 2. 下载人体骨架检测网络所使用的 Keypoint R-CNN 框架权重
.\scripts\run.bat pose-download-weights --weights-file localstore/models/keypointrcnn_resnet50_fpn_coco.pth

# 3. 操作者显式安装官方 Faster R-CNN COCO 权重；默认写入 localstore/models/
.\scripts\run.bat object-detection-download-fasterrcnn
```

### 3.2 模拟与干跑 (Dry Run / Simulation)
```powershell
# 0. 网页服务必须传入显式配置；--dev 只为网页热重载追加 --reload
.\scripts\run.bat web --config-file configs/development.json
.\scripts\run.bat --dev web --config-file configs/development.json

# 1. 运行系统主目标指令（默认使用 configs/development.json 安全模拟器）
.\scripts\run.bat run --config-file configs/development.json --objective "Pick the detected workpiece"

# 2. 对节卡机械臂关节发送模拟指令干手预览（不连接网路及 SDK）
.\scripts\run.bat jaka-joint-dry-run --config-file configs/jaka-joint-dry-run.example.json --target jaka-dry-run-primary --joint-positions-rad "[0.1, 0.0, 0.0, 0.0, 0.0, 0.0]" --mode absolute --speed-rad-per-second 0.5

# 3. 图像中心收敛虚拟算法伺服仿真
.\scripts\run.bat image-centering-simulate --config-file configs/image-centering-simulation.example.json
```

### 3.3 离线评估与测试自检
```powershell
# 1. 运行全单元与集成测试套件 (快捷批处理脚本，支持指定测试用例参数)
.\scripts\test.bat

# 2. 对本地测试集（Fixture）进行姿态推理质量的综合离线评价
.\scripts\run.bat vision-evaluate --config-file configs/vision-evaluation.example.json --report-file temp/gripper-ai-controller/vision-evaluation/report.json --save-overlays

# 3. 热重载开发模块测试（动态重新加载插件等）
.\scripts\run.bat reload --config-file configs/development.json --module gripper_ai_controller.plugins.audit

# 4. 提交代码前的绝对路径检查自律脚本
.\scripts\test.bat tests.test_submission_paths

# 5. 使用 Poetry 1.8.5 安全刷新兼容锁文件
.\scripts\lock.bat
```

### 3.4 物理通联测试（真机调试）
```powershell

.\scripts\run.bat jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

### 3.5 单目已知工件标定与只读识别
```powershell
# 生成固定规格的 ChArUco 板图；不连接任何设备
.\scripts\calibration.bat calibration-generate-charuco --output-file temp/gripper-ai-controller/charuco/board.png

# 唯一会连接相机的标定命令，只读采集至少 25 张图像；不连接 JAKA 或夹爪
.\scripts\calibration.bat calibration-capture-charuco --config-file localstore/object-pose/hikvision-usb/camera.local.json --output-dir localstore/object-pose/hikvision-usb/charuco-intrinsics --frame-count 25 --capture-interval-seconds 3
```

完整标定、工件档案和验收步骤见 [单目工件标定流程](docs/object-pose-calibration.md)，只读 HTTP 契约见 [工件位姿接口](docs/object-pose-api.md)。该首期模块只发布台面约束下的 `X/Y/Yaw`；`Z/Roll/Pitch` 是推导值，不包含自动抓取或运动权限。

### 3.6 通用二维物品框选

`object-detection-analysis` 用本地 Faster R-CNN 或提示类别已在导出前固化的 YOLO-World ONNX，对共享相机帧返回类别、置信度和归一化二维框。它用于画面标注和候选筛选，不提供抓取点、深度、姿态或 JAKA 基座坐标；需要已知工件 `X/Y/Yaw` 时仍使用上一节的 `object-pose-analysis`。

`configs/development.json` 已包含默认关闭的模型档案示例，但仓库不包含对应权重或 ONNX。Faster R-CNN 官方 COCO 权重可由操作者显式执行 `scripts\run.bat object-detection-download-fasterrcnn` 安装到默认本地路径；命令对完整文件做固定 SHA-256 校验后才原子替换。YOLO-World ONNX 仍需按模型许可证和导出契约另行准备。网页服务和检测 Plugin 在运行期都不会自动联网下载模型。模型与接口说明见 [通用二维目标检测](src/gripper_ai_controller/object_detection/README.md) 和 [二维检测接口](docs/object-detection-api.md)。

---

## 4. 人机调试 Web 服务 (Camera & Device Controls Server)

Web 调试服务提供海康相机只读预览、图像质量、人体骨架、已知工件位姿与通用二维物品框叠加显示，以及经显式本机授权的机械臂、电装夹爪临时单步操作配置。

### 前端构建与服务拉起
在拉起服务前，需首先构建 Vue 3 前端静态页面资产：
```powershell
# 安装与构建前端应用程序
.\scripts\frontend.bat install
.\scripts\frontend.bat build

# 启动模拟调试服务界面
.\scripts\run.bat web --config-file configs/development.json

# 启动包含物理夹爪人工控制界面的网页端（加载 localstore 下的本地环回 IP 配置）
.\scripts\run.bat web --config-file localstore/gripper-web-control.local.json
```

### 4.1 海康相机网页自适应控制
海康 USB 工业相机在启动时被独占拉起，以单一采集循环推送 MJPEG 服务；其各项曝光及帧率参数白名单见于 [相机说明](src/gripper_ai_controller/adapters/hikvision/README.md)。
1. 将 `configs/hikvision-usb.example.json` 拷贝至 `localstore`。
2. 将 `web.bind_host` 设为 `127.0.0.1`，再显式开启 `camera_selection.enabled`，页面即可刷新 USB 相机列表并选择当前采集设备。
3. 设备选择和真实参数只写回启动时显式传入的 `localstore/` 本机配置；浏览器只获得不直接包含厂商序列号的设备标识。

切换相机不会创建第二条采集链路，也不会更改逻辑 `camera_id` 或 MJPEG URL。后端会串行停止旧相机、打开新相机并恢复允许的参数；失败时尝试回滚旧设备，成功后清空旧设备的画面、骨架和成像分析缓存。未给新设备配置标定映射时仍可预览和执行 2D 分析，但不得将其视为已标定的机器人空间数据。

### 4.2 电装网页夹爪授权控制 (DH PGI)
真实夹爪的物理参数需存放于 `localstore/gripper-web-control.local.json` 配置的 `gripper_control` 子段中：
* 网页控制具备 60 秒的会话安全令牌限制。
* 使用物理独立急停以备机械动作卡阻，不要依赖 Web 锁屏按钮。
* 真机状态中的“已设定目标位置”来自已验证的目标寄存器，不是实时物理位置反馈。
* API 接口说明详情参见 [网页服务说明](src/gripper_ai_controller/web/README.md) 及 [PGI 适配器文档说明](src/gripper_ai_controller/adapters/pgi/README.md)。

### 4.3 节卡六轴网页授权控制
机械臂真实物理地址填入 `localstore/jaka-web-control.local.json` 中所选 `targets` 项的 `robot_adapter_settings.controller_ip`；`jaka_control` 子段只保存网页控制策略。
* **双重验证保护**：在对机械臂关节点动发送前，需首先生成带有 `preview_id` 的服务端预览帧，随后再次确认现场安全方可触发实体阻隔运动。
* 网页手动控制绝对不具备对机械臂的 `power_on()`（开机上电能力）。测试前务必由专家完成工具负载及安装姿态的手控校验。
* 详情说明参见 [JAKA 适配器说明](src/gripper_ai_controller/adapters/jaka/README.md) 与 [网页服务说明](src/gripper_ai_controller/web/README.md)。

### 4.4 Plugin/Adapter 工作台
桌面宽屏页面分为三栏：左侧显示后端已配置的功能 Plugin，中央始终保持唯一的 MJPEG 实时画面与新鲜分析叠加，右侧通过选项卡切换相机、夹爪和 JAKA 适配器面板；非活动面板保持挂载但不显示。Plugin 状态、错误和能力由 `GET /api/plugins` 动态提供；当前受信任注册表内置 `visual-pose-analysis`、`object-pose-analysis` 与 `object-detection-analysis`，新增模块必须先在服务端注册固定工厂，网页不会根据未知或未注册 Plugin 自动生成设备控制表单。

`visual-pose-analysis` 只消费 `FrameCaptured` 图像事件，用于人体姿态、人员范围和成像质量分析。它不持有相机 SDK、夹爪客户端、JAKA 客户端或任何指令权限，因此刷新、展开或重载 Plugin 都不会初始化、使能或驱动设备，也不会重建中央视频流。

`object-pose-analysis` 独立消费同一 `FrameCaptured` 事件，以单工作线程和最新帧优先策略分析固定相机下的单个已知平放工件。它只读取已缓存帧与 `localstore/` 中显式指定的背景和标定；不会持有相机 SDK、JAKA、夹爪、安全策略或动作权限。浏览器通过 `GET /api/cameras/{camera_id}/objects` 读取缓存，工件轮廓只在与最新 MJPEG 帧时间一致时叠加显示。

`object-detection-analysis` 同样只消费共享帧事件，以本地模型输出二维类别框。Faster R-CNN 只读取显式本地权重；YOLO-World 首期只运行提示类别已固化的 ONNX，并支持 `ultralytics`、`end2end`、`official-nms` 三种明确输出格式。浏览器通过 `GET /api/cameras/{camera_id}/detections` 读取缓存；模型选择仅在当前服务进程会话生效，切换时清空旧框，不改写配置，也不下载模型。

Plugin 详情中的相机下拉框与右侧相机面板共享同一物理设备选择状态。它用于确认和切换整个预览管线的输入，不会为某个 Plugin 独占一台相机；服务始终只有一个逻辑 `camera_id`、一个采集循环和一个 MJPEG URL。

开发重载必须同时满足 `runtime_mode: "development"`、`web.bind_host: "127.0.0.1"` 和 `web.plugin_reload_enabled: true`。默认模板关闭该开关；生产服务必须完整重启后才会加载 Plugin 更新。详细接口与配置约束见[网页服务说明](src/gripper_ai_controller/web/README.md)和[配置说明](configs/README.md)。

---

## 5. 组件扩展示例 (How-to: Add Customized Components)

1. 将物理 JAKA、DH 夹爪或海康相机的二进制/源码 SDK 文件拷贝至本子项目指定的 Adapter 对应文件夹下（严禁直接跨目录引用 `documents/`）。
2. 在对应的 [src/gripper_ai_controller/adapters/](src/gripper_ai_controller/adapters) 包下继承虚基类，实现生命周期方法。
3. 编写所实现组件的 `ComponentManifest`，以加入引导启动器。
4. 使用 `tests/` 目录下的模拟测试模板编写相应的单体与冒烟自检测试用例。
