# 运行时配置文件

此目录是唯一存放受版本控制运行时配置文件的位置。当前运行时仅接受 JSON 格式，并使用 Python 3.7 标准库进行解析。

- `development.json`：安全的内存主目标、镜像目标和相机配置。
- `tool-camera.json`：包含工具端安装相机标定拓扑的同一安全组件图。
- `production.example.json`：不可直接运行的模板。在实现项目本地真实适配器前，生产环境保持故障关闭。
- `jaka-hardware.example.json`：JAKA 连接模板。复制到 `localstore/` 后填入本机控制器地址；默认关闭使能，且不应直接以模板运行。
- `hikvision-usb.example.json`：海康 USB3 Vision 相机模板。复制到 `localstore/` 后填入相机序列号与真实标定标识；模板不会写入相机参数。
- `invalid-component.fixture.json`：仅供加载器测试使用的受版本控制的反向测试夹具。

配置文件只包含组件标识符和非敏感运行设置。不得在此放置令牌、密码、私有 IP 地址、标定采集数据、模型权重或可变运行状态；此类内容应存放在 `localstore/`。

## 网页预览段

可启动 `gripper-ai-controller web` 的配置需包含可选的 `web` JSON 对象。未填写字段使用下列默认值：

- `bind_host`：`"0.0.0.0"`，预览服务监听地址；
- `port`：`8000`，范围为 `1` 至 `65535`；
- `frontend_dist_dir`：`"src/web/dist"`，由 FastAPI 静态托管的已构建前端目录；必须为相对于启动工作目录的路径，不能使用绝对路径或 `..`；
- `stream_fps`：`10`，范围为 `1` 至 `30`；
- `jpeg_quality`：`80`，范围为 `1` 至 `95`；
- `capture_retry_seconds`：`1.0`，范围为 `0.1` 至 `30`。

网页服务只读取上述设置和 `camera`、`components.vision`、`components.vision_adapter_settings`。即使同一配置还声明 `targets`、插件或安全设置，它也不会构建或启动它们。真实序列号、真实标定标识和本机覆盖仍必须置于被 Git 忽略的 `localstore/` 配置文件。

请从子项目根目录启动 CLI，使默认 `src/web/dist` 相对于该项目解析。服务不会通过源码位置或仓库遍历推导此目录。
