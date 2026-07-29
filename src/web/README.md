# 相机预览前端

本目录是 Vite、Vue 3 和 TypeScript 构建的相机预览与人工设备控制界面。主画面始终显示连续 MJPEG，并在服务器确认姿态来源帧仍足够新鲜时，用 Canvas 叠加服务器已计算的人体 2D 骨架和成像分析；它不会为了姿态结果替换为静态 JPEG。它不会直接导入海康、JAKA 或 PGI SDK，所有设备访问都经过 FastAPI 的受限接口。

## 结构

- `src/api/camera.ts`：相机、参数、姿态和成像分析 HTTP 契约校验。
- `src/composables/useCameraPreview.ts`：物理相机目录、选择状态、逻辑相机状态轮询与 MJPEG 重连。
- `src/components/CameraSelector.vue`：在相机 Adapter 面板中刷新并选择后端发现的采集设备。
- `src/composables/usePoseTracking.ts`：姿态快照轮询与目标关节选择。
- `src/composables/useVisionAnalysis.ts`：只读成像质量与人员识别缓存轮询。
- `src/components/PoseSkeletonOverlay.vue`：按照图片实际显示尺寸绘制人体框、骨架和锁定关节的 Canvas 覆盖层。
- `src/components/PoseTargetPanel.vue`：COCO 关节选择和锁定状态面板。
- `src/components/VisionAnalysisPanel.vue`：帧质量、检测人数、主人体框和 17 关节可见性面板。
- `src/api/gripper.ts`：夹爪状态、授权、初始化、动作和错误响应的 HTTP 契约校验。
- `src/composables/useGripperControl.ts`：浏览器内存中的临时令牌、状态轮询和单操作状态管理。
- `src/components/GripperControlPanel.vue`：夹爪状态、解锁确认、初始化确认、位置/力/速度草稿和明确执行按钮。
- `src/api/robot.ts`：JAKA 状态、临时授权、伺服使能、关节动作预览和二次提交的 HTTP 契约校验。
- `src/composables/useJakaControl.ts`：JAKA 浏览器令牌、受限状态轮询和单条阻塞关节动作的本地状态管理。
- `src/components/RobotControlPanel.vue`：J1 至 J6 只读角度、绝对目标草稿、伺服使能和两阶段关节运动确认界面。

骨架坐标由后端以相对于原始帧宽高的归一化数值提供。Canvas 会按连续 MJPEG 在 `object-contain` 盒内实际可见的像素矩形绘制，因此黑边、窗口缩放或相机画面比例变化都不会把骨架投射到图像外。只有 `/pose` 的 `overlay_fresh` 为真时才绘制，过期时隐藏叠加但视频不中断。`/pose/frame` 是后端诊断接口，不参与主画面切换。

相机 Adapter 面板调用 `GET /api/cameras` 获取物理设备目录，选择时调用 `PUT /api/cameras/{camera_id}/selection`。页面始终保留同一个逻辑相机和 MJPEG 元素；切换成功只递增数据源修订号，用于清空参数、骨架和分析面板的旧状态，不会主动重建视频地址。发现失败与切换失败分别显示，当前可用画面不因列表刷新而中断。选择开关由本机后端配置决定，前端不能自行开启。

## 开发与构建

```powershell
pnpm --dir src/web install
pnpm --dir src/web typecheck
pnpm --dir src/web build
pnpm --dir src/web dev
pnpm --dir src/web test:e2e
```

开发服务器和 `vite preview` 均只绑定 `127.0.0.1`，再通过 Vite 代理访问本机 FastAPI 的 `/api`。这是为了避免开发代理将回环限定的设备控制接口重新暴露到局域网。构建后的 `dist/` 由 FastAPI 静态托管，不参与 Git 提交。

`test:e2e` 默认会在回环地址启动临时 Vite 服务，并为全部 `/api` 请求提供浏览器侧模拟响应；它不会连接 `8000` 预览服务、相机、夹爪或机械臂。仅在人工诊断时才可显式设置 `CAMERA_PREVIEW_BASE_URL` 指向已有服务。

## 姿态交互

页面只轮询 `GET /api/cameras/{camera_id}/pose` 的最新结果；它不会使相机重新取帧或触发新的 GPU 推理。用户切换下拉框时，页面调用 `PUT /api/cameras/{camera_id}/pose/target`。后端会先校验 COCO 关节名，再原子保存到显式本机配置，最后更新内存中的追踪状态。

