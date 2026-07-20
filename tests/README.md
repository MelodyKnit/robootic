# 测试

测试使用 Python 标准库的 `unittest` 运行器和内存适配器图。测试绝不能依赖硬件、厂商 SDK、相机或网络连接。

从项目根目录运行：

```powershell
python -m unittest discover -s tests -v
```

覆盖范围包括：生命周期/重载行为、安全授权、异常和过期感知数据拒绝、固定/工具相机变换、主/镜像目标调度以及适配器故障隔离。
