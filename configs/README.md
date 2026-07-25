# 运行时配置文件

此目录是唯一存放受版本控制运行时配置文件的位置。当前运行时仅接受 JSON 格式，并使用 Python 3.7 标准库进行解析。

- `development.json`：安全的内存主目标、镜像目标和相机配置。
- `tool-camera.json`：包含工具端安装相机标定拓扑的同一安全组件图。
- `production.example.json`：不可直接运行的模板。在实现项目本地真实适配器前，生产环境保持故障关闭。
- `jaka-hardware.example.json`：JAKA 连接模板。复制到 `localstore/` 后填入本机控制器地址；默认关闭使能，且不应直接以模板运行。
- `hikvision-usb.example.json`：海康 USB3 Vision 相机模板。复制到 `localstore/` 后填入相机序列号与真实标定标识；模板默认禁止网页写入相机参数，并以最新帧优先方式预览。
- `pose-preview.example.json`：人体 2D 姿态追踪模板。默认关闭；复制到 `localstore/` 后填写本机相机与模型权重路径，再显式启用。
- `vision-evaluation.example.json`：不创建相机的离线图片评测模板。它只引用 `localstore/` 中的 CUDA 模型权重，可与 `data/vision-fixtures/` 一起验证 RGB 和 Mono8 推理路径。
- `image-centering-simulation.example.json`：静态公开图片的图像居中虚拟仿真模板。它仅包含 CUDA 姿态设置与虚拟图像平面模型，不包含相机、机器人、夹爪、执行目标或真实设备字段。
- `invalid-component.fixture.json`：仅供加载器测试使用的受版本控制的反向测试夹具。

配置文件只包含组件标识符和非敏感运行设置。不得在此放置令牌、密码、私有 IP 地址、标定采集数据、模型权重或可变运行状态；此类内容应存放在 `localstore/`。

使用本机 JAKA 配置读取六轴关节角时，从子项目根目录执行：

