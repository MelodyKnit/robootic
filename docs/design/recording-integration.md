# 录制截图功能集成指南

本文档说明如何集成新增的录制和截图功能，以及工业风格UI优化建议。

---

## 已完成的后端工作

### 1. 录制服务类
**文件**: `src/gripper_ai_controller/web/recording_service.py`

提供的功能：
- `save_snapshot()` - 保存单帧截图
- `start_recording()` - 开始录制视频
- `stop_recording()` - 停止录制
- `add_frame()` - 添加帧到录制
- `list_recordings()` - 列出录制文件
- `list_snapshots()` - 列出截图文件
- `delete_recording()` / `delete_snapshot()` - 删除文件

### 2. API 路由
**文件**: `src/gripper_ai_controller/web/app.py`

新增的端点：
- `POST /api/cameras/{camera_id}/snapshot` - 保存截图
- `POST /api/cameras/{camera_id}/recording/start` - 开始录制
- `POST /api/cameras/{camera_id}/recording/stop` - 停止录制
- `GET /api/cameras/{camera_id}/recording/status` - 获取录制状态
- `GET /api/recordings` - 列出所有录制
- `GET /api/snapshots` - 列出所有截图
- `DELETE /api/recordings/{recording_id}` - 删除录制
- `DELETE /api/snapshots/{filename}` - 删除截图

### 3. 配置支持
**文件**: `src/gripper_ai_controller/configuration.py`, `src/gripper_ai_controller/bootstrap/preview_builder.py`

新增配置项：
```json
{
  "web": {
    "recording_enabled": true,
    "recording_output_dir": "localstore/recordings",
    "recording_default_fps": 30
  }
}
```

---

## 前端组件

### RecordingControls.vue
**文件**: `src/web/src/components/RecordingControls.vue`

已创建的工业风格录制控制面板，包含：
- 截图按钮（蓝色高亮）
- 开始/停止录制按钮（红色/橙色）
- 录制状态实时显示（时长、帧数）
- 录制指示灯动画
- 成功/错误消息提示

---

## 集成步骤

### 步骤 1: 在 CameraPreview.vue 中导入组件

在 `src/web/src/components/CameraPreview.vue` 的 `<script setup>` 部分添加：

```typescript
import RecordingControls from './RecordingControls.vue'
```

### 步骤 2: 在模板中添加组件

在相机控制面板区域添加录制控制：

```vue
<template>
  <div class="camera-preview">
    <!-- 现有的相机预览区域 -->
    <div class="preview-area">
      <!-- ... -->
    </div>

    <!-- 右侧控制面板 -->
    <div class="control-panel">
      <!-- 相机适配器控制 -->
      <CameraAdapterPanel v-if="camera" :camera="camera" />

      <!-- ✨ 新增：录制控制 -->
      <RecordingControls
        v-if="camera"
        :camera-id="camera.cameraId"
        @snapshot="onSnapshot"
        @recording-started="onRecordingStarted"
        @recording-stopped="onRecordingStopped"
      />

      <!-- 其他控制面板 -->
      <RobotControlPanel v-if="..." />
      <GripperControlPanel v-if="..." />
    </div>
  </div>
</template>

<script setup lang="ts">
// ... 现有代码 ...

function onSnapshot() {
  console.log('Snapshot saved')
  // 可选：显示通知或刷新截图列表
}

function onRecordingStarted() {
  console.log('Recording started')
  // 可选：禁用某些控制或显示提示
}

function onRecordingStopped(result: any) {
  console.log('Recording stopped:', result)
  // 可选：显示录制结果或刷新录制列表
}
</script>
```

### 步骤 3: 配置文件更新

更新你的配置文件（如 `localstore/hikvision-object-detection.local.json`）：

```json
{
  "web": {
    "bind_host": "127.0.0.1",
    "port": 8000,
    "camera_controls_enabled": false,
    "recording_enabled": true,
    "recording_output_dir": "localstore/recordings",
    "recording_default_fps": 30
  }
}
```

### 步骤 4: 创建输出目录

```bash
mkdir -p localstore/recordings/videos
mkdir -p localstore/recordings/snapshots
```

---

## 工业风格 UI 优化指南

### 设计原则

1. **深色主题**
   - 主背景：`#0f172a`（深蓝灰）
   - 面板背景：`rgba(15, 23, 42, 0.95)`
   - 边框：`rgba(148, 163, 184, 0.2)`（半透明灰）

2. **高对比度**
   - 主要文字：`#e2e8f0`（浅灰）
   - 次要文字：`#94a3b8`（中灰）
   - 禁用状态：`opacity: 0.5`

3. **专业配色**
   - 蓝色（信息）：`#3b82f6`
   - 红色（警告/录制）：`#dc2626`
   - 橙色（操作中）：`#fb923c`
   - 绿色（成功）：`#22c55e`
   - 黄色（提示）：`#eab308`

4. **状态指示**
   - 使用发光效果：`box-shadow: 0 0 0 4px rgba(color, 0.2)`
   - 动画脉冲：录制中、连接中等状态
   - 明确的视觉反馈

### 全局样式建议

创建 `src/web/src/assets/industrial-theme.css`：

