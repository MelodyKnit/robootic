# 本地运行时存储

此目录存放与机器相关、可变或敏感的运行时产物，例如真实相机标定结果、采集帧、模型权重、SDK 日志、设备快照和本地覆盖配置。

除本 README 外，此目录中的所有内容均被 Git 忽略。不得将其用作受版本控制的默认配置或测试资源；可复现模板应存放在 `configs/` 或 `data/`。

真实 JAKA 控制器配置应从 `configs/jaka-hardware.example.json` 复制到本目录后再填写。该本地副本可保存控制器地址和是否允许显式使能，但不得记录密码、令牌、完整遥测或采集数据。读取 J1 至 J6 关节角时，使用此本地文件运行 `scripts\run.bat jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary`；命令不会为读取而改变机械臂状态。

真实海康 USB 相机配置应从 `configs/hikvision-usb.example.json` 复制到本目录后填写相机序列号和真实标定标识，并作为显式 `--config-file` 传入网页服务。设备成功应用网页参数后，服务会将实际生效值写回该文件根对象的 `camera_parameters`，并在启动或重连的首帧前恢复；这不会调用相机的设备持久化命令。不得将采集图像、完整帧载荷、相机 SDK 日志或设备私有设置提交到版本库。

人体姿态功能的配置副本、COCO Keypoint R-CNN 权重和浏览器选择的 `pose.target_joint` 也必须置于本目录。例如可使用 `localstore/pose-preview.json` 与 `localstore/models/keypointrcnn_resnet50_fpn_coco.pth`。网页服务只会更新显式传入本机 JSON 内的目标关节，不会保存图像、骨架序列或推理缓存。

通用二维目标检测的本地 Faster R-CNN 状态字典和 YOLO-World ONNX 也必须放在本目录，例如可按模型来源和用途组织到 `localstore/models/object-detection/`。版本库只保存默认关闭的配置结构，**不包含模型文件**；网页服务不会自动下载、转换或更新权重。操作者可从项目根目录显式运行 `scripts\run.bat object-detection-download-fasterrcnn`，将官方 Faster R-CNN COCO 权重安装到默认的 `localstore/models/fasterrcnn_resnet50_fpn_coco.pth`，也可用 `--weights-file` 指定另一个仍位于 `localstore/` 的相对路径。命令会校验完整 SHA-256，校验失败时保留原文件并删除临时文件。YOLO-World ONNX 仍需从符合许可证与部署要求的来源取得，核对完整性和导出契约后，再把相对路径写入本机配置的 `object_detection.models[].model_path`。

Faster R-CNN 档案按固定 COCO 类别顺序解释状态字典。YOLO-World 首期只接受提示类别已在导出前固化的 ONNX，配置中的 `class_names` 必须保持导出顺序；`official-nms` 导出还必须提供 `num_dets`、`boxes`、`scores`、`labels` 四个精确命名输出。不要把浏览器输入、未知模型格式或未校验文件当作运行时开放提示机制。

网页模型选择只保存在当前服务进程内，不会修改本机 JSON。需要下次启动继续使用某一模型时，应由操作者显式更新本机配置的 `object_detection.selected_model_id` 后重启服务。检测缓存只存在于内存，不应将检测框、原始帧或现场样本自动写入此目录。

## 网页 Plugin 运行态

网页预览 Plugin 的可用集合仍由版本化配置中的 `components.plugins.preview` 决定；本目录中的显式启动 JSON 只保存操作者选择的运行态。要让网页启停跨刷新与服务重启保留，可在该 JSON 根对象中写入：

```json
{
  "plugin_runtime": {
    "enabled": {
      "visual-pose-analysis": true,
      "object-pose-analysis": false
    }
  }
}
```

`enabled` 的每个值必须是 JSON `true` 或 `false`，键必须是同一文件 `components.plugins.preview` 已声明的稳定 Plugin ID。省略整个 `plugin_runtime.enabled`，或省略其中某个已配置 ID 时，该 Plugin 默认开启，以兼容旧本机配置。页面刷新只读取此状态，绝不因为刷新而重新启动已关闭的功能模块。

网页持久化启停还要求 `web.plugin_lifecycle_controls_enabled` 为 `true`，且服务严格绑定 `127.0.0.1`。未满足时网页只显示状态，不得修改本机 JSON。关闭 Plugin 只停止该 Plugin 的被动帧分析和生命周期；相机采集、MJPEG 预览、夹爪、JAKA 适配器及其控制权限均保持不变。

单目已知工件功能的空桌背景、ChArUco 内参图、相机内参 JSON、固定板图、操作者示教点和 `WorkcellCalibration` 也必须按设备标识分别存放，例如 `localstore/object-pose/hikvision-usb/`。请使用 `configs/object-pose-preview.example.json` 作为模板，并通过 `scripts\calibration.bat` 运行标定命令；只有显式的 `calibration-capture-charuco` 会以只读方式连接所选相机，其他命令只读写本地文件，所有命令都不会连接 JAKA 或夹爪。具体步骤见 `docs/object-pose-calibration.md`。

当受 Python 3.7 工具链限制而必须进行经核验的 CUDA Torch 离线安装时，官方 wheel 可暂存于 `localstore/packages/`。该目录只保留机器本地的、可复用的二进制包，必须继续被 Git 忽略；不要把普通 Python 依赖、开发缓存或未校验下载放入其中。
