# 相机预览前端

本目录是夹爪 AI 控制器的 Vue 3 + TypeScript 前端工程。它只显示 FastAPI 相机预览服务提供的状态和 MJPEG 图像流，不包含机械臂、夹爪或运行时指令调度能力。

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

页面使用以下只读接口：

- `GET /api/cameras` 返回 `{ "cameras": [...] }`；
- `GET /api/cameras/{camera_id}/status` 返回一个相机状态；
- `GET /api/cameras/{camera_id}/stream` 返回 MJPEG 图像流。

相机状态字段为 `camera_id`、`state`、`latest_frame_at`、`frame_sequence` 和 `error`。`state` 只能为 `starting`、`streaming`、`degraded` 或 `stopped`；`error` 为 `null` 或包含 `code`、`message` 的对象。当前界面只支持一台已配置相机。

## 浏览器验收

先启动后端预览服务，再执行：

```powershell
pnpm exec playwright install chromium
pnpm test:e2e
```

默认测试访问 `http://127.0.0.1:8000`。可通过 `CAMERA_PREVIEW_BASE_URL` 覆盖测试地址。验收截图保存到已忽略的项目根 `temp/gripper-ai-controller/`；失败诊断产物保存在本目录已忽略的 `playwright-report/` 与 `test-results/`。

局域网部署不提供认证；仅可在受信任网络中使用。
