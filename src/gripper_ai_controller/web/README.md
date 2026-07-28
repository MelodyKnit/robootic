# 相机网页预览后端

本包提供独立的 FastAPI 相机预览、受限相机参数和可选人工夹爪、JAKA 六轴控制服务。默认启动配置的 `VisionAdapter` 和网页预览 `PluginHost`，以单一采集循环获取帧、在内存中编码为 JPEG，并通过快照和 MJPEG 接口提供给浏览器。`visual-pose-analysis` Plugin 只消费 JPEG 发布后的帧事件，维护姿态与成像分析快照；采集、JPEG 编码、CUDA 姿态推理和质量诊断各自使用受限的单工作线程，每条慢路径最多保留一个正在处理项和一个可替换的最新待处理项。

服务不会创建 `Runtime`、规划器、镜像目标或视觉运动链路，也不会将帧写入磁盘。默认配置不构造机器人或夹爪适配器；声明 `gripper_control` 或 `jaka_control` 后，才会分别构造其 `target_name` 选中的一个主设备及对应人工控制门面，以提供只读状态。只有本机配置明确开启对应网页控制开关后，人工门面才允许动作；它们不会启动自动规划、视觉跟随或运行时调度。

## 接口

- `GET /api/cameras`：配置的相机列表及预览状态。
- `GET /api/cameras/{camera_id}/status`：相机状态，字段为 `camera_id`、`state`、`latest_frame_at` 和 `error`；不提供帧序号。
- `GET /api/cameras/{camera_id}/frame`：最新 JPEG 快照；首帧未准备好时返回 `503`。
- `GET /api/cameras/{camera_id}/stream`：MJPEG 连续流；所有浏览器复用同一采集循环和最新 JPEG 缓存。
- `GET /api/cameras/{camera_id}/parameters`：读取当前设备实际支持的固定参数白名单、取值范围、选项及应用方式。
- `PATCH /api/cameras/{camera_id}/parameters/{parameter_key}`：立即应用一个 `live` 参数，请求体为 `{ "value": ... }`。设备成功后，实际生效值会写回显式配置的 `camera_parameters`。
- `POST /api/cameras/{camera_id}/parameters/apply`：提交一个或多个 `restart` 参数，请求体为 `{ "values": { "pixel_format": "Mono8" } }`。后端会停止取流、写入、更新配置并恢复取流。
- `GET /api/cameras/{camera_id}/pose`：返回已缓存的单人 2D 姿态、当前锁定关节、相邻有效帧的图像运动状态、`latest_frame_at` 和 `overlay_fresh`；不会触发新的相机采集或模型推理。
- `GET /api/cameras/{camera_id}/pose/frame?captured_at={timestamp}`：返回该姿态结果对应的 JPEG 源图；`captured_at` 为 `/pose` 的浮点采集时间，成功时返回 `image/jpeg`、`Cache-Control: no-store` 和 `X-Camera-Captured-At`。它只读取最多三个已送入推理的内存 JPEG，不触发采集或推理；缓存尚未可用、已被淘汰或姿态未启用时返回 `503`、`pose_frame_unavailable`。
- `PUT /api/cameras/{camera_id}/pose/target`：请求体为 `{ "target_joint": "right_wrist" }`，更新 COCO 关节选择并原子保存到显式本机配置。
- `GET /api/cameras/{camera_id}/vision/analysis`：返回已缓存的成像质量、Keypoint R-CNN 已产生的人员框、主人体框、17 个关节可见性和失败原因；不会触发新的采集或推理。
- `GET /api/plugins`：返回配置的网页预览 Plugin 清单、能力、生命周期状态、最近错误和重载可用性。
- `GET /api/plugins/{plugin_id}/status`：返回一个已配置网页 Plugin 的详细状态；未知标识返回 `404`。
- `POST /api/plugins/reload`：请求体为 `{ "plugin_ids": ["visual-pose-analysis"] }`；空列表表示重载全部已配置网页 Plugin。仅本机回环开发服务、且 `web.plugin_reload_enabled` 为真时可用；未授权为 `403`，重载进行中为 `409`，替换失败为 `503`。
- `GET /api/grippers`：返回零或一个可人工控制夹爪的状态；控制未启用或未配置时列表为空。
- `GET /api/grippers/{gripper_id}/status`：读取当前连接、初始化、运动、夹持、`target_position`、该值是否为实时反馈以及最近错误。
- `POST /api/grippers/{gripper_id}/reconnect`：只重新连接和读取状态，立即撤销旧控制令牌，不初始化或运动。
- `POST /api/grippers/{gripper_id}/arm`：请求体为 `{ "work_area_clear": true, "emergency_stop_ready": true }`，确认后返回临时令牌。
- `DELETE /api/grippers/{gripper_id}/arm`：携带 `X-Gripper-Control-Token` 撤销当前令牌，不发送设备动作。
- `POST /api/grippers/{gripper_id}/initialization`：携带 `X-Gripper-Control-Token` 和 `Idempotency-Key`，执行普通初始化。
- `POST /api/grippers/{gripper_id}/commands`：同样携带两个请求头；动作仅为 `open`、`close`、`move_to_position` 或模拟夹爪的 `stop`。
- `GET /api/robots`：返回零或一个可人工控制的 JAKA 主目标状态；未声明 JAKA 控制段时列表为空。
- `GET /api/robots/{robot_id}/status`：读取连接、供电、伺服、故障、急停、六轴关节角和本机限制；不会使能或运动。
- `POST /api/robots/{robot_id}/reconnect`：重新建立 JAKA SDK 会话并读取状态，撤销旧令牌和预览；不会上电、使能或运动。
- `POST /api/robots/{robot_id}/arm`：请求体为 `{ "work_area_clear": true, "emergency_stop_ready": true }`，确认后返回短时 `X-Robot-Control-Token`。
- `DELETE /api/robots/{robot_id}/arm`：携带 `X-Robot-Control-Token` 撤销网页授权和未执行预览，不向控制器发送动作。
- `POST /api/robots/{robot_id}/enable`：携带 `X-Robot-Control-Token` 和 `Idempotency-Key`；只可使能已人工上电、无故障且本机允许使能的控制器，绝不会调用 `power_on()`。
- `POST /api/robots/{robot_id}/joint-moves/preview`：携带 `X-Robot-Control-Token`，请求体为六轴绝对关节目标 `joint_positions_rad` 与 `speed_rad_per_second`；只生成服务器端临时预览，不发送动作。
- `POST /api/robots/{robot_id}/commands`：携带 `X-Robot-Control-Token` 和 `Idempotency-Key`，请求体为 `{ "preview_id": "..." }`；服务重新读取遥测并确认预览未过期、来源关节角仍在容差内后，才会执行该预览。