```css
:root {
  /* 工业配色 */
  --industrial-bg-primary: #0f172a;
  --industrial-bg-secondary: #1e293b;
  --industrial-bg-tertiary: #334155;
  
  --industrial-border-subtle: rgba(148, 163, 184, 0.1);
  --industrial-border-default: rgba(148, 163, 184, 0.2);
  --industrial-border-emphasis: rgba(148, 163, 184, 0.4);
  
  --industrial-text-primary: #e2e8f0;
  --industrial-text-secondary: #94a3b8;
  --industrial-text-tertiary: #64748b;
  
  --industrial-status-info: #3b82f6;
  --industrial-status-success: #22c55e;
  --industrial-status-warning: #eab308;
  --industrial-status-danger: #dc2626;
  
  /* 间距 */
  --spacing-xs: 4px;
  --spacing-sm: 8px;
  --spacing-md: 12px;
  --spacing-lg: 16px;
  --spacing-xl: 24px;
  
  /* 圆角 */
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  
  /* 阴影 */
  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
  
  /* 动画 */
  --transition-fast: 150ms ease;
  --transition-base: 200ms ease;
  --transition-slow: 300ms ease;
}

/* 全局样式 */
body {
  background: var(--industrial-bg-primary);
  color: var(--industrial-text-primary);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* 工业面板通用样式 */
.industrial-panel {
  background: rgba(15, 23, 42, 0.95);
  border: 1px solid var(--industrial-border-default);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.industrial-panel-header {
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--industrial-border-subtle);
  background: rgba(15, 23, 42, 0.5);
}

.industrial-panel-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--industrial-text-primary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.industrial-panel-body {
  padding: var(--spacing-lg);
}

/* 按钮样式 */
.industrial-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: 10px 16px;
  border: 1px solid var(--industrial-border-default);
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-base);
  background: rgba(30, 41, 59, 0.8);
  color: var(--industrial-text-primary);
}

.industrial-btn:hover:not(:disabled) {
  background: rgba(51, 65, 85, 0.9);
  border-color: var(--industrial-border-emphasis);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}

.industrial-btn:active:not(:disabled) {
  transform: translateY(0);
}

.industrial-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 状态指示灯 */
.status-indicator {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: var(--spacing-sm);
}

.status-indicator.success {
  background: var(--industrial-status-success);
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.2);
}

.status-indicator.warning {
  background: var(--industrial-status-warning);
  box-shadow: 0 0 0 3px rgba(234, 179, 8, 0.2);
}

.status-indicator.danger {
  background: var(--industrial-status-danger);
  box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.2);
  animation: pulse 1.5s ease-in-out infinite;
}

.status-indicator.info {
  background: var(--industrial-status-info);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
}

/* 数据显示（数字仪表） */
.data-display {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-xs);
  font-variant-numeric: tabular-nums;
}

.data-display .value {
  font-size: 24px;
  font-weight: 700;
  color: var(--industrial-text-primary);
  line-height: 1;
}

.data-display .unit {
  font-size: 14px;
  font-weight: 500;
  color: var(--industrial-text-secondary);
}

/* 网格布局 */
.industrial-grid {
  display: grid;
  gap: var(--spacing-md);
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}

/* 脉冲动画 */
@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

/* 滑动动画 */
@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-10px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

/* 工具提示 */
.industrial-tooltip {
  position: absolute;
  z-index: 1000;
  padding: 6px 10px;
  background: rgba(15, 23, 42, 0.95);
  border: 1px solid var(--industrial-border-default);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--industrial-text-primary);
  white-space: nowrap;
  pointer-events: none;
  box-shadow: var(--shadow-lg);
}
```

### 在主应用中应用样式

在 `src/web/src/main.ts` 中导入：

```typescript
import './assets/industrial-theme.css'
```

---

## 测试步骤

1. **启动服务**
   ```bash
   poetry run gripper-ai-controller web --config-file localstore/hikvision-object-detection.local.json
   ```

2. **测试截图**
   - 打开浏览器访问 `http://127.0.0.1:8000`
   - 点击"截图"按钮
   - 检查 `localstore/recordings/snapshots/` 目录

3. **测试录制**
   - 点击"开始录制"
   - 观察录制状态（时长、帧数）
   - 点击"停止录制"
   - 检查 `localstore/recordings/videos/` 目录

4. **测试API**
   ```bash
   # 列出录制
   curl http://127.0.0.1:8000/api/recordings

   # 列出截图
   curl http://127.0.0.1:8000/api/snapshots

   # 获取录制状态
   curl http://127.0.0.1:8000/api/cameras/hikvision-usb/recording/status
   ```

---

## 故障排查

### 问题1: 录制按钮灰色不可用
**原因**: `recording_enabled` 未设置为 `true`  
**解决**: 检查配置文件中的 `web.recording_enabled`

### 问题2: 录制的视频无法播放
**原因**: 可能是codec不兼容  
**解决**: 在 `recording_service.py` 中将 codec 从 `mp4v` 改为 `avc1` (H.264):
```python
fourcc = cv2.VideoWriter_fourcc(*'avc1')
```

### 问题3: 录制帧率太低
**原因**: 相机帧率限制或处理瓶颈  
**解决**: 
- 降低 `recording_default_fps` 
- 在配置中增加 `stream_fps`
- 确保GPU可用（CUDA加速）

### 问题4: UI组件不显示
**原因**: Vue组件未正确导入  
**解决**: 检查导入路径和组件注册

---

## 下一步优化

1. **文件管理界面**
   - 创建专门的录制/截图浏览页面
   - 支持预览、下载、删除
   - 显示文件大小、时长等元数据

2. **高级录制选项**
   - 设置视频分辨率
   - 选择编码格式（H.264, H.265, VP9）
   - 添加时间戳水印
   - 录制区域选择（ROI）

3. **批量操作**
   - 批量导出截图
   - 批量转换视频格式
   - 自动清理旧文件

4. **云存储集成**
   - 自动上传到OSS/S3
   - 远程访问录制文件

5. **更多工业UI元素**
   - 添加六角形按钮
   - 矩形分割线
   - 科技感图标
   - 数据可视化仪表盘