当新鲜帧中存在人体姿态时，页面绘制人体框和可用关节骨架；即使所选锁定关节暂时低置信度，其他已检测关节仍会显示。`valid` 仅表示所选关节是否满足锁定和后续跟随资格，并控制目标高亮，不是整个人体骨架的显示条件。姿态功能未启用、未检测到人体或结果过期时，页面保留相机画面但不绘制旧骨架。该界面仅用于视觉验证，不代表可驱动机械臂的坐标结果。

## 成像与识别面板

页面轮询 `GET /api/cameras/{camera_id}/vision/analysis` 的服务器缓存。它显示图像格式、尺寸、亮度、对比度、清晰度、人数、主人体框和 COCO 17 关节状态；Canvas 使用与 MJPEG 相同的归一化坐标，并受 `/pose` 的新鲜度校验保护，因此缩放时保持对齐且不会绘制过期骨架。

该轮询不打开第二条视频流、不修改相机参数，也不请求新的模型推理。姿态禁用时仍可显示帧健康；未检测到人、低置信度、画面外和相机不可用均由状态面板直接区分。

## 夹爪交互

右侧“夹爪控制”标签页先读取 `GET /api/grippers`。控制开关未启用时，页面只显示未配置状态，不能发送任何动作。启用后，操作者必须在弹窗中确认工作区已清空且现场独立急停可用，才会得到短时令牌；令牌只在浏览器内存中保存，刷新页面不会恢复。

`GET /api/grippers` 成功返回空列表表示当前启动配置没有声明可人工控制的夹爪，页面会显示“未配置夹爪”。若该接口返回 `404`，则当前预览服务尚未加载夹爪控制路由，页面会明确提示重启服务；这与 TCP 设备连接失败不同。网页不会根据 Wi-Fi 名称、网关或厂商示例地址猜测 PGI TCP 端点，真机仍必须在本机私有配置中显式声明协议转换器地址、端口和设备 ID。

位置、力和模拟速度的滑块及数值输入都只是草稿，只有“打开夹爪”“关闭夹爪”或“执行目标位置”才会发送请求。初始化也必须经过独立确认。真机 PGI TCP 模式不会显示可用的速度或软件停止能力；“立即锁定”只撤销后续网页权限，不能替代现场独立急停。页面不会自动重试动作，避免因网络不确定性制造重复物理命令。

## JAKA 六轴交互

右侧“机械臂控制”标签在打开时读取 `GET /api/robots`，默认仅展示 J1 至 J6 的实时弧度与换算角度、供电/伺服状态和读取时间。它不会在加载、刷新、重连或切换标签时执行上电、使能或关节运动；切换离开该标签会卸载组件并撤销浏览器临时令牌。

只有本机配置同时允许网页控制和人工关节运动时，操作者才能先确认工作区清空和现场独立急停可用，取得短时令牌。伺服使能仍需独立确认，并且网页不会尝试自动上电。J1 至 J6 输入使用度作为显示与编辑单位，前端转换为受后端校验的弧度数组；编辑草稿不会发送请求。点击“生成动作预览”只让后端校验并保存单条绝对关节动作，随后必须在预览弹窗中再次确认，才会以临时令牌和 `Idempotency-Key` 提交该预览。

版本化 `configs/jaka-web-control.example.json` 保持 `jaka-dry-run-robot` 和 `web.jaka_controls_enabled: false`，只适合检查页面与只读状态。演练干运行操作应复制配置到 Git 忽略的 `localstore/` 后显式开启网页控制；真实 `controller_ip`、`allow_enable`、`allow_manual_motion` 和 `robot_model: "zu3"` 也只能保存在该本机文件。缺少机型确认时页面只显示状态，不能生成或确认动作预览。页面加载、刷新、标签切换和“重新连接”只读取状态或重建会话，不会自动上电、使能或发送关节动作。

首版不提供相对运动、直线运动、jog、servo 或网页软件急停。`joint_move` 为阻塞操作，前端执行期间暂停新的状态轮询，完成后再刷新状态。控制器报告未到位、拖拽、故障、急停或通信断开时，页面不提供解锁、使能或预览；收到运动中、故障或急停状态时，浏览器还会立即清除本地令牌和待确认预览，状态恢复后必须重新解锁。预览到期也不能打开二次确认。网页“立即锁定”只能阻止后续请求，机械臂运动期间必须使用现场独立急停。
