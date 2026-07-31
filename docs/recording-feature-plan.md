# 相机录制和截图功能实现方案

## 概述
为系统增加录制和截图功能，允许用户从相机画面中保存样本数据。

## 已完成的工作

### 1. 后端核心模块
- ✅ 创建 `src/gripper_ai_controller/web/recorder.py`
  - `CameraRecorder` 类：管理录制和截图
  - `capture_screenshot()`: 截取单帧保存为 JPG
  - `start_recording()`: 开始视频录制
  - `stop_recording()`: 停止录制
  - `write_frame()`: 写入视频帧

### 2. 配置扩展
- ✅ 在 `configuration.py` 的 `WebPreviewSettings` 中添加:
  - `recording_enabled: bool = True` - 启用录制功能
  - `recording_output_dir: str = "localstore/recordings"` - 保存目录
  - `recording_default_fps: int = 30` - 默认帧率

## 待实现的工作

### 3. Web Service 集成 (高优先级)
需要修改 `src/gripper_ai_controller/web/service.py`:

```python
from gripper_ai_controller.web.recorder import CameraRecorder
from pathlib import Path

class CameraPreviewService:
    def __init__(self, ...):
        # 在现有初始化中添加
        self._recorder: Optional[CameraRecorder] = None
        if settings.recording_enabled:
            output_dir = Path(settings.recording_output_dir)
            self._recorder = CameraRecorder(output_dir)
    
    def _on_jpeg_frame_published(self, ...):
        # 在现有方法中添加录制逻辑
        if self._recorder and self._recorder.is_recording:
            # 获取原始帧并写入
            self._recorder.write_frame(frame)
    
    async def capture_screenshot(self) -> Dict[str, Any]:
        """截图API"""
        if self._recorder is None:
            raise RuntimeError("Recording disabled")
        
        frame = await self._get_latest_frame()
        filepath = self._recorder.capture_screenshot(frame)
        
        return {
            "success": True,
            "filepath": str(filepath),
            "timestamp": datetime.now().isoformat()
        }
    
    async def start_recording(self, fps: Optional[int] = None) -> Dict[str, Any]:
        """开始录制API"""
        if self._recorder is None:
            raise RuntimeError("Recording disabled")
        
        if self._recorder.is_recording:
            raise RuntimeError("Already recording")
        
        frame = await self._get_latest_frame()
        fps = fps or self.settings.recording_default_fps
        filepath = self._recorder.start_recording(frame, fps)
        
        return {
            "success": True,
            "filepath": str(filepath),
            "fps": fps
        }
    
    async def stop_recording(self) -> Dict[str, Any]:
        """停止录制API"""
        if self._recorder is None:
            raise RuntimeError("Recording disabled")
        
        result = self._recorder.stop_recording()
        if result is None:
            raise RuntimeError("Not recording")
        
        filepath, frame_count = result
        return {
            "success": True,
            "filepath": str(filepath),
            "frame_count": frame_count,
            "duration_seconds": frame_count / self.settings.recording_default_fps
        }
    
    def get_recording_status(self) -> Dict[str, Any]:
        """获取录制状态"""
        if self._recorder is None:
            return {"enabled": False}
        
        return {
            "enabled": True,
            "is_recording": self._recorder.is_recording,
            "frame_count": self._recorder.frame_count,
            "output_dir": str(self._recorder.output_dir)
        }
```

### 4. HTTP API 端点 (高优先级)
需要在 `src/gripper_ai_controller/web/app.py` 添加路由:

```python
@app.post("/api/camera/{camera_id}/screenshot")
async def capture_screenshot(camera_id: str):
    """截图"""
    service = _get_camera_service(camera_id)
    result = await service.capture_screenshot()
    return result

@app.post("/api/camera/{camera_id}/recording/start")
async def start_recording(camera_id: str, fps: Optional[int] = None):
    """开始录制"""
    service = _get_camera_service(camera_id)
    result = await service.start_recording(fps)
    return result

@app.post("/api/camera/{camera_id}/recording/stop")
async def stop_recording(camera_id: str):
    """停止录制"""
    service = _get_camera_service(camera_id)
    result = await service.stop_recording()
    return result

@app.get("/api/camera/{camera_id}/recording/status")
async def get_recording_status(camera_id: str):
    """录制状态"""
    service = _get_camera_service(camera_id)
    return service.get_recording_status()
```

### 5. 前端 API 客户端 (中优先级)
创建 `src/web/src/api/recording.ts`:

```typescript
export interface RecordingStatus {
  enabled: boolean
  is_recording: boolean
  frame_count: number
  output_dir: string
}

export interface ScreenshotResult {
  success: boolean
  filepath: string
  timestamp: string
}

export interface RecordingResult {
  success: boolean
  filepath: string
  frame_count?: number
  duration_seconds?: number
  fps?: number
}

export async function captureScreenshot(cameraId: string): Promise<ScreenshotResult> {
  const response = await fetch(`/api/camera/${cameraId}/screenshot`, {
    method: 'POST'
  })
  if (!response.ok) throw new Error('Screenshot failed')
  return response.json()
}

export async function startRecording(cameraId: string, fps?: number): Promise<RecordingResult> {
  const url = `/api/camera/${cameraId}/recording/start${fps ? `?fps=${fps}` : ''}`
  const response = await fetch(url, { method: 'POST' })
  if (!response.ok) throw new Error('Start recording failed')
  return response.json()
}

export async function stopRecording(cameraId: string): Promise<RecordingResult> {
  const response = await fetch(`/api/camera/${cameraId}/recording/stop`, {
    method: 'POST'
  })
  if (!response.ok) throw new Error('Stop recording failed')
  return response.json()
}

export async function getRecordingStatus(cameraId: string): Promise<RecordingStatus> {
  const response = await fetch(`/api/camera/${cameraId}/recording/status`)
  if (!response.ok) throw new Error('Get status failed')
  return response.json()
}
```

