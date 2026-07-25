# JAKA 机器人适配器

本包将 JAKA Python SDK 2.1.2 的同步接口转换为项目的 `RobotAdapter` 合约。它目前只支持安全连接、只读状态查询、只读关节角查询、显式使能和显式去使能；不支持任何关节、直线、伺服或其他运动指令。

## SDK 文件

开发机必须将官方 Windows Python 64 位 SDK 中的 `jkrc.pyd` 与 `jakaAPI.dll` 复制到本包目录。它们必须与 `robotic` 的 Python 3.7 64 位环境匹配，并依赖 Microsoft Visual C++ 运行库。不得从工作区 `documents/` 目录动态导入 SDK。由于当前仓库没有可确认的二进制再分发授权，这两个文件会保留为本机资产，并由 Git 与 Poetry 构建排除；克隆项目后必须从官方 SDK 重新复制。

## 安全边界

- `startup()` 只执行 `RC(controller_ip)` 和 `login()`；不会上电、使能或移动。
- `initialize()` 与 `get_status()` 只读取 `get_robot_status()` 返回的遥测数据。
- `get_joint_positions()` 只执行 SDK 的 `get_joint_position()`，返回带本机采集时间的 `JointPositionSnapshot`。`joint_positions_rad` 的六个值按 `J1` 至 `J6` 排列，单位为弧度；它们是关节空间坐标，不是六个实体关节在机器人基坐标系中的三维 `x/y/z` 位置。
- `enable()` 不会调用 `power_on()`；只有 `allow_enable=True`、控制器已上电且未报告故障或急停时，才会调用 `enable_robot()`。
- `execute()` 始终拒绝机器人运动指令，规划器和运行时无法绕过该限制。
- `disable()` 是显式恢复动作；`shutdown()` 仅 `logout()`，不会隐式去使能或断电。

控制器 IP 属于本机设备设置，调用方必须从 `localstore/` 的本地配置或其他显式本地输入提供，禁止写入受版本控制的 `configs/` 文件。

`configs/jaka-hardware.example.json` 提供了不含真实地址的运行时模板。应将其复制至 `localstore/` 后填写地址，再通过显式 `--config-file` 参数运行。加载该配置不会导入 SDK、建立连接或改变设备状态；只有运行时启动 JAKA 目标时才会登录。

## 使用方式

```python
adapter = JakaAdapter(controller_ip=controller_ip, allow_enable=False)
await adapter.startup()
status = await adapter.initialize()
await adapter.shutdown()
```

## 一次性读取关节角

将 `configs/jaka-hardware.example.json` 复制到 `localstore/`，填写本机控制器 IP 后，从子项目根目录执行：

```powershell
poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

该命令只启动选定的 JAKA 适配器，执行 `login -> get_joint_position -> logout`，随后在标准输出中给出 `J1` 至 `J6` 的弧度与角度制数值。它不会启动相机、夹爪、网页服务或运行时调度，也不会调用 `power_on()`、`enable_robot()`、`disable_robot()` 或任何运动接口。控制器未上电或未使能时，SDK 的只读错误会原样映射为命令失败；命令不会为读取数据而修改机械臂状态。

真实使能前必须确认急停可用、机械臂工作区清空、控制器已人工上电且无人处于危险范围。使能后如需恢复到非使能状态，显式调用 `await adapter.disable()`。
