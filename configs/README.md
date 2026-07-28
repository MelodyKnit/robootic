# 运行时配置文件

此目录是唯一存放受版本控制运行时配置文件的位置。当前运行时仅接受 JSON 格式，并使用 Python 3.7 标准库进行解析。

- `development.json`：安全的内存主目标、镜像目标和相机配置。
- `tool-camera.json`：包含工具端安装相机标定拓扑的同一安全组件图。
- `production.example.json`：不可直接运行的模板。在实现项目本地真实适配器前，生产环境保持故障关闭。
- `jaka-hardware.example.json`：JAKA 连接模板。复制到 `localstore/` 后填入本机控制器地址；默认关闭使能，且不应直接以模板运行。
- `jaka-joint-dry-run.example.json`：JAKA Zu 3 关节运动干运行模板。它使用内存中的 JAKA 主目标和模拟镜像，不包含控制器地址，不能加载 SDK、连接控制器、上电、使能或下发真实运动。
- `jaka-web-control.example.json`：JAKA 网页控制的干运行模板。它只选择 `jaka-dry-run-robot`、绑定本地回环地址，并将网页控制开关保持为关闭；不包含控制器地址或真机授权。
- `gripper-web-control.example.json`：夹爪网页控制的模拟模板。它包含模拟主目标、保守的开闭位置和控制限位，但默认关闭控制开关；复制到 `localstore/` 后才能显式开启。
- `hikvision-usb.example.json`：海康 USB3 Vision 相机模板。复制到 `localstore/` 后填入相机序列号与真实标定标识；模板默认禁止网页写入相机参数，并以最新帧优先方式预览。
- `pose-preview.example.json`：人体 2D 姿态追踪模板。默认关闭；复制到 `localstore/` 后填写本机相机与模型权重路径，再显式启用。
- `vision-evaluation.example.json`：不创建相机的离线图片评测模板。它只引用 `localstore/` 中的 CUDA 模型权重，可与 `data/vision-fixtures/` 一起验证 RGB 和 Mono8 推理路径。
- `image-centering-simulation.example.json`：静态公开图片的图像居中虚拟仿真模板。它仅包含 CUDA 姿态设置与虚拟图像平面模型，不包含相机、机器人、夹爪、执行目标或真实设备字段。
- `invalid-component.fixture.json`：仅供加载器测试使用的受版本控制的反向测试夹具。

配置文件只包含组件标识符和非敏感运行设置。不得在此放置令牌、密码、私有 IP 地址、标定采集数据、模型权重或可变运行状态；此类内容应存放在 `localstore/`。

`components.plugins.preview` 是网页预览 Plugin 的显式组件列表。版本化的可运行与示例组件图都声明 `"visual-pose-analysis"`，使人体姿态与成像质量分析能够以独立生命周期运行；缺少该列表的旧配置仍保持兼容，不会被隐式补写。当前受信任的网页预览注册表仅包含 `visual-pose-analysis`；配置其他标识会在服务启动时被拒绝。新增模块必须先在服务端以固定工厂和清单完成注册，不能使用 Python 模块路径或浏览器输入动态加载。离线图片评测和图像居中仿真不声明该列表，因为它们不启动网页服务或相机采集。

使用本机 JAKA 配置读取六轴关节角时，从子项目根目录执行：

```powershell
poetry run gripper-ai-controller jaka-joints --config-file localstore/jaka-hardware.local.json --target jaka-primary
```

该命令只登录选定 JAKA 目标、读取 `J1` 至 `J6` 的弧度关节角并登出。它不会启动相机、夹爪、网页服务或完整运行时，也不会上电、使能、去使能或下发运动指令。`get_joint_position()` 返回的是关节空间角度，不是各实体关节在三维空间中的位置；后者需要另行建立该型号的运动学模型。

## JAKA 关节干运行

`jaka-joint-dry-run` 仅加载配置中指定的 `jaka-dry-run-robot` 目标及其关节限制，不会构造相机、夹爪、模拟镜像、完整运行时或真实 `JakaAdapter`。适配器只维护六轴预测状态，并输出逻辑 SDK 调用预览；`sent_to_hardware` 始终为 `false`。

从项目根目录执行绝对关节目标预览：

```powershell
poetry run gripper-ai-controller jaka-joint-dry-run --config-file configs/jaka-joint-dry-run.example.json --target jaka-dry-run-primary --joint-positions-rad "[0.1, 0.0, 0.0, 0.0, 0.0, 0.0]" --mode absolute --speed-rad-per-second 0.5
```

