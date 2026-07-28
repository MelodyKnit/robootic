<script setup lang="ts">
import { computed } from 'vue'

import type { CameraState, CameraStatus } from '../api/camera'
import CameraParameterPanel from './CameraParameterPanel.vue'

const props = defineProps<{
  camera: CameraStatus | null
  isLoading: boolean
}>()

const emit = defineEmits<{
  reconnect: []
}>()

const stateLabels: Record<CameraState, string> = {
  starting: '正在启动',
  streaming: '正在采集',
  degraded: '已降级',
  stopped: '已停止',
}

const stateLabel = computed(() => (props.camera === null ? '未连接' : stateLabels[props.camera.state]))
const formattedFrameTime = computed(() => {
  const timestamp = props.camera?.latestFrameAt
  if (timestamp === null || timestamp === undefined) {
    return '暂无画面'
  }
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(timestamp * 1_000))
})

function requestReconnect(): void {
  emit('reconnect')
}
</script>

<template>
  <section class="border-b border-slate-800 bg-slate-950" aria-label="相机适配器" data-testid="camera-adapter">
    <div class="flex items-center justify-between gap-3 px-4 py-3">
      <div class="min-w-0">
        <p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Adapter</p>
        <h2 class="mt-0.5 truncate text-sm font-bold text-slate-100">相机</h2>
      </div>
      <button
        class="shrink-0 rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
        type="button"
        :disabled="isLoading"
        @click="requestReconnect"
      >
        重新连接
      </button>
    </div>

    <dl class="grid grid-cols-2 gap-x-3 gap-y-2 border-y border-slate-800 px-4 py-3 text-[11px]">
      <div>
        <dt class="text-slate-500">设备</dt>
        <dd class="mt-0.5 truncate font-semibold text-slate-300" :title="camera?.cameraId ?? ''">{{ camera?.cameraId ?? '未发现' }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">状态</dt>
        <dd class="mt-0.5 font-semibold" :class="camera?.state === 'streaming' ? 'text-emerald-400' : 'text-amber-300'">{{ stateLabel }}</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">最新帧</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ formattedFrameTime }}</dd>
      </div>
    </dl>

    <p v-if="camera?.error" class="mx-4 mt-3 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-300" role="alert">
      {{ camera.error.message }}
    </p>
    <CameraParameterPanel :camera-id="camera?.cameraId ?? null" />
  </section>
</template>
