<script setup lang="ts">
import { computed, ref } from 'vue'

interface RecordingStatus {
  is_recording: boolean
  recording_id?: string
  frame_count?: number
  fps?: number
  duration_seconds?: number
  started_at?: string
}

const props = defineProps<{
  cameraId: string
}>()

const emit = defineEmits<{
  snapshot: []
  recordingStarted: []
  recordingStopped: [result: any]
}>()

const isRecording = ref(false)
const recordingStatus = ref<RecordingStatus | null>(null)
const isSavingSnapshot = ref(false)
const isStartingRecording = ref(false)
const isStoppingRecording = ref(false)
const errorMessage = ref<string | null>(null)

const statusInterval = ref<number | null>(null)

const durationDisplay = computed(() => {
  if (!recordingStatus.value?.duration_seconds) return '00:00'
  const seconds = Math.floor(recordingStatus.value.duration_seconds)
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
})

const frameCountDisplay = computed(() => {
  if (!recordingStatus.value?.frame_count) return '0'
  return recordingStatus.value.frame_count.toLocaleString()
})

async function takeSnapshot() {
  if (isSavingSnapshot.value) return

  isSavingSnapshot.value = true
  errorMessage.value = null

  try {
    const response = await fetch(`/api/cameras/${props.cameraId}/snapshot`, {
      method: 'POST'
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.message || '截图失败')
    }

    await response.json()
    emit('snapshot')

    // 显示成功提示
    errorMessage.value = `截图已保存`
    setTimeout(() => {
      if (errorMessage.value === `截图已保存`) {
        errorMessage.value = null
      }
    }, 3000)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '截图失败'
  } finally {
    isSavingSnapshot.value = false
  }
}

async function startRecording() {
  if (isStartingRecording.value || isRecording.value) return

  isStartingRecording.value = true
  errorMessage.value = null

  try {
    const response = await fetch(`/api/cameras/${props.cameraId}/recording/start`, {
      method: 'POST'
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.message || '开始录制失败')
    }

    await response.json()
    isRecording.value = true
    emit('recordingStarted')

    // 开始轮询状态
    startStatusPolling()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '开始录制失败'
  } finally {
    isStartingRecording.value = false
  }
}

async function stopRecording() {
  if (isStoppingRecording.value || !isRecording.value) return

  isStoppingRecording.value = true
  errorMessage.value = null

  try {
    const response = await fetch(`/api/cameras/${props.cameraId}/recording/stop`, {
      method: 'POST'
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.message || '停止录制失败')
    }

    const result = await response.json()
    isRecording.value = false
    recordingStatus.value = null
    emit('recordingStopped', result)

    // 停止轮询
    stopStatusPolling()

    // 显示成功提示
    errorMessage.value = `录制已保存: ${result.frame_count} 帧, ${result.duration_seconds.toFixed(1)}秒`
    setTimeout(() => {
      errorMessage.value = null
    }, 5000)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '停止录制失败'
  } finally {
    isStoppingRecording.value = false
  }
}

async function fetchRecordingStatus() {
  try {
    const response = await fetch(`/api/cameras/${props.cameraId}/recording/status`)
    if (response.ok) {
      const status = await response.json()
      recordingStatus.value = status
      isRecording.value = status.is_recording
    }
  } catch (error) {
    console.error('Failed to fetch recording status:', error)
  }
}

function startStatusPolling() {
  if (statusInterval.value) return

  statusInterval.value = window.setInterval(() => {
    fetchRecordingStatus()
  }, 500) // 每500ms更新一次
}

function stopStatusPolling() {
  if (statusInterval.value) {
    clearInterval(statusInterval.value)
    statusInterval.value = null
  }
}

// 组件挂载时检查状态
fetchRecordingStatus()

// 组件卸载时停止轮询
import { onBeforeUnmount } from 'vue'
onBeforeUnmount(() => {
  stopStatusPolling()
})
</script>

