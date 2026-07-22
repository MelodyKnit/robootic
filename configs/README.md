# 运行时配置文件

此目录是唯一存放受版本控制运行时配置文件的位置。当前运行时仅接受 JSON 格式，并使用 Python 3.7 标准库进行解析。

- `development.json`：安全的内存主目标、镜像目标和相机配置。
- `tool-camera.json`：包含工具端安装相机标定拓扑的同一安全组件图。
- `production.example.json`：不可直接运行的模板。在实现项目本地真实适配器前，生产环境保持故障关闭。
- `jaka-hardware.example.json`：JAKA 连接模板。复制到 `localstore/` 后填入本机控制器地址；默认关闭使能，且不应直接以模板运行。
- `hikvision-usb.example.json`：海康 USB3 Vision 相机模板。复制到 `localstore/` 后填入相机序列号与真实标定标识；模板默认禁止网页写入相机参数。
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
- `camera_controls_enabled`：`false`，严格布尔值。设为 `true` 后允许网页写入当前相机适配器公开的固定参数白名单；版本化海康模板必须保持 `false`，真实写入仅可在 `localstore/` 本机配置显式开启。

网页服务只读取上述设置和 `camera`、`components.vision`、`components.vision_adapter_settings`、根 `camera_parameters`。即使同一配置还声明 `targets`、插件或安全设置，它也不会构建或启动它们。真实序列号、真实标定标识和本机覆盖仍必须置于被 Git 忽略的 `localstore/` 配置文件。

## 相机参数持久化段

可选的根对象 `camera_parameters` 保存网页服务最近一次成功应用后从设备确认的实际参数值。对象键必须是当前相机适配器公开白名单中的参数名，值必须为标量；服务会保存本次更新的实际值，以及自动曝光、自动增益和帧率开关等依赖手动项在下次启动时所需的前置开关。

服务只会在设备成功应用参数后，将实际生效值原子写回启动 CLI 显式传入的 JSON 文件；它不会从源码位置、工作目录或仓库遍历推导其他配置文件。启动相机或断连重连后，服务会在首帧前恢复 `camera_parameters`。恢复失败时预览保持降级状态并按采集重试间隔再次尝试，不会静默改回默认参数。

`web.camera_controls_enabled` 是浏览器写入与自动恢复共用的本机授权开关。该值为 `false` 时，服务可继续只读取帧，但绝不会因 `camera_parameters` 在启动或重连期间写入设备。

这项持久化不调用相机的 `FeatureSave`、`UserSetSave` 或其他厂商设备持久化命令。需要暂停取流的参数仍由后端在同一采集锁内停止取流、写入并恢复取流。若设备已成功应用参数、但 JSON 写回失败，接口会返回明确失败：设备保持新值而配置未保存；修复文件权限或内容后应再次提交参数。

版本化 `configs/` 文件可保存安全、非敏感且可复现的仿真参数；当操作者明确以该文件作为 `--config-file` 启动时，网页服务会写回同一文件，因此 Git 工作区会出现可见变更。真实海康相机应从模板复制一份 JSON 到被 Git 忽略的 `localstore/`，再将该文件作为 `--config-file` 传入；这样设备序列号、标定标识和可变 `camera_parameters` 都不会进入 Git。由于默认监听地址可被局域网访问，开启 `camera_controls_enabled` 前必须确认网络受信任。

请从子项目根目录启动 CLI，使默认 `src/web/dist` 相对于该项目解析。服务不会通过源码位置或仓库遍历推导此目录。
