# JAKA 机器人适配器

本包将 JAKA Python SDK 2.1.2 的同步接口转换为项目的 `RobotAdapter` 合约。真实 `JakaAdapter` 支持安全连接、只读状态查询、只读关节角查询、显式使能和显式去使能；通用 `execute()` 始终拒绝关节、直线、伺服和其他运动指令，因此运行时与规划器无法驱动真机。仅独立的网页人工控制门面可在本机权限、实时状态、预览和二次确认均通过后，调用窄化的阻塞绝对关节动作入口。另提供完全离线的 `JakaDryRunRobotAdapter`，用于在不导入 SDK、不建立网络连接且不改变设备状态的前提下，验证 JAKA Zu 3 关节指令并预测关节状态。

## SDK 文件

开发机必须将官方 Windows Python 64 位 SDK 中的 `jkrc.pyd` 与 `jakaAPI.dll` 复制到本包目录。它们必须与 `robotic` 的 Python 3.7 64 位环境匹配，并依赖 Microsoft Visual C++ 运行库。不得从工作区 `documents/` 目录动态导入 SDK。由于当前仓库没有可确认的二进制再分发授权，这两个文件会保留为本机资产，并由 Git 与 Poetry 构建排除；克隆项目后必须从官方 SDK 重新复制。

## 安全边界

- `startup()` 只执行 `RC(controller_ip)` 和 `login()`；不会上电、使能或移动。
- `initialize()` 与 `get_status()` 只读取 `get_robot_status()` 返回的遥测数据。
- `get_joint_positions()` 只执行 SDK 的 `get_joint_position()`，返回带本机采集时间的 `JointPositionSnapshot`。`joint_positions_rad` 的六个值按 `J1` 至 `J6` 排列，单位为弧度；它们是关节空间坐标，不是六个实体关节在机器人基坐标系中的三维 `x/y/z` 位置。
- `enable()` 不会调用 `power_on()`；只有 `allow_enable=True`、控制器已上电且未报告故障或急停时，才会调用 `enable_robot()`。
- `execute()` 始终拒绝机器人运动指令，规划器和运行时无法绕过该限制。
- `operator_joint_move()` 不是运行时接口，只供 `ManualJakaControlService` 在临时令牌、实时遥测、预览和二次确认完成后调用。它仅接受一条阻塞的六轴**绝对**关节动作；相对运动、直线运动、jog、servo、非阻塞动作和自动上电均不支持。
- 真实适配器同样持有纯 `motion_constraint`，因此当它作为主目标时，Runtime 会在 `CommandAuthorized` 前先拒绝不符合 JAKA Zu 3 六轴、速度、步长和软件限位要求的关节命令；通过该只读检查不代表允许执行，`execute()` 仍会拒绝全部真实运动。
- `disable()` 是显式恢复动作；`shutdown()` 仅 `logout()`，不会隐式去使能或断电。

控制器 IP 属于本机设备设置，调用方必须从 `localstore/` 的本地配置或其他显式本地输入提供，禁止写入受版本控制的 `configs/` 文件。

`configs/jaka-hardware.example.json` 提供了不含真实地址的运行时模板。应将其复制至 `localstore/` 后填写地址，再通过显式 `--config-file` 参数运行。加载该配置不会导入 SDK、建立连接或改变设备状态；只有运行时启动 JAKA 目标时才会登录。

## 网页人工关节控制

网页控制仅由 `ManualJakaControlService` 组合真实适配器或 `JakaDryRunRobotAdapter`。它与 `Runtime`、规划器、视觉跟随和镜像同步隔离；网页、插件和普通 `RobotAdapter.execute()` 都不能直接访问 SDK 客户端。服务启动和“重新连接”只执行登录与状态读取，不会自动上电、使能或执行动作。

真实控制器地址、`allow_enable`、`allow_manual_motion` 与 `robot_model: "zu3"` 必须同时只存在于 Git 忽略的 `localstore/` 本机配置。`robot_model` 缺失时即使 `allow_manual_motion=true`，适配器也只提供读取，不能发送网页动作。即使这些适配器级权限已开启，网页还必须在同一个本机配置中将 `web.jaka_controls_enabled` 显式设为 `true`，且服务只能绑定 `127.0.0.1`。受版本控制的 `configs/jaka-web-control.example.json` 始终使用 `jaka-dry-run-robot` 并保持网页控制关闭；要演练流程应复制为本机副本，再仅对干运行副本开启控制。