<template>
  <div class="recording-controls">
    <div class="controls-header">
      <h3>录制与截图</h3>
    </div>

    <div class="controls-body">
      <!-- 录制状态显示 -->
      <div v-if="isRecording" class="recording-status">
        <div class="status-indicator recording"></div>
        <div class="status-info">
          <div class="status-label">录制中</div>
          <div class="status-details">
            <span class="duration">{{ durationDisplay }}</span>
            <span class="divider">·</span>
            <span class="frames">{{ frameCountDisplay }} 帧</span>
          </div>
        </div>
      </div>

      <!-- 按钮组 -->
      <div class="button-group">
        <button
          class="btn btn-snapshot"
          :disabled="isSavingSnapshot || isRecording"
          @click="takeSnapshot"
        >
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <circle cx="8.5" cy="8.5" r="1.5"></circle>
            <polyline points="21 15 16 10 5 21"></polyline>
          </svg>
          <span>{{ isSavingSnapshot ? '保存中...' : '截图' }}</span>
        </button>

        <button
          v-if="!isRecording"
          class="btn btn-record-start"
          :disabled="isStartingRecording"
          @click="startRecording"
        >
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
            <circle cx="12" cy="12" r="8"></circle>
          </svg>
          <span>{{ isStartingRecording ? '启动中...' : '开始录制' }}</span>
        </button>

        <button
          v-else
          class="btn btn-record-stop"
          :disabled="isStoppingRecording"
          @click="stopRecording"
        >
          <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="1"></rect>
          </svg>
          <span>{{ isStoppingRecording ? '停止中...' : '停止录制' }}</span>
        </button>
      </div>

      <!-- 错误/成功消息 -->
      <div v-if="errorMessage" class="message" :class="{ success: errorMessage.includes('已保存') }">
        {{ errorMessage }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.recording-controls {
  background: rgba(15, 23, 42, 0.95);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 8px;
  overflow: hidden;
}

.controls-header {
  padding: 12px 16px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
  background: rgba(15, 23, 42, 0.5);
}

.controls-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #e2e8f0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.controls-body {
  padding: 16px;
}

.recording-status {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: 6px;
  margin-bottom: 16px;
}

.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-indicator.recording {
  background: #dc2626;
  box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.2);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.status-info {
  flex: 1;
}

.status-label {
  font-size: 12px;
  font-weight: 600;
  color: #dc2626;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
}

.status-details {
  font-size: 14px;
  color: #e2e8f0;
  font-variant-numeric: tabular-nums;
}

.divider {
  margin: 0 8px;
  color: rgba(226, 232, 240, 0.4);
}

.button-group {
  display: flex;
  gap: 8px;
}

.btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  background: rgba(30, 41, 59, 0.8);
  color: #e2e8f0;
}

.btn:hover:not(:disabled) {
  background: rgba(51, 65, 85, 0.9);
  border-color: rgba(148, 163, 184, 0.4);
  transform: translateY(-1px);
}

.btn:active:not(:disabled) {
  transform: translateY(0);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-snapshot {
  border-color: rgba(59, 130, 246, 0.3);
}

.btn-snapshot:hover:not(:disabled) {
  background: rgba(59, 130, 246, 0.15);
  border-color: rgba(59, 130, 246, 0.5);
}

.btn-record-start {
  border-color: rgba(220, 38, 38, 0.3);
}

.btn-record-start:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.15);
  border-color: rgba(220, 38, 38, 0.5);
}

.btn-record-start .icon {
  color: #dc2626;
}

.btn-record-stop {
  border-color: rgba(251, 146, 60, 0.3);
  background: rgba(251, 146, 60, 0.1);
}

.btn-record-stop:hover:not(:disabled) {
  background: rgba(251, 146, 60, 0.2);
  border-color: rgba(251, 146, 60, 0.5);
}

.btn .icon {
  width: 18px;
  height: 18px;
}

.message {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  color: #fca5a5;
}

.message.success {
  background: rgba(34, 197, 94, 0.1);
  border-color: rgba(34, 197, 94, 0.3);
  color: #86efac;
}
</style>