相对关节命令将六个值解释为相对于当前预测状态的增量：

```powershell
poetry run gripper-ai-controller jaka-joint-dry-run --config-file configs/jaka-joint-dry-run.example.json --joint-positions-rad "[0.1, 0.0, 0.0, 0.0, 0.0, 0.0]" --mode relative --speed-rad-per-second 0.5
```

逻辑停止命令不需要视觉结果，且不改变预测关节角：

```powershell
poetry run gripper-ai-controller jaka-joint-dry-run --config-file configs/jaka-joint-dry-run.example.json --stop
```

移动命令必须提供六个有限弧度值、`--mode` 和 `--speed-rad-per-second`。模板默认采用 JAKA Zu 3 的保守软件限制：各轴硬件范围向内收缩 `10°`、最大速度 `0.5 rad/s`、单轴单步最大 `10°`。`robot_adapter_settings` 可提供 `joint_lower_limits_rad`、`joint_upper_limits_rad`、`maximum_joint_speed_rad_per_second` 与 `maximum_joint_step_rad` 进一步收紧限制；它们不能突破物理范围或默认软件边界，速度与单步上限也只能继续收紧。

## 网页预览段

可启动 `gripper-ai-controller web` 的配置需包含可选的 `web` JSON 对象。未填写字段使用下列默认值：

- `bind_host`：`"0.0.0.0"`，预览服务监听地址；
- `port`：`8000`，范围为 `1` 至 `65535`；
- `frontend_dist_dir`：`"src/web/dist"`，由 FastAPI 静态托管的已构建前端目录；必须为相对于启动工作目录的路径，不能使用绝对路径或 `..`；
- `stream_fps`：`10`，范围为 `1` 至 `30`；
- `jpeg_quality`：`80`，范围为 `1` 至 `95`；
- `capture_retry_seconds`：`1.0`，范围为 `0.1` 至 `30`。
- `camera_controls_enabled`：`false`，严格布尔值。设为 `true` 后允许网页写入当前相机适配器公开的固定参数白名单；版本化海康模板必须保持 `false`，真实写入仅可在 `localstore/` 本机配置显式开启。
- `gripper_controls_enabled`：`false`，严格布尔值。设为 `true` 时必须同时提供 `gripper_control` 和一个选中的 `primary` 目标，且 `bind_host` 必须严格为 `"127.0.0.1"`；命令行 `--host` 也不能绕过该限制。
- `jaka_controls_enabled`：`false`，严格布尔值。设为 `true` 时必须同时提供 `jaka_control` 和一个选中的 `primary` JAKA 目标，且 `bind_host` 必须严格为 `"127.0.0.1"`；命令行 `--host` 同样不能绕过该限制。
- `plugin_reload_enabled`：`false`，严格布尔值。只有 `runtime_mode` 为 `"development"`、`bind_host` 严格为 `"127.0.0.1"` 且该开关为 `true` 时，网页才允许重载已配置的预览 Plugin；生产模式始终要求重启整个服务。

未声明 `gripper_control` 或 `jaka_control` 时，网页服务只读取上述设置和 `camera`、`components.vision`、`components.vision_adapter_settings`、根 `camera_parameters`。即使同一配置还声明 `targets`、插件或安全设置，它也不会构建或启动它们。声明人工控制段后，服务只构造该段 `target_name` 指向的一个主设备以提供只读状态；只有相应控制开关启用时才允许人工动作。它不构造完整运行时、规划器或镜像目标。真实序列号、真实标定标识和本机覆盖仍必须置于被 Git 忽略的 `localstore/` 配置文件。

## 网页预览 Plugin

网页预览只读取 `components.plugins.preview` 中已注册的稳定 Plugin 标识，不接受浏览器提供的 Python 模块名。当前注册表只包含 `visual-pose-analysis`；它只消费采集循环发布的帧事件，组合姿态追踪与成像分析缓存，并不导入相机 SDK、不持有相机/夹爪/JAKA 适配器，也不能发起人工控制或运动指令。后续新增 Plugin 必须先在服务端以固定工厂注册，不能仅通过配置或网页请求引入。

当本机开发重载条件全部满足时，可通过网页接口重载一个或多个已配置 Plugin；重载期间会暂停该 Plugin 的帧分发并丢弃等待分析的旧帧，JPEG 缓存与 MJPEG 主画面继续复用原有采集循环。新实例启动失败时保留原实例；生产配置和非回环监听地址必须拒绝重载请求。