真机操作流程固定为：操作者确认工作区清空与现场独立急停可用，获取短时网页令牌；在必要时对已人工上电、控制器通信正常且已到位的控制器显式使能；提交一条六轴绝对关节目标的预览；最后在预览未过期、关节遥测仍在来源容差内时二次确认。适配器在实际 SDK 调用前再次读取状态并核对通信、到位、拖拽、故障、急停和来源关节角；调用成功后还要求控制器已到位且每轴到达目标容差内，才向网页返回完成。动作期间没有网页软件急停，撤销令牌、关闭页面、网络断开或服务退出不能代替现场独立急停。

首次真机测试前必须确认实际机械臂型号与 JAKA Zu 3 限位模板一致，并完成控制器/SDK 二进制兼容性、工具与负载、安装姿态、工作区、障碍物和现场急停的安全检查。不得以网页控制代替控制器安全配置，也不得通过网页修改碰撞等级、现场限位或网络异常行为。

## 使用方式

```python
adapter = JakaAdapter(controller_ip=controller_ip, allow_enable=False)
await adapter.startup()
status = await adapter.initialize()
await adapter.shutdown()
```

## 离线关节运动预览

`JakaDryRunRobotAdapter` 只处理 `MOVE_JOINTS` 与 `STOP`。它不加载 `jkrc.pyd`、不访问 `jakaAPI.dll`、不创建控制器客户端，也不打开网络连接。`MOVE_JOINTS` 会生成不可变的 `JakaJointMovePreview`：其中包含逻辑 SDK 调用名与参数（`joint_move(关节值, 模式 0/1, True, 速度)`）、绝对目标关节值、各轴变化量和估计时长。模式 `0` 为 `ABS`，模式 `1` 为 `INCR`。`STOP` 会生成逻辑 `motion_abort()` 预览，并保留当前预测关节值；为保留恢复语义，它只要求当前六轴值为有限数，不会因其已超出物理边界而拒绝预览。

JAKA Zu 3 的物理边界为：J1、J5、J6 为 `[-2π, 2π]`，J2、J4 为 `[-85°, 265°]`，J3 为 `[-175°, 175°]`，硬速度上限为 `π rad/s`。离线适配器默认将每个关节的软件边界向内收缩 `10°`，默认速度上限为 `0.5 rad/s`，单轴单步上限为 `10°`。本地 JSON 输入只能收紧这些默认软件边界、速度和单步上限，不能放宽。

```python
from gripper_ai_controller.adapters.jaka import JakaDryRunRobotAdapter
from gripper_ai_controller.domain.models import JointMoveMode, RobotAction, RobotCommand

adapter = JakaDryRunRobotAdapter()
await adapter.startup()
await adapter.initialize()
preview = adapter.preview(
    RobotCommand(
        RobotAction.MOVE_JOINTS,
        joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=0.5,
        joint_move_mode=JointMoveMode.RELATIVE,
    )
)
predicted_status = await adapter.execute(
    RobotCommand(
        RobotAction.MOVE_JOINTS,
        joint_positions_rad=(0.1, 0.0, 0.0, 0.0, 0.0, 0.0),
        speed=0.5,
        joint_move_mode=JointMoveMode.RELATIVE,
    )
)
```

执行目标可使用 `adapter.motion_constraint.evaluate(command, status)` 复用同一编译器进行纯验证。该约束只返回 `SafetyDecision`，不改变适配器预测状态；`execute()` 才会在验证成功后更新内存中的 `RobotStatus`。

## 一次性读取关节角

将 `configs/jaka-hardware.example.json` 复制到 `localstore/`，填写本机控制器 IP 后，从子项目根目录执行：

```powershell
poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

该命令只启动选定的 JAKA 适配器，执行 `login -> get_joint_position -> logout`，随后在标准输出中给出 `J1` 至 `J6` 的弧度与角度制数值。它不会启动相机、夹爪、网页服务或运行时调度，也不会调用 `power_on()`、`enable_robot()`、`disable_robot()` 或任何运动接口。控制器未上电或未使能时，SDK 的只读错误会原样映射为命令失败；命令不会为读取数据而修改机械臂状态。

真实使能前必须确认急停可用、机械臂工作区清空、控制器已人工上电且无人处于危险范围。使能后如需恢复到非使能状态，显式调用 `await adapter.disable()`。
