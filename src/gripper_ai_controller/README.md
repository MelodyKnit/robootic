# `gripper_ai_controller`

本包是视觉引导机器人和夹爪运行时的应用边界。不包含任何厂商 SDK、网络端点、串口连接、相机连接或直接硬件指令。

## 入口点

- `python -m gripper_ai_controller run --config-file configs/development.json`：运行已配置的开发仿真。
- `python -m gripper_ai_controller reload --config-file configs/development.json`：在安全生命周期关闭后显式重载开发组件。
- `python -m gripper_ai_controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary`：只读取一个已配置 JAKA 目标的 J1–J6 关节角；不启动完整运行时，不使能或移动机械臂。

## 包结构

- `core/`：负责生命周期、事件、授权和执行调度。
- `domain/`：负责所有组件共享的设备无关合约。
- `adapters/`：将合约转换为设备、模拟器或相机实现。
- `plugins/`：包含感知、规划和观察者行为。
- `bootstrap/`：将项目根目录 `configs/` 中的 JSON 配置转换为运行时图。
- `services/`：包含确定性的横切策略，如安全授权。
- `image_servo_simulation/`：只读公开图片的图像居中虚拟关节模型与控制台仿真，不导入设备适配器或执行运行时。

除运行时入口外，还提供 `python -m gripper_ai_controller image-centering-simulate --config-file configs/image-centering-simulation.example.json`。该命令仅使用公开图片、CUDA 姿态模型与内存图像平面模型，适合在接入任何运动设计前验证锁定关节与“向中心收敛”的计算方向。

运行时 JSON 文件统一位于项目根 `configs/`；设置和系统行为请参见项目级 [README](../../README.md) 和 [架构文档](../../docs/architecture.md)。
