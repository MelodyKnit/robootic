# `gripper_ai_controller`

本包是视觉引导机器人和夹爪运行时的应用边界。不包含任何厂商 SDK、网络端点、串口连接、相机连接或直接硬件指令。

## 入口点

- `python -m gripper_ai_controller run --config-file configs/development.json`：运行已配置的开发仿真。
- `python -m gripper_ai_controller reload --config-file configs/development.json`：在安全生命周期关闭后显式重载开发组件。

## 包结构

- `core/`：负责生命周期、事件、授权和执行调度。
- `domain/`：负责所有组件共享的设备无关合约。
- `adapters/`：将合约转换为设备、模拟器或相机实现。
- `plugins/`：包含感知、规划和观察者行为。
- `bootstrap/`：将项目根目录 `configs/` 中的 JSON 配置转换为运行时图。
- `services/`：包含确定性的横切策略，如安全授权。

运行时 JSON 文件统一位于项目根 `configs/`；设置和系统行为请参见项目级 [README](../../README.md) 和 [架构文档](../../docs/architecture.md)。