### 6. 前端 UI 组件 (中优先级)
在 `CameraPreview.vue` 中添加录制控制按钮:

```vue
<template>
  <!-- 在相机预览区域添加悬浮工具栏 -->
  <div class="recording-toolbar">
    <button 
      @click="handleScreenshot" 
      :disabled="!recordingStatus?.enabled"
      class="toolbar-btn screenshot-btn"
      title="截图 (Ctrl+S)"
    >
      <CameraIcon />
      <span>截图</span>
    </button>
    
    <button 
      v-if="!isRecording"
      @click="handleStartRecording"
      :disabled="!recordingStatus?.enabled"
      class="toolbar-btn record-start-btn"
      title="开始录制 (Ctrl+R)"
    >
      <RecordIcon />
      <span>录制</span>
    </button>
    
    <button 
      v-else
      @click="handleStopRecording"
      class="toolbar-btn record-stop-btn recording-active"
      title="停止录制 (Ctrl+R)"
    >
      <StopIcon />
      <span>停止 ({{ recordingFrameCount }}帧)</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { captureScreenshot, startRecording, stopRecording, getRecordingStatus } from '../api/recording'

const recordingStatus = ref(null)
const isRecording = ref(false)
const recordingFrameCount = ref(0)

// 定期更新录制状态
setInterval(async () => {
  if (isRecording.value) {
    const status = await getRecordingStatus(camera.value.id)
    recordingFrameCount.value = status.frame_count
  }
}, 500)

async function handleScreenshot() {
  try {
    const result = await captureScreenshot(camera.value.id)
    // 显示成功提示
    showNotification(`截图已保存: ${result.filepath}`)
  } catch (error) {
    showError('截图失败: ' + error.message)
  }
}

async function handleStartRecording() {
  try {
    const result = await startRecording(camera.value.id)
    isRecording.value = true
    showNotification('开始录制')
  } catch (error) {
    showError('开始录制失败: ' + error.message)
  }
}

async function handleStopRecording() {
  try {
    const result = await stopRecording(camera.value.id)
    isRecording.value = false
    recordingFrameCount.value = 0
    showNotification(`录制已保存: ${result.filepath} (${result.frame_count}帧)`)
  } catch (error) {
    showError('停止录制失败: ' + error.message)
  }
}
</script>
```

### 7. 工业风格样式优化 (低优先级)
创建 `src/web/src/styles/industrial.css`:

```css
/* 工业风格主题 */
:root {
  --industrial-bg: #1a1d23;
  --industrial-surface: #2a2d35;
  --industrial-border: #3a3d45;
  --industrial-accent: #00a8ff;
  --industrial-success: #00c851;
  --industrial-warning: #ffbb33;
  --industrial-danger: #ff4444;
  --industrial-text: #e8eaed;
  --industrial-text-muted: #9aa0a6;
}

.recording-toolbar {
  position: absolute;
  top: 16px;
  right: 16px;
  display: flex;
  gap: 8px;
  background: rgba(26, 29, 35, 0.9);
  backdrop-filter: blur(8px);
  padding: 8px;
  border-radius: 8px;
  border: 1px solid var(--industrial-border);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--industrial-surface);
  border: 1px solid var(--industrial-border);
  border-radius: 4px;
  color: var(--industrial-text);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.toolbar-btn:hover:not(:disabled) {
  background: var(--industrial-border);
  border-color: var(--industrial-accent);
}

.toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.screenshot-btn:hover:not(:disabled) {
  border-color: var(--industrial-accent);
}

.record-start-btn:hover:not(:disabled) {
  border-color: var(--industrial-danger);
}

.record-stop-btn {
  background: var(--industrial-danger);
  border-color: var(--industrial-danger);
}

.recording-active {
  animation: recording-pulse 1.5s ease-in-out infinite;
}

@keyframes recording-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
```

## 配置示例

在 `localstore/hikvision-web.local.json` 中添加:

```json
{
  "web": {
    "recording_enabled": true,
    "recording_output_dir": "localstore/recordings",
    "recording_default_fps": 30
  }
}
```

## 测试清单

- [ ] 截图功能测试
  - [ ] 单次截图保存
  - [ ] 文件命名格式正确
  - [ ] 图像质量检查
  
- [ ] 录制功能测试
  - [ ] 开始/停止录制
  - [ ] 帧数统计正确
  - [ ] 视频可播放
  - [ ] 并发录制保护
  
- [ ] UI测试
  - [ ] 按钮状态切换
  - [ ] 快捷键支持
  - [ ] 录制中显示帧数
  - [ ] 成功/失败提示
  
- [ ] 配置测试
  - [ ] 自定义保存路径
  - [ ] 自定义帧率
  - [ ] 禁用录制功能

## 后续增强

1. 添加录制历史浏览
2. 支持选择编码格式 (H.264, MJPEG)
3. 支持裁剪区域录制
4. 添加水印和时间戳
5. 支持网络推流 (RTSP)