## 夹爪网页控制段

启用 `web.gripper_controls_enabled` 时，根对象必须增加 `gripper_control`。支持字段如下：

- `target_name`：必填，必须精确选择 `targets` 中唯一的 `primary` 目标；
- `open_position`、`close_position`：必填整数，范围为 `minimum_position` 至 `maximum_position`，且两者必须不同；它们由现场实际安装方向决定；
- `minimum_position`、`maximum_position`：允许的目标位置范围，默认 `0` 与 `1000`；
- `minimum_force_percent`、`maximum_force_percent`：允许的力百分比范围，默认 `20` 至 `30`；
- `minimum_speed_percent`、`maximum_speed_percent`：模拟夹爪允许的速度范围，默认 `1` 至 `20`。PGI TCP 真机不支持速度命令，页面会禁用该控件；
- `arm_timeout_seconds`：临时控制令牌的无成功操作超时，默认 `60` 秒，范围为 `5` 至 `600`；
- `initialization_timeout_seconds`：网页等待普通初始化完成的上限，默认 `5` 秒，范围为 `1` 至 `30`；
- `idempotency_cache_size`、`idempotency_ttl_seconds`：重复动作的内存幂等缓存上限与保留时间。

真实 PGI TCP 目标将 `gripper_adapter` 设为 `"pgi-tcp-gripper"`，并在该目标的 `gripper_adapter_settings` 中填写真实 `host`、`port`、`device_id`、连接超时和初始化轮询设置。这些设备字段只能存在于 `localstore/` 本机文件，不能放入版本化模板。服务启动只连接并读取状态，不执行初始化或位置命令；普通初始化仍必须由页面解锁、确认和幂等请求显式触发。

## JAKA 网页控制段

启用 `web.jaka_controls_enabled` 时，根对象必须增加 `jaka_control`。支持字段如下：

- `target_name`：必填，必须精确选择 `targets` 中唯一的 `primary` JAKA 目标；
- `arm_timeout_seconds`：临时控制令牌的无成功操作超时，默认 `60` 秒；
- `preview_timeout_seconds`：关节动作预览的有效时长，默认 `10` 秒；过期后必须重新读取状态并创建预览；
- `source_position_tolerance_rad`：预览来源关节角与执行前新遥测允许的最大单轴偏差，默认 `0.01` 弧度；超过该值必须重新预览；
- `idempotency_cache_size`、`idempotency_ttl_seconds`：使能和已确认关节动作的内存幂等缓存上限与保留时间。

`jaka-web-control.example.json` 只选用 `jaka-dry-run-robot`，其 `web.jaka_controls_enabled` 必须保持 `false`，可用于检查配置和只读状态而不会加载 SDK、建立网络连接、上电、使能或发送真实运动。若要演练干运行的解锁、预览和二次确认，先将模板复制到 Git 忽略的 `localstore/`，再仅对该副本显式开启控制。真机配置也必须从该模板复制到 `localstore/`：选中的目标使用 `"jaka-robot"`，其 `robot_adapter_settings` 中的真实 `controller_ip`、`allow_enable`、`allow_manual_motion` 和 `robot_model: "zu3"` 只允许存在于该本机文件。`robot_model` 缺失时适配器仅提供读取，不能发送网页动作。上述字段绝不能写入 `configs/`、文档或提交内容。

即使真机本机配置已声明 JAKA 目标，服务启动和“重新连接”也只执行登录与状态读取，绝不调用 `power_on()`、`enable_robot()`、`joint_move()` 或其他运动接口。网页人工动作必须先确认工作区清空和现场独立急停可用，取得短时令牌后提交一次六轴**绝对**关节目标预览；只有预览未过期、来源关节角仍在容差内并经第二次明确确认后，才允许执行。网页当前不提供软件急停，不能将关闭页面、撤销授权或断开网络当作停止运动的手段；现场独立急停始终是恢复措施。

真实测试前必须由操作者确认实际机械臂型号与 JAKA Zu 3 限位模板相符，并以 `robot_model: "zu3"` 显式确认；同时确认控制器和 Python 3.7 SDK 二进制兼容、工具/负载与安装状态已完成现场安全评估，并确认工作区无人且无障碍物。控制器通信断开、未到位、拖拽、故障或急停时，网页动作会被拒绝；阻塞调用返回后目标关节误差超出来源容差也会报告失败。不得通过网页修改控制器安全参数、碰撞设置、网络异常行为或现场限位。

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
