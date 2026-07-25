# 图像居中仿真

本包用公开图片中的人体关节坐标，验证“目标关节向图像中心收敛”的二维图像伺服数学链路。它只维护内存中的六个虚拟关节和一个固定的二维图像雅可比矩阵；不会启动相机、网页服务、运行时、机器人、夹爪或网络连接。

`ImageCenteringController` 使用阻尼最小二乘求解图像中心误差，生成受每步角度和虚拟关节限位约束的更新。`ImageServoSimulationSession` 再使用同一雅可比预测虚拟相机移动后的目标位置，从而可以在一张静态图片上验证收敛过程。

会话还提供 `observe_source_target()`，可按严格递增的采集时间接收同一相机、同一关节的后续二维观测。人物手部在原始固定相机画面中的位移会先更新虚拟投影，再进入下一次居中计算；这用于验证人物移动时的计算方向。当前 CLI 只使用单张公开图片作为可复现实验入口，不订阅真实相机或网页姿态流。

## 边界

- 虚拟关节名称、限位与雅可比只用于可复现实验，不是 JAKA 运动学、手眼标定、碰撞模型或真实速度限制。
- 输出仅写入标准输出；不生成 `RobotCommand`，不调用 `RobotAdapter.execute()`，也不创建 `Runtime`。
- 真机阶段仍需相机内参、工具相机手眼标定、真实机器人运动学、图像雅可比在线更新、工作空间/速度/加速度/碰撞/人机距离限制、目标丢失停机及 `Runtime` 的安全授权。不能仅替换一个适配器后直接执行本包的模拟结果。

## 离线运行

模型权重必须已通过项目命令下载到 `localstore/`，且 CUDA 预检通过：

```powershell
poetry run gripper-ai-controller gpu-check --require-torch
poetry run gripper-ai-controller image-centering-simulate --config-file configs/image-centering-simulation.example.json
```

命令读取 `data/vision-fixtures/manifest.json` 中经过 SHA-256 校验的公开图片。默认模板使用 `Mono8`，因此与单色海康相机的姿态模型输入路径一致。控制台会显示每步预测目标位置、参与变化的虚拟关节、完整六轴状态和终止原因；`simulation_only=true` 明确表示未触发任何硬件行为。

## 扩展方向

未来将图像伺服接入真机前，应在独立规划插件中消费经过标定、时效检查和身份关联的姿态结果，并通过 `Runtime -> SafetyPolicy -> ExecutionTarget` 提出和授权真实指令。该桥接层不属于本包，也不属于当前阶段。
