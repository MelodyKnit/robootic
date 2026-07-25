# 本地运行时存储

此目录存放与机器相关、可变或敏感的运行时产物，例如真实相机标定结果、采集帧、模型权重、SDK 日志、设备快照和本地覆盖配置。

除本 README 外，此目录中的所有内容均被 Git 忽略。不得将其用作受版本控制的默认配置或测试资源；可复现模板应存放在 `configs/` 或 `data/`。

真实 JAKA 控制器配置应从 `configs/jaka-hardware.example.json` 复制到本目录后再填写。该本地副本可保存控制器地址和是否允许显式使能，但不得记录密码、令牌、完整遥测或采集数据。读取 J1 至 J6 关节角时，使用此本地文件运行 `poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary`；命令不会为读取而改变机械臂状态。

真实海康 USB 相机配置应从 `configs/hikvision-usb.example.json` 复制到本目录后填写相机序列号和真实标定标识，并作为显式 `--config-file` 传入网页服务。设备成功应用网页参数后，服务会将实际生效值写回该文件根对象的 `camera_parameters`，并在启动或重连的首帧前恢复；这不会调用相机的设备持久化命令。不得将采集图像、完整帧载荷、相机 SDK 日志或设备私有设置提交到版本库。

人体姿态功能的配置副本、COCO Keypoint R-CNN 权重和浏览器选择的 `pose.target_joint` 也必须置于本目录。例如可使用 `localstore/pose-preview.json` 与 `localstore/models/keypointrcnn_resnet50_fpn_coco.pth`。网页服务只会更新显式传入本机 JSON 内的目标关节，不会保存图像、骨架序列或推理缓存。

当受 Python 3.7 工具链限制而必须进行经核验的 CUDA Torch 离线安装时，官方 wheel 可暂存于 `localstore/packages/`。该目录只保留机器本地的、可复用的二进制包，必须继续被 Git 忽略；不要把普通 Python 依赖、开发缓存或未校验下载放入其中。
