# 海康 USB3 Vision 适配器

本包通过项目内本机复制的 MVS Python 封装和 Windows x64 运行库实现 `VisionAdapter`。它只负责枚举、打开、获取原始帧和释放相机资源；目标检测、分割、姿态估计、抓取规划和相机参数标定均不属于本包。

## 生命周期和安全边界

- `startup()`：枚举并打开一个 MVS USB3 Vision 相机，不启动取流，不修改触发、曝光、增益、帧率或持久化参数。
- `capture()`：按当前设备已有配置启动采集并复制一帧原始像素数据；不会保存文件，也不会运行感知模型。成功构造 `ImageFrame` 后会按注册顺序调用该实例的 `on_frame()` 异步观察者。
- `shutdown()`：按停止取流、关闭设备、销毁句柄、反初始化 SDK 的顺序释放资源。
- 未配置 `camera_serial` 时，只有枚举到一台 MVS USB 相机才允许打开；存在多台设备时必须从本机私有配置指定序列号。

## 配置

受版本控制的 [配置模板](../../../../../configs/hikvision-usb.example.json) 不包含真实序列号或标定数据。将模板复制到 `localstore/` 后填入相机序列号和真实标定标识，再通过显式 `--config-file` 传入运行时。

`camera_id` 与 `calibration_id` 分别用于帧标识和标定关联。它们不改变设备本身的 UserID、曝光、触发或其他相机参数。

## SDK 文件

- `sdk/`：从官方 MVS 开发包复制的 Python ctypes 封装。保留厂商原始代码和注释。
- `runtime/win64/`：从官方 MVS Runtime 复制的 Windows x64 运行库。Python 3.7 不支持 `os.add_dll_directory()`，客户端会在加载厂商封装前将该包资源目录置于当前进程 DLL 搜索路径。

当前本机验证的组合为 MVS 5.0、MVS SDK 4.7.x、Windows x64 和 `robotic` Python 3.7 x64。MVS 安装资料未明确授予厂商二进制或 Python 封装的再分发权限；在取得确认前，`sdk/`、`runtime/` 和安装包许可证通知均为 Git 忽略的本机资产，且 Poetry 构建会排除它们。克隆项目后，从官方 MVS 安装包按上述目录结构重新复制这些文件。

## 验证

```powershell
conda run -n robotic python -m unittest tests.test_hikvision_adapter -v
```

单元测试使用假客户端，不打开真实相机。实际硬件检查仅应执行 `startup()` 后立即 `shutdown()`；首次取帧需要由操作者明确发起。

帧观察者的通用注册方式、错误语义和多相机边界见上级 [适配器说明](../README.md#帧观察者)。
