# 测试

测试使用 Python 标准库的 `unittest` 运行器和内存适配器图。测试绝不能依赖硬件、厂商 SDK、相机或网络连接。

从项目根目录运行：

```powershell
python -m unittest discover -s tests -v
```

覆盖范围包括：生命周期/重载行为、安全授权、异常和过期感知数据拒绝、固定/工具相机变换、主/镜像目标调度以及适配器故障隔离。`test_web.py` 使用假相机和 FastAPI 测试客户端验证网页配置校验、RGB/Mono JPEG 编码、单一采集循环、故障重试、快照错误码、MJPEG 多客户端帧复用、公开帧序号移除、参数写入开关、即时参数和保存后重新采集参数；它不打开真实相机，也不启动 Web 监听端口。`test_camera_parameter_persistence.py` 验证显式 JSON 配置的原子写回、相机/适配器身份校验、写入失败的部分成功语义、服务重建或断连重连前的参数恢复、自动控制切换时移除失效手动覆盖，以及恢复失败时首帧不会发布。

`test_jaka_adapter.py` 只使用可注入的内存假客户端，验证 JAKA 登录、状态映射、专用六轴关节角读取、非有限或非六维遥测拒绝、`jaka-joints` CLI 输出、显式使能许可、去使能、配置构造、缺失本机 SDK 的错误映射与运动拒绝。单元测试不会加载真实 SDK、连接控制器或改变机械臂状态；项目根 `temp/gripper-ai-controller/` 下的连接冒烟脚本仅用于人工明确发起的只读本机检查。

`test_hikvision_adapter.py` 只使用可注入的假 MVS 客户端，验证相机打开、单帧映射、Mono8/RGB8/Bayer 像素规范化、转换长度校验、MVS 缓冲区释放、帧观察者、缺失本机运行库的错误映射、失败健康状态、关闭清理、关闭等待正在执行的原生取帧、运行时参数范围、即时写入、暂停取流/写入/恢复取流顺序及失败后的恢复尝试。`test_vision_adapter.py` 验证实例级 `on_frame()` 装饰器注册、多相机隔离、顺序交付和同步回调拒绝。单元测试不会加载 MVS DLL、打开真实相机或采集图像。

`test_camera_selection.py` 覆盖相机选择配置的旧文件兼容、回环限制、严格字段校验、`localstore/` 原子持久化和配置身份绑定，以及设备目录、成功切换、禁用、设备消失、打开失败、持久化失败回滚、请求取消后的提交一致性和旧画面/姿态缓存清理。测试只使用两设备内存适配器与临时 JSON，不会枚举或打开真实 USB 相机。前端 `camera-selection.spec.ts` 验证同一 MJPEG 元素复用、发现与切换失败保留旧画面、新源状态重置，以及旧状态和旧参数异步响应不能污染新设备界面。

`test_submission_paths.py` 还验证提交前绝对路径检查的识别规则，并确认当前 Git 提交候选文件不包含文件系统绝对路径。

网页 HTTP 契约测试使用 Poetry 的开发依赖 `httpx==0.24.1`，因为 FastAPI 的 `TestClient` 需要它；它不属于预览服务的运行依赖。

`test_pose.py` 使用内存帧和假推理器验证单色帧三通道复制、最高置信度单人选择、关节阈值拒绝、连续丢失、关节切换、JSON 原子持久化和 CUDA 预检逻辑。`test_web.py` 同时覆盖姿态 API 的禁用状态、未知相机、有效骨架快照、目标关节保存、无效关节 `422` 和无 CUDA 时启动阻断。测试不下载权重、不加载 Torch 模型、不调用 GPU、不启动 Web 监听端口，也不连接机器人或夹爪。

`test_vision.py` 覆盖 RGB8/Mono8 帧质量指标、损坏帧降级、人体候选复用、主人体选择、COCO 关节可见性、图片清单字段与 SHA-256、必要质量警告、离线评测配置以及禁用姿态时的缓存分析。`test_web.py` 还验证 `GET /api/cameras/{camera_id}/vision/analysis` 的禁用、有效和未知相机响应。它们均使用假推理器和临时图片，不运行真实 CUDA 模型；完整公开素材验收由 `vision-evaluate` 在本机 GPU 环境单独执行。

`test_image_servo_simulation.py` 只验证纯内存图像平面模型：中心死区、水平/垂直误差方向、单步限幅、虚拟关节限位、过期或无效输入保持、静态图片收敛、人体/手腕选择、JSON 配置拒绝和控制台报告。它不导入或调用 `Runtime`、JAKA、相机、`RobotAdapter` 或网络连接；GPU 图片验收由 `image-centering-simulate` 独立执行。