参数列表响应包含 `camera_id`、`write_enabled` 和 `parameters`。每个参数带有 `key`、`kind`、`apply_mode`、`value`、可选的数值范围和单位，以及枚举选项。更新响应额外带有 `restarted_acquisition`。未知相机返回 `404`；写入开关未启用时返回 `403`；适配器未提供控制能力时返回 `409`；格式、范围或应用方式错误返回 `422`。设备成功应用参数但无法写回配置时返回 `503`，错误 `code` 为 `camera_parameter_persistence_failed`，含义是设备已生效而配置未保存。所有 JSON 错误均为 `code` 和 `message`。

夹爪接口也使用统一的 `{ "code", "message" }` 错误格式：未启用或未解锁为 `403`，动作或能力状态冲突为 `409`，请求或参数错误为 `422`，TCP 或状态读取故障为 `503`。写入已送出但确认响应丢失时，接口以 `503` 和 `gripper_operation_outcome_unknown` 返回；服务会撤销临时令牌，客户端不得以新幂等键自动重发，必须先重新连接并检查设备。初始化和命令的成功响应包含 `idempotency_key`、`replayed` 和 `status`；相同幂等键加相同操作只返回原结果，不会重复下发动作。控制令牌只保留在浏览器内存中，60 秒没有成功初始化或动作后失效；断连、重新连接、初始化失败和服务关闭也会撤销它。

