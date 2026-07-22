# 测试

测试使用 Python 标准库的 `unittest` 运行器和内存适配器图。测试绝不能依赖硬件、厂商 SDK、相机或网络连接。

从项目根目录运行：

```powershell
python -m unittest discover -s tests -v
```

覆盖范围包括：生命周期/重载行为、安全授权、异常和过期感知数据拒绝、固定/工具相机变换、主/镜像目标调度以及适配器故障隔离。

`test_jaka_adapter.py` 只使用可注入的内存假客户端，验证 JAKA 登录、状态映射、显式使能许可、去使能、配置构造、缺失本机 SDK 的错误映射与运动拒绝。单元测试不会加载真实 SDK、连接控制器或改变机械臂状态；项目根 `temp/gripper-ai-controller/` 下的连接冒烟脚本仅用于人工明确发起的只读本机检查。

`test_hikvision_adapter.py` 只使用可注入的假 MVS 客户端，验证相机打开、单帧映射、帧观察者、缺失本机运行库的错误映射、失败健康状态、关闭清理和配置构造。`test_vision_adapter.py` 验证实例级 `on_frame()` 装饰器注册、多相机隔离、顺序交付和同步回调拒绝。单元测试不会加载 MVS DLL、打开真实相机或采集图像。

`test_submission_paths.py` 还验证提交前绝对路径检查的识别规则，并确认当前 Git 提交候选文件不包含文件系统绝对路径。
