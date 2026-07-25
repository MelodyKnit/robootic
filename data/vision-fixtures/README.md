# 视觉离线评测素材

本目录保存人体姿态视觉链路的可再分发小型图片集。它只用于离线算法验收，不会被网页预览服务读取，也不会写入或覆盖真实相机画面。

## 素材与许可

所有文件均来自 NASA 官方图片库，清单将它们标记为 `Public Domain`。素材由 NASA 发布，使用时仍须遵守 [NASA 图像与媒体使用指南](https://www.nasa.gov/nasa-brand-center/images-and-media/)，不得暗示 NASA 对本项目、模型结论或任何机械臂行为背书。

`manifest.json` 为唯一的机器可读来源：每项都保存详情页链接、作者/机构、许可、SHA-256、预期人数、最低可见关节数、必要关节、必要成像警告和人工复核要求。不要以相同文件名直接替换图片；变更图片后必须先复核许可，再更新哈希和验收条件。

## 场景覆盖

- `full-body-front.jpg`：完整站立人体，用于人体框、主要全身关节和右手腕图像居中仿真起点。
- `upper-body.jpg`：仅前景上半身，用于画面外关节状态。
- `occluded-side.jpg`：侧身并被设备遮挡的人体，用于低置信度和不可见关节。
- `no-person.jpg`：无人地形，用于无人体结果。
- `low-light.jpg`：低亮度夜间影像，用于帧质量警告和无人体结果。

## 执行评测

必须先通过 CUDA Torch 预检并将模型权重放在 `localstore/`，再从项目根目录运行：

```powershell
poetry run gripper-ai-controller vision-evaluate --config-file configs/vision-evaluation.example.json
```

命令会对每张图片分别按 `rgb8` 和确定性 `mono8` 路径评测。它只加载本地权重和图片，不创建相机适配器、不启动网页服务，也不会连接机器人或夹爪。可选叠加图仅允许输出到 `temp/gripper-ai-controller/`。

首次在某张 GPU 或模型权重上运行时，人工检查叠加图与 `manual_review` 后，再将结果写入工作日志；清单的阈值不是对未知现场单色相机精度的承诺。
