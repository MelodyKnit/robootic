<script setup lang="ts">
import { useRecording } from '../composables/useRecording'

const props = defineProps<{
  cameraId: string
}>()

const {
  status,
  isRecording,
  frameCount,
  isOperating,
  message,
  isError,
  takeScreenshot,
  startRecording,
  stopRecording,
} = useRecording(() => props.cameraId)
</script>

<template>
  <div class="industrial-toolbar">
    <div class="toolbar-group">
      <button
        class="ind-btn ind-btn-secondary"
        :disabled="isOperating || !status?.enabled"
        title="截取当前帧画面保存为高清晰度图片"
        @click="takeScreenshot"
      >
        <svg class="ind-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
          <circle cx="12" cy="13" r="4"/>
        </svg>
        <span>快照截图</span>
      </button>

      <button
        v-if="!isRecording"
        class="ind-btn ind-btn-danger"
        :disabled="isOperating || !status?.enabled"
        title="开始将相机实时画面录制为 AVI 视频"
        @click="startRecording()"
      >
        <svg class="ind-icon" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="12" r="8" fill="#ff4d4f"/>
        </svg>
        <span>开始录制</span>
      </button>

      <button
        v-else
        class="ind-btn ind-btn-recording pulsing"
        :disabled="isOperating"
        title="停止当前视频录制并保存"
        @click="stopRecording"
      >
        <svg class="ind-icon" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2" fill="#ffffff"/>
        </svg>
        <span>停止录制 ({{ frameCount }} 帧)</span>
      </button>
    </div>

    <div v-if="message" :class="['status-toast', isError ? 'toast-error' : 'toast-success']">
      {{ message }}
    </div>
  </div>
</template>

<style scoped>
.industrial-toolbar {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  font-family: var(--ind-font-family, 'Segoe UI', system-ui, sans-serif);
}

.toolbar-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: rgba(18, 24, 38, 0.85);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(56, 75, 112, 0.6);
  border-radius: 4px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.5);
}

.ind-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: #e2e8f0;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.ind-btn:hover:not(:disabled) {
  background: #334155;
  border-color: #475569;
  color: #ffffff;
}

.ind-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ind-btn-danger:hover:not(:disabled) {
  background: #7f1d1d;
  border-color: #991b1b;
  color: #fca5a5;
}

.ind-btn-recording {
  background: #dc2626;
  border-color: #ef4444;
  color: #ffffff;
}

.pulsing {
  animation: pulse-border 1.5s infinite;
}

@keyframes pulse-border {
  0% {
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(239, 68, 68, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
  }
}

.ind-icon {
  width: 14px;
  height: 14px;
}

.status-toast {
  padding: 6px 12px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 3px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
  max-width: 320px;
  word-break: break-all;
}

.toast-success {
  background: #064e3b;
  border: 1px solid #047857;
  color: #a7f3d0;
}

.toast-error {
  background: #7f1d1d;
  border: 1px solid #b91c1c;
  color: #fecaca;
}
</style>
