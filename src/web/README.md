# 相机预览前端

本目录是 Vite、Vue 3 和 TypeScript 构建的相机预览界面。主画面始终显示连续 MJPEG，并在服务器确认姿态来源帧仍足够新鲜时，用 Canvas 叠加服务器已计算的人体 2D 骨架和成像分析；它不会为了姿态结果替换为静态 JPEG。它不会直接导入海康 SDK，也没有机器人、夹爪或运动控制接口。

## 结构

- `src/api/camera.ts`：相机、参数、姿态和成像分析 HTTP 契约校验。
- `src/composables/useCameraPreview.ts`：相机状态轮询与 MJPEG 重连。
- `src/composables/usePoseTracking.ts`：姿态快照轮询与目标关节选择。
- `src/composables/useVisionAnalysis.ts`：只读成像质量与人员识别缓存轮询。
- `src/components/PoseSkeletonOverlay.vue`：按照图片实际显示尺寸绘制人体框、骨架和锁定关节的 Canvas 覆盖层。
- `src/components/PoseTargetPanel.vue`：COCO 关节选择和锁定状态面板。
- `src/components/VisionAnalysisPanel.vue`：帧质量、检测人数、主人体框和 17 关节可见性面板。

骨架坐标由后端以相对于原始帧宽高的归一化数值提供。Canvas 会按连续 MJPEG 在 `object-contain` 盒内实际可见的像素矩形绘制，因此黑边、窗口缩放或相机画面比例变化都不会把骨架投射到图像外。只有 `/pose` 的 `overlay_fresh` 为真时才绘制，过期时隐藏叠加但视频不中断。`/pose/frame` 是后端诊断接口，不参与主画面切换。

## 开发与构建

```powershell
pnpm --dir src/web install
pnpm --dir src/web typecheck
pnpm --dir src/web build
pnpm --dir src/web dev
```

开发服务器通过 Vite 代理访问本机 FastAPI 的 `/api`。构建后的 `dist/` 由 FastAPI 静态托管，不参与 Git 提交。

## 姿态交互

页面只轮询 `GET /api/cameras/{camera_id}/pose` 的最新结果；它不会使相机重新取帧或触发新的 GPU 推理。用户切换下拉框时，页面调用 `PUT /api/cameras/{camera_id}/pose/target`。后端会先校验 COCO 关节名，再原子保存到显式本机配置，最后更新内存中的追踪状态。

当新鲜帧中存在人体姿态时，页面绘制人体框和可用关节骨架；即使所选锁定关节暂时低置信度，其他已检测关节仍会显示。`valid` 仅表示所选关节是否满足锁定和后续跟随资格，并控制目标高亮，不是整个人体骨架的显示条件。姿态功能未启用、未检测到人体或结果过期时，页面保留相机画面但不绘制旧骨架。该界面仅用于视觉验证，不代表可驱动机械臂的坐标结果。

## 成像与识别面板

页面轮询 `GET /api/cameras/{camera_id}/vision/analysis` 的服务器缓存。它显示图像格式、尺寸、亮度、对比度、清晰度、人数、主人体框和 COCO 17 关节状态；Canvas 使用与 MJPEG 相同的归一化坐标，并受 `/pose` 的新鲜度校验保护，因此缩放时保持对齐且不会绘制过期骨架。

该轮询不打开第二条视频流、不修改相机参数，也不请求新的模型推理。姿态禁用时仍可显示帧健康；未检测到人、低置信度、画面外和相机不可用均由状态面板直接区分。
