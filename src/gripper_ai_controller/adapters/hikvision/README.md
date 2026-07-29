# 海康 USB3 Vision 适配器

本包通过项目内本机复制的 MVS Python 封装和 Windows x64 运行库实现 `VisionAdapter` 与可选的 `CameraParameterAdapter`。它负责枚举、打开、获取原始帧、释放相机资源，以及受固定白名单约束的运行时参数读取与写入；目标检测、分割、姿态估计、抓取规划和相机标定均不属于本包。

## 生命周期和安全边界

- `startup()`：枚举并打开一个 MVS USB3 Vision 相机，不启动取流，不修改任何参数。
- `capture()`：按当前设备已有配置启动采集并复制一帧原始像素数据；不会保存文件，也不会运行感知模型。`ImageFrame` 会包含宽度、高度和规范化像素格式：`Mono8` 保留为 `mono8`，`Mono10/12/14/16`（含设备支持的 Packed 形式）在释放 MVS 缓冲区前转换为 `mono8`，`RGB8` 为 `rgb8`，其他受支持彩色格式转换为 `rgb8`。成功构造帧后会按注册顺序调用该实例的 `on_frame()` 异步观察者。
- `get_camera_parameters()`：在相机已打开时读取实际可用的固定参数白名单。浮点范围和枚举选项均来自设备；当前型号不支持、不可读或受访问条件限制的节点不会出现在结果中。
- `update_camera_parameters()`：只允许自动曝光 `ExposureAuto`、曝光时间 `ExposureTime`、自动增益 `GainAuto`、增益 `Gain`、帧率开关 `AcquisitionFrameRateEnable`、帧率 `AcquisitionFrameRate` 和像素格式 `PixelFormat`。曝光、增益和帧率为实时参数；像素格式需停止取流后应用，再自动恢复取流。该方法不会接受任意节点名、触发模式或持久化用户设置。
- `shutdown()`：先等待正在执行的原生取帧返回，再按停止取流、关闭设备、销毁句柄、反初始化 SDK 的顺序释放资源。关闭请求最多受当前 `frame_timeout_ms` 与底层 SDK 返回时间影响，不能并发释放正在被 MVS 调用使用的句柄。
- 未配置 `camera_serial` 时，只有枚举到一台 MVS USB 相机才允许打开；存在多台设备时必须从本机私有配置指定序列号。
- `list_camera_devices()`：只读枚举当前 MVS USB 设备并返回不透明 ID、显示名称和型号，不打开第二个相机句柄，也不暴露厂商序列号。
- `configure_camera_device()`：只允许在适配器已停止时切换到刚刚枚举到的设备；打开、回滚和本机持久化由网页服务生命周期统一协调。

默认 `frame_delivery_mode` 为 `latest_only`。每次 MVS 相机句柄打开且开始取流前，适配器会设置 `MV_GrabStrategy_LatestImagesOnly`，使后续取帧丢弃等待队列中的旧图像并优先交付当前画面。已连接 USB 相机在开始取流后设置该策略会返回调用顺序错误，因此该设置失败会阻止本次取流并报告相机错误，不会静默回退为 FIFO。`sequential` 仅为未来离线逐帧任务保留；它显式使用 `MV_GrabStrategy_OneByOne`，不能用于低延迟网页预览。适配器所有原生 MVS 调用共用一个专用单工作线程，避免 SDK 取帧、节点操作和关闭过程与网页的其它后台任务争用默认线程池。

## 低延迟排查

`web.stream_fps` 是网页采集循环的上限，不会强制相机以该帧率输出。`latest_only` 只能丢弃 FIFO 中的历史帧，确保浏览器拿到最新图像；它不能提高传感器、触发模式或 USB 链路的实际帧率。排查卡顿时，应以 MVS 的 `ResultingFrameRate` 为准，而不是只看 `AcquisitionFrameRate`：后者只有在 `AcquisitionFrameRateEnable` 开启时才是有效限制。

以 `2448 x 2048 Mono10Packed` 为例，单帧约为 `5.98 MiB`；约 `5 FPS` 的数据量接近 USB 2.0 High-Speed 的实际有效吞吐。若 Windows 或 MVS 显示设备协商到 High-Speed，应先将相机直连已知的 USB 3.x SuperSpeed 主机端口，使用合格的短 USB 3.x 数据线，并绕过 USB 2.0 集线器、显示器扩展口和带宽受限的扩展坞，再重新插拔设备使链路重新协商。不要在网页服务启动时自动修改 `TransferSize`、`TransferWays`、触发模式、曝光或像素格式；这些实验必须由操作者在单项、可回退的授权下进行。

## 配置

受版本控制的 [配置模板](../../../../../configs/hikvision-usb.example.json) 不包含真实序列号或标定数据。将模板复制到 `localstore/` 后填入相机序列号和真实标定标识，再通过显式 `--config-file` 传入运行时。

`camera_id` 与 `calibration_id` 分别用于帧标识和标定关联。它们不改变设备本身的 UserID、曝光、触发或其他相机参数。网页参数写入还必须由 `web.camera_controls_enabled` 在本机私有配置中显式开启；适配器本身不读取配置开关，网页服务负责该访问许可。

网页多设备选择使用 `camera_selection.selected_device_id`，其值是根据序列号单向派生的稳定不透明标识；旧的本机 `camera_serial` 配置继续兼容，但不会通过 Web 返回。`camera_selection.calibration_ids` 可为每个不透明设备 ID 绑定独立标定。切换到未绑定设备时 `ImageFrame.calibration_id` 为 `None`，避免把旧相机标定错误套用到新画面。

参数写入成功后，网页服务会把适配器确认的实际生效参数写回启动时显式传入 JSON 的根 `camera_parameters`，并在适配器启动或断连重连后的首帧前恢复。实际海康配置必须置于 Git 忽略的 `localstore/`；受版本控制的 `configs/` 文件仅作为模板。若设备已成功更新而 JSON 写回失败，调用方会收到“设备已生效、配置未保存”的明确失败，设备不会被回滚。实现不会调用 `MV_CC_FeatureSave`、`UserSetSave` 或其他厂商持久化命令。取帧、读取、写入、停止取流、恢复取流和关闭共用同一异步锁，避免 MVS 句柄被并发调用。

## SDK 文件

- `sdk/`：从官方 MVS 开发包复制的 Python ctypes 封装。保留厂商原始代码和注释。
- `runtime/win64/`：从官方 MVS Runtime 复制的 Windows x64 运行库。Python 3.7 不支持 `os.add_dll_directory()`，客户端会在加载厂商封装前将该包资源目录置于当前进程 DLL 搜索路径。

当前本机验证的组合为 MVS 5.0、MVS SDK 4.7.x、Windows x64 和 `robotic` Python 3.7 x64。MVS 安装资料未明确授予厂商二进制或 Python 封装的再分发权限；在取得确认前，`sdk/`、`runtime/` 和安装包许可证通知均为 Git 忽略的本机资产，且 Poetry 构建会排除它们。克隆项目后，从官方 MVS 安装包按上述目录结构重新复制这些文件。

## 验证

```powershell
conda run -n robotic python -m unittest tests.test_hikvision_adapter -v
```

单元测试使用假客户端，不打开真实相机。实际硬件检查应先读取 `get_camera_parameters()` 的实际范围和枚举；写入验收只应将当前值写回，确认实时参数或停止/恢复采集链路，不应使用未经确认的新曝光、增益或像素格式值。首次取帧和任何参数写入都需要由操作者明确发起。

帧观察者的通用注册方式、错误语义和多相机边界见上级 [适配器说明](../README.md#帧观察者)。