JAKA 接口同样使用 `{ "code", "message" }` 错误格式：控制未启用或令牌缺失、失效时为 `403`；设备状态、能力或幂等键冲突时为 `409`；关节数量、有限值、速度、限位或过期预览错误为 `422`；SDK 登录、遥测或控制器连接故障为 `503`。使能和已确认动作的成功响应都包含 `idempotency_key`、`replayed` 和最新 `status`，重复同一幂等键且操作一致时只返回首次结果，不会重复下发动作。

姿态响应包含 `enabled`、`valid`、`reason`、`target_joint`、可选 `target`、可选 `person`、`inference_latency_ms`、`lost_frames`、可选 `motion`、`draw_skeleton`、`latest_frame_at` 和 `overlay_fresh`。`motion` 只描述连续两个已关联有效帧中锁定关节的归一化图像位移与速度，并在目标丢失、人体关联失败、目标切换、时间戳倒退或采集间隔超限后为 `null`；它不含真实距离、三维速度或控制命令。`valid` 只表示所选目标关节是否满足锁定资格；只要 `person` 存在且其来源帧不晚于最新 JPEG、两者时间差不超过 `pose.overlay_max_frame_lag_seconds`，`overlay_fresh` 就为 `true`，浏览器即可绘制人体框和其余骨架。过期时只隐藏叠加，视频流继续播放。网页主画面始终保持 `GET /stream` 的 MJPEG；`/pose/frame` 是独立诊断接口，不参与主画面切换。`person` 内是归一化坐标及原始像素坐标的 COCO 关节点，不含图像载荷、模型张量或任何可执行控制命令。姿态未启用时 `GET /pose` 仍返回 `200` 和 `enabled: false`，而更新目标返回 `409`；无效关节返回 `422`。详情见 [姿态感知包说明](../pose/README.md)。

成像分析响应包含 `frame`、`person_count`、`persons`、`selected_person`、`joint_visibility` 和 `visible_joint_names`。`frame` 只含尺寸、像素格式、亮度均值、对比度、拉普拉斯方差清晰度和非阻断警告；`persons` 直接复用最近一次 Keypoint R-CNN 推理中通过人体阈值的候选，不会加载第二个人体检测模型。姿态未启用时接口仍返回 `200`，并提供帧质量但 `pose_enabled: false`；未知相机继续返回 `404`。详情见 [视觉分析包说明](../vision/README.md)。

## 网页预览 Plugin 边界

网页预览只从 `components.plugins.preview` 加载已注册的稳定组件标识符，浏览器请求不能提供模块路径或工厂名称。当前受信任注册表只包含 `visual-pose-analysis`；新增 Plugin 必须先在服务端注册固定工厂，不能仅改写配置。Plugin 列表接口返回稳定 ID、名称、版本、能力、`ui_kind`、生命周期状态、最近错误和重载能力；已注册但尚无专用面板的模块只显示状态卡，绝不生成动态设备控制表单。

`visual-pose-analysis` 只处理帧事件并公开只读姿态、人员检测与成像分析结果。它不导入相机 SDK、不持有相机、夹爪或 JAKA 适配器，也不能调用人工控制服务。Plugin 重载会暂停该模块的新帧投递，等待其工作完成后原子替换；若新实例启动失败，旧实例继续运行。整个过程中 JPEG 缓存、MJPEG URL 和相机采集循环保持不变。

重载仅在 `runtime_mode` 为 `development`、`web.bind_host` 严格为 `127.0.0.1` 且 `web.plugin_reload_enabled` 为 `true` 时开放。生产模式必须停止并重启服务以加载 Plugin 更新；任何非回环地址均拒绝网页重载，避免无认证网络服务装载更新。

## 夹爪控制边界

人工控制由 `ManualGripperControlService` 统一执行状态读取、临时授权、幂等、互斥和 `SafetyPolicy` 检查；HTTP 路由不会直接访问 TCP 客户端。一个运行时只允许选择一个 `primary` 目标夹爪。命令执行期间，新的独立命令会收到冲突响应；重复的相同幂等键只复用第一次结果。浏览器请求中断不会取消已经开始的底层操作，设备锁会持续到适配器调用返回。