```powershell
poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

该命令只登录选定 JAKA 目标、读取 `J1` 至 `J6` 的弧度关节角并登出。它不会启动相机、夹爪、网页服务或完整运行时，也不会上电、使能、去使能或下发运动指令。`get_joint_position()` 返回的是关节空间角度，不是各实体关节在三维空间中的位置；后者需要另行建立该型号的运动学模型。

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

## 人体姿态段

可选根对象 `pose` 仅配置网页端的单人 2D 姿态显示，默认关闭。支持字段如下：

- `enabled`：严格布尔值。为 `true` 时，服务启动前必须通过 CUDA 11.7 Torch 就绪检查；不满足时服务拒绝启动，不回退到 CPU。
- `model`：固定为 `"torchvision-keypoint-rcnn-resnet50-fpn"`。
- `weights_path`：可选的 `localstore/` 相对权重路径，不允许绝对路径或 `..`。
- `device`：固定为 `"cuda"`。
- `inference_max_side`：模型输入图像最长边，默认 `768`；预处理与 Torchvision 内部图像变换共同遵守该上限，保持原始画面的宽高比，模型输出会缩放回原始相机像素坐标。
- `max_inference_fps`：`1` 至 `30`，默认 `2`；采集帧率不会因该值提升。
- `torch_cpu_threads`、`torch_interop_threads`：Torch CPU 辅助线程数，默认分别为 `2`、`1`；用于限制 CUDA 推理周边的 CPU 线程争用。
- `overlay_max_frame_lag_seconds`：骨架来源帧与最新浏览器 JPEG 的允许最大时间差，默认 `0.35` 秒。超过该值时网页保持 MJPEG 实时画面并隐藏骨架。
- `person_confidence_threshold`、`joint_confidence_threshold`：`0` 至 `1` 的置信度阈值。
- `lost_after_frames`：`1` 至 `120` 的丢失计数上限。
- `target_joint`：COCO 17 个关节之一，默认 `right_wrist`。
- `tracking_min_iou`：`0` 至 `1` 的人体框最小 IoU，默认 `0.20`；当前帧与已锁定人体框达到该阈值即可视为连续候选。
- `tracking_max_center_distance`：`0` 至 `1` 的人体框中心最大归一化距离，默认 `0.25`；用于人体移动时的保守关联补充。
- `motion_speed_threshold`：非负的归一化图像坐标/秒阈值，默认 `0.04`；达到该值时返回 `moving: true`。
- `motion_max_interval_seconds`：`0.05` 至 `30` 秒，默认 `1.5`；超过该采集间隔只重建基线，不输出跨间隔速度。
- `draw_skeleton`：是否向网页返回骨架绘制开关。

姿态启用后的配置应放在 `localstore/`。网页选择关节时，服务只会原子写回该显式 `--config-file` 的 `pose.target_joint` 字段；权重、图像和推理缓存绝不写入 `configs/` 或 `data/`。详细模型和安全边界见 [姿态感知包说明](../src/gripper_ai_controller/pose/README.md)。

## 成像质量分析段

可选根对象 `vision_analysis` 配置网页和离线评测共用的只读帧质量阈值。省略时使用安全默认值，支持字段如下：

- `minimum_width`、`minimum_height`：正整数，默认 `320`、`240`；较小图像返回 `resolution_low` 警告。
- `minimum_brightness`、`maximum_brightness`：`0` 至 `255` 的亮度均值范围，默认 `20.0` 至 `235.0`；范围外返回亮度警告。
- `minimum_contrast`：非负对比度最小值，默认 `8.0`。
- `minimum_sharpness`：非负拉普拉斯方差最小值，默认 `5.0`。
- `sample_max_side`：质量计算使用的缩采样图最长边，默认 `640`；响应中的宽高仍为原始采集尺寸。
- `max_analysis_fps`：质量诊断最大频率，默认 `1`。分析忙碌时只保留最新待分析帧，不阻塞相机采集。

这些值只决定诊断警告，不会修改曝光、停止采集或阻断相机。质量结果始终来自已采集帧的内存缓存。

海康 `components.vision_adapter_settings.frame_delivery_mode` 只允许 `latest_only` 或 `sequential`。网页预览必须使用默认的 `latest_only`：MVS 在相机句柄已打开、开始取流前设置策略，使取帧时清除旧队列并交付最新图像，以避免慢速浏览器显示 FIFO 历史帧。已连接 USB 相机在开始取流后设置策略会返回调用顺序错误。`sequential` 仅为后续离线逐帧任务保留，不能用于低延迟预览。

## 离线评测配置

`vision-evaluation.example.json` 不含 `camera`、`components`、`web`、机器人或夹爪字段。它的 `pose.enabled` 必须保持 `true`，因为 `vision-evaluate` 必须拒绝 CPU 回退；`weights_path` 仍必须是 `localstore/` 下不含 `..` 的相对路径。

从项目根目录执行：

```powershell
poetry run gripper-ai-controller vision-evaluate --config-file configs/vision-evaluation.example.json
```

该命令仅从 `data/vision-fixtures/manifest.json` 加载已校验哈希的公开素材，分别评测 RGB8 和确定性 Mono8。JSON 报告或叠加图的可选输出必须位于 `temp/gripper-ai-controller/`；不能输出到 `data/`、`configs/` 或 `localstore/`。

## 图像居中仿真配置

`image-centering-simulation.example.json` 只能包含 `pose` 和 `simulation` 两个根对象。它拒绝 `camera`、`components`、`targets`、网络地址和任何硬件适配器设置，因此 `image-centering-simulate` 无法通过配置意外启动相机或机械臂。

`simulation` 段的字段如下：

- `fixture_id`：`data/vision-fixtures/manifest.json` 中的公开素材标识；默认 `full-body-front`。
- `pixel_format`：`rgb8` 或 `mono8`；默认 `mono8`，用于走与单色相机一致的模型预处理路径。
- `desired_normalized`：二维目标图像坐标，首版固定使用画面中心 `[0.5, 0.5]`。
- `center_deadband`：目标进入中心区域后停止虚拟更新的归一化死区。
- `maximum_pose_age_seconds`：模拟输入允许的最大时间差；过期结果保持虚拟关节不动。
- `maximum_joint_step_rad`、`gain`、`damping`、`maximum_iterations`：阻尼最小二乘图像伺服的受限数值参数。
- `initial_joint_positions_rad`、`joint_lower_limits_rad`、`joint_upper_limits_rad`：六个虚拟关节的初始值和限位。
- `image_jacobian`：严格为 `2 x 6` 的归一化图像位移/弧度矩阵，仅描述示例虚拟模型。

从项目根目录执行：

```powershell
poetry run gripper-ai-controller image-centering-simulate --config-file configs/image-centering-simulation.example.json
```

该命令只在控制台输出预测目标位置和虚拟六轴状态，不写入配置或数据目录，也不会创建相机、网页服务、`Runtime`、机器人或夹爪连接。`image_jacobian` 与限位不是现场标定；真实图像伺服参数、相机内参、手眼标定和机器人安全边界必须保存在后续受控设计中，而不能直接套用此模板。
