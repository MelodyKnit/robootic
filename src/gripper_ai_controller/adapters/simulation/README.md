# 仿真适配器

本包提供默认的无硬件开发目标。其适配器将所有状态保持在内存中，有意不打开串口、TCP 套接字、相机、PLC 连接或机器人控制器连接。

- `SimulatedRobotAdapter`：表示六轴机器人，支持镜像遥测修正。
- `SimulatedGripperAdapter`：表示类似 PGI 的位置和夹持状态。
- `SimulatedCameraAdapter`：发出确定性 RGB8 或 Mono8 像素帧，并实现与海康网页接口兼容的 `CameraParameterAdapter`。它可离线验证曝光、增益、帧率和像素格式的即时/保存后重新采集交互，不连接真实相机。

这些适配器是确定性的测试替身，而非物理模拟器。未来的 CoppeliaSim 适配器应实现相同的端口，并可在不更改核心逻辑或插件的情况下替换镜像目标。