控制开关为 `false` 时，网页只读取已声明夹爪的状态，不能解锁、重新连接、初始化或发送动作。PGI 适配器在服务启动时只连接并读取初始化、已设定目标位置和夹持状态；不会自动初始化、写入力/位置、写入速度或发送 `0xA5` 全行程重新标定。当前 TCP 映射尚未验证实时位置反馈，因此真机页面会明确标记该位置不是实时反馈。真机仅允许位置和力，速度与软件停止在 UI 和服务端均不可用。网页“立即锁定”不是物理急停，运动中的真机必须使用现场独立急停。

为了避免无认证 Web 服务暴露真机控制，启用 `web.gripper_controls_enabled` 时 `web.bind_host` 必须为严格的 `127.0.0.1`；CLI 覆盖后的地址也会再次校验。真实端点、设备 ID 和开闭位置只能位于 Git 忽略的 `localstore/` 配置。

## JAKA 六轴控制边界

`ManualJakaControlService` 是网页层唯一的 JAKA 人工控制门面。HTTP 路由不会直接访问 SDK 客户端；普通 `Runtime`、规划器和 `JakaAdapter.execute()` 仍不能发送真实运动。门面只接受选中 `primary` 目标的 `JakaAdapter` 或 `JakaDryRunRobotAdapter`，并在临时令牌、单操作互斥、幂等、当前遥测、JAKA 关节限制和两阶段确认全部通过后，才调用窄化的人工动作入口。

启用 `web.jaka_controls_enabled` 时，`web.bind_host` 必须严格为 `127.0.0.1`，命令行覆盖也不能放宽。真实 `controller_ip`、`allow_enable`、`allow_manual_motion` 与 `robot_model: "zu3"` 只能位于 Git 忽略的 `localstore/` 本机配置；省略机型确认时网页保持只读。版本化 `configs/` 只提供禁用控制的干运行模板。服务启动和“重新连接”只登录并读取状态，绝不会自动调用 `power_on()`、`enable_robot()` 或 `joint_move()`。

每次人工动作只支持一条六轴**绝对**关节目标和受限速度。操作者先确认工作区清空且现场独立急停可用，取得短时令牌；再生成预览，并在预览未过期、实时关节角未超出来源容差时进行第二次明确确认。发送前适配器会再次核对控制器通信、到位、拖拽、故障、急停和来源关节角，调用返回后还会核对到位和目标关节误差。浏览器请求取消不会释放底层 SDK 调用锁，直到阻塞调用实际返回。只要遥测出现断连、故障、急停、运动中，或已初始化/上电/使能状态发生反向变化，服务立即撤销令牌和未执行预览；状态恢复后必须重新确认工作区和急停。相对运动、直线运动、jog、servo 和网页软件急停均不提供。撤销令牌、关闭网页、断开网络或停止服务只会阻止后续网页请求，不能停止已开始的真机动作；发生异常时必须使用现场独立急停。

真实测试前，操作者必须确认实际型号与当前 JAKA Zu 3 限位模板一致，并完成控制器/SDK 兼容性、工具与负载、安装姿态、工作区和障碍物的现场安全检查。网页不得修改碰撞等级、控制器安全配置、网络异常行为或现场限位。

## 参数安全边界

`CameraParameterAdapter` 是独立于 `VisionAdapter` 取帧职责的可选能力端口。网页服务仅通过该端口访问参数，不持有厂商 SDK 客户端，也不会接受任意节点名称。

海康首版白名单为自动曝光、曝光时间、自动增益、增益、帧率开关、帧率和像素格式。实际数值范围与枚举选项均从已连接设备读取；当前设备不支持或不可读的节点不会出现在列表中。自动曝光或自动增益开启时，后端与页面都会拒绝对应手动参数；帧率设置要求帧率控制已启用。像素格式被标记为 `restart`，只能通过明确的保存操作应用。

