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
* **包管理器**：Poetry 1.8.5（需确保 `virtualenv` 固定为 `20.26.6`，`packaging` 固定为 `23.2`，以规避 Python 3.7 兼容性解析错误）。**切勿使用 Poetry 2.x 进行锁定。**
* **图像与服务依赖**：`pyzmq==25.1.2`、`cbor==1.0.0`、`FastAPI==0.99.1`、`uvicorn==0.22.0`、`Pillow==9.5.0`
* **姿态学习依赖**：`torch==1.13.1` 与 `torchvision==0.14.1`（固定锁定 PyTorch CUDA 11.7 软件源包）
* **Web 前端环境**：Node.js 20+ 以及 pnpm 10

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
│   ├── models/       # 人体姿态权重存放目录
│   └── packages/     # 离线下载的 wheel 包受控例外包
├── logs/             # 运行期产生的系统日志与标定记录（被 Git 忽略）
│   └── work/         # 开发者工作日志（按 YYYYMMDDHHmmss-内容.md 命名）
├── src/              # 核心源代码包
│   ├── gripper_ai_controller/
│   │   ├── bootstrap/      # 运行时环境的构建器与配置校验器
│   │   ├── core/           # 运行时调度核心、事件总线、任务目标 ── 见 [src/gripper_ai_controller/core/README.md](src/gripper_ai_controller/core/README.md)
│   │   ├── adapters/       # 基础物理/仿真硬件的驱动解耦层 ── 见 [src/gripper_ai_controller/adapters/README.md](src/gripper_ai_controller/adapters/README.md)
│   │   ├── plugins/        # 独立的路径规划、控制算法或策略过滤器
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
poetry run gripper-ai-controller gpu-check --require-torch

# 2. 下载人体骨架检测网络所使用的 Keypoint R-CNN 框架权重
poetry run gripper-ai-controller pose-download-weights --weights-file localstore/models/keypointrcnn_resnet50_fpn_coco.pth
```

### 3.2 模拟与干跑 (Dry Run / Simulation)
```powershell
# 1. 运行系统主目标指令（默认使用 configs/development.json 安全模拟器）
poetry run python -m gripper_ai_controller run --config-file configs/development.json --objective "Pick the detected workpiece"

# 2. 对节卡机械臂关节发送模拟指令干手预览（不连接网路及 SDK）
poetry run gripper-ai-controller jaka-joint-dry-run --config-file configs/jaka-joint-dry-run.example.json --target jaka-dry-run-primary --joint-positions-rad "[0.1, 0.0, 0.0, 0.0, 0.0, 0.0]" --mode absolute --speed-rad-per-second 0.5

# 3. 图像中心收敛虚拟算法伺服仿真
poetry run gripper-ai-controller image-centering-simulate --config-file configs/image-centering-simulation.example.json
```

### 3.3 离线评估与测试自检
```powershell
# 1. 对本地测试集（Fixture）进行姿态推理质量的综合离线评价
poetry run gripper-ai-controller vision-evaluate --config-file configs/vision-evaluation.example.json --report-file temp/gripper-ai-controller/vision-evaluation/report.json --save-overlays

# 2. 热重载开发模块测试（动态重新加载插件等）
poetry run python -m gripper_ai_controller reload --config-file configs/development.json --module gripper_ai_controller.plugins.audit

# 3. 运行全单元与集成测试套件
poetry run python -m unittest discover -s tests -v

# 4. 提交代码前的绝对路径检查自律脚本
python scripts/check_submission_paths.py
```

### 3.4 物理通联测试（真机调试）
```powershell

poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

---

## 4. 人机调试 Web 服务 (Camera & Device Controls Server)

Web 调试服务提供海康相机只读预览、图像质量与人体骨架叠加实时显示，以及经显式本机授权的机械臂、电装夹爪临时单步操作配置。

### 前端构建与服务拉起
在拉起服务前，需首先构建 Vue 3 前端静态页面资产：
```powershell
# 构建前端应用程序
pnpm --dir src/web install
pnpm --dir src/web build

# 启动模拟调试服务界面
poetry run gripper-ai-controller web --config-file configs/development.json

# 启动包含物理夹爪人工控制界面的网页端（加载 localstore 下的本地环回 IP 配置）
poetry run gripper-ai-controller web --config-file localstore/gripper-web-control.local.json
```

### 4.1 海康相机网页自适应控制
海康 USB 工业相机在启动时被独占拉起，以单一采集循环推送 MJPEG 服务；其各项曝光及帧率参数白名单见于 [相机说明](src/gripper_ai_controller/adapters/hikvision/README.md)。
1. 将 `configs/hikvision-usb.example.json` 拷贝至 `localstore`。
2. 真实设备变更的相机参数会自动由 Web 后端持续原子写回所配置的本机配置文件中。

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

---

## 5. 组件扩展示例 (How-to: Add Customized Components)

1. 将物理 JAKA、DH 夹爪或海康相机的二进制/源码 SDK 文件拷贝至本子项目指定的 Adapter 对应文件夹下（严禁直接跨目录引用 `documents/`）。
2. 在对应的 [src/gripper_ai_controller/adapters/](src/gripper_ai_controller/adapters) 包下继承虚基类，实现生命周期方法。
3. 编写所实现组件的 `ComponentManifest`，以加入引导启动器。
4. 使用 `tests/` 目录下的模拟测试模板编写相应的单体与冒烟自检测试用例。
