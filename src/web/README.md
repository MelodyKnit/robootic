# 相机预览前端

本目录是夹爪 AI 控制器的 Vue 3 + TypeScript 前端工程。它显示 FastAPI 相机预览服务提供的状态和 MJPEG 图像流，并在后端本机开关允许时显示受限相机参数控件；不包含机械臂、夹爪或运行时指令调度能力。

## 运行环境

- Node.js 20 或更高版本
- pnpm 10
- 后端相机预览服务已通过项目的 `web` CLI 子命令启动

## 开发

在本目录执行：

```powershell
pnpm install
pnpm dev
```

开发服务器绑定 `0.0.0.0:5173`，并将 `/api` 转发至 `http://127.0.0.1:8000`。如后端使用其他地址，可在启动前设置 `VITE_API_PROXY_TARGET`。构建后的文件由 FastAPI 直接提供，不需要浏览器跨域配置。

```powershell
pnpm build
```

构建产物位于 `dist/`，不参与 Git 提交。

## 后端接口

页面使用以下预览接口：

- `GET /api/cameras` 返回 `{ "cameras": [...] }`；
- `GET /api/cameras/{camera_id}/status` 返回一个相机状态；
- `GET /api/cameras/{camera_id}/stream` 返回 MJPEG 图像流。

相机参数区域使用以下受控接口：

- `GET /api/cameras/{camera_id}/parameters` 获取设备当前支持的参数及范围；
- `PATCH /api/cameras/{camera_id}/parameters/{parameter_key}` 立即应用曝光、增益、帧率等 `live` 参数；
- `POST /api/cameras/{camera_id}/parameters/apply` 保存 `restart` 参数并由后端停止、写入和恢复采集。

相机状态字段为 `camera_id`、`state`、`latest_frame_at` 和 `error`，页面不显示帧序号。`state` 只能为 `starting`、`streaming`、`degraded` 或 `stopped`；`error` 为 `null` 或包含 `code`、`message` 的对象。当前界面只支持一台已配置相机。

浮点参数使用滑动条和数值输入，滑块释放或数值提交后立即请求后端。自动曝光、自动增益和帧率开关会禁用相应的手动控件。像素格式等 `restart` 参数只保存在页面草稿中，直到操作者点击“保存并重新采集”。所有参数范围和枚举选项均来自后端实际设备响应，不在前端写死。

## 浏览器验收

先启动后端预览服务，再执行：

```powershell
pnpm exec playwright install chromium
pnpm test:e2e
```

默认测试访问 `http://127.0.0.1:8000`。可通过 `CAMERA_PREVIEW_BASE_URL` 覆盖测试地址。验收截图保存到已忽略的项目根 `temp/gripper-ai-controller/`；失败诊断产物保存在本目录已忽略的 `playwright-report/` 与 `test-results/`。

局域网部署不提供认证；仅可在受信任网络中使用。`camera_controls_enabled` 默认关闭；开启后，同一受信任网络中的访问者可修改后端白名单内的相机参数，不能直接暴露到互联网。网页永远不提供机器人或夹爪控制。