同一异步操作锁覆盖取帧、节点读取、节点写入、停止取流、恢复取流和关闭相机，避免 MVS 句柄同时被原生取帧和重配置访问。设备成功写入后，服务将适配器确认的本次实际值及其必要前置开关原子写回启动 CLI 显式传入 JSON 的根 `camera_parameters`；适配器启动和每次断连重连后，会在首帧前恢复该对象。恢复失败会使预览进入可重试的降级状态，而非静默使用默认参数。此配置持久化绝不调用 `FeatureSave`、`UserSetSave` 或其他厂商设备持久化命令。

`web.camera_controls_enabled` 同时约束浏览器写入和自动恢复。将它设为 `false` 后，服务仍可只读预览，但即使 JSON 中保留旧的 `camera_parameters`，启动或重连也不会向设备写入参数。

浏览器刷新仅重新读取状态、参数能力和视频流，不会重置设备参数或改写 `camera_parameters`。

`web.camera_controls_enabled` 默认为 `false`。应只在被 Git 忽略的 `localstore/` 实机配置中显式设为 `true`，并将该文件作为 `--config-file` 传入。受版本控制的 `configs/` 仅为模板；若用模板直接启动，成功参数写入会修改该模板并使 Git 工作区产生变更。服务默认可按配置绑定 `0.0.0.0`，且没有认证、TLS、来源限制或访问审计；启用写入后，受信任局域网中的访问者能够更改白名单内相机参数，不能暴露到互联网。

## 图像契约

`ImageFrame` 必须提供 `pixel_payload`、`width`、`height` 与 `pixel_format`。网页编码器接受 `rgb8` 或 `mono8`，不保存原始数据。海康 MVS 客户端直接保留 `Mono8` 为 `mono8`、直接复制 `RGB8` 为 `rgb8`，并在 MVS 缓冲区释放前将其他受支持的彩色格式转换为 `rgb8`；仿真相机提供内存中的确定性 RGB 和 Mono8 测试图。

相机在启动或取帧失败时，服务保留最近一次成功帧并公开 `degraded` 状态，然后按 `capture_retry_seconds` 关闭并重新打开适配器。JPEG 编码失败只标记当前预览降级，后续有效帧仍可继续发布，不会把缓慢编码累积为历史帧队列。服务不将帧、错误或设备状态写入 `data/`、`localstore/` 或日志文件。

## 启动

先构建前端，再以显式配置启动：

```powershell
pnpm --dir src/web build
poetry run gripper-ai-controller web --config-file configs/development.json
```

CLI 可用 `--host`、`--port` 和 `--frontend-dist-dir` 临时覆盖对应 `web` 配置；端口必须为 `1` 至 `65535`。未构建前端目录时，服务仍提供 `/api`，但不会提供根网页入口。

要验收模拟夹爪控制，将 `configs/gripper-web-control.example.json` 复制到 `localstore/`，把 `web.gripper_controls_enabled` 改为 `true` 后，以该文件启动。该开关启用时，`--host` 只能是 `127.0.0.1`。真实 PGI 配置必须单独放在 `localstore/`，并在工作区清空、现场独立急停可用的前提下由操作者手动验证。

要演练 JAKA 干运行流程，将 `configs/jaka-web-control.example.json` 复制到 `localstore/`，保持目标为 `jaka-dry-run-robot`，再将 `web.jaka_controls_enabled` 改为 `true` 后启动。该演练仍不导入 SDK、不建立 JAKA 网络连接，也不会改变真机状态。真实 JAKA 配置必须另存于 `localstore/`，并在现场完成型号、工作区和独立急停检查后，由操作者显式决定是否开启相应权限。

使用真实海康相机时，从版本化模板复制本机配置到 `localstore/`，并确保官方 MVS SDK 已按项目文档复制到本机适配器目录。版本化配置、代码和接口文档不得包含真实相机序列号或采集帧。

启用 `pose.enabled` 时，服务还会在创建相机采集循环前检查 NVIDIA 驱动、CUDA 11.7 Torch 构建和 `torch.cuda.is_available()`。检查失败会阻止服务启动，避免在无 GPU 的机器上无声回退到 CPU。模型权重必须由操作员使用显式 CLI 下载到 `localstore/`；服务本身不会下载权重。
