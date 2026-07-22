<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'

import { useCameraPreview } from '../composables/useCameraPreview'
import type { CameraState } from '../api/camera'

const {
  camera,
  errorMessage,
  frameAvailable,
  isLoading,
  start,
  stop,
  streamAttempt,
  streamUrl,
  onFrameLoaded,
  onStreamError,
} = useCameraPreview()

const stateLabels: Record<CameraState, string> = {
  starting: '正在启动',
  streaming: '正在采集',
  degraded: '已降级',
  stopped: '已停止',
}

const cameraStateLabel = computed(() => {
  if (camera.value === null) {
    return '未连接'
  }

  return stateLabels[camera.value.state]
})

const formattedFrameTime = computed(() => {
  const timestamp = camera.value?.latestFrameAt
  if (timestamp === null || timestamp === undefined) {
    return '暂无画面'
  }

  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(timestamp * 1000))
})

const frameSequence = computed(() => {
  const sequence = camera.value?.frameSequence
  return sequence === null || sequence === undefined ? '-' : String(sequence)
})

onMounted(start)
onBeforeUnmount(stop)
</script>

<template>
  <section class="camera-layout" aria-label="相机实时画面">
    <div class="stream-column">
      <div class="stream-header">
        <div>
          <h2>{{ camera?.cameraId ?? '正在获取相机' }}</h2>
          <p class="state-line" :class="`state-${camera?.state ?? 'stopped'}`">
            {{ cameraStateLabel }}
          </p>
        </div>
        <button class="retry-button" type="button" :disabled="isLoading" @click="start">
          重新连接
        </button>
      </div>

      <div class="stream-surface">
        <img
          v-if="streamUrl"
          :key="streamAttempt"
          data-testid="camera-stream"
          :src="streamUrl"
          alt="实时相机画面"
          @load="onFrameLoaded"
          @error="onStreamError"
        />
        <p v-else class="stream-placeholder" role="status">
          正在等待相机画面
        </p>

        <p v-if="!frameAvailable && streamUrl" class="stream-placeholder stream-placeholder-overlay" role="status">
          正在建立画面流
        </p>
      </div>

      <p v-if="errorMessage" class="error-message" role="alert">
        {{ errorMessage }}
      </p>
    </div>

    <aside class="camera-metadata" aria-label="相机状态">
      <dl>
        <div>
          <dt>状态</dt>
          <dd>{{ cameraStateLabel }}</dd>
        </div>
        <div>
          <dt>最新帧</dt>
          <dd>{{ formattedFrameTime }}</dd>
        </div>
        <div>
          <dt>帧序号</dt>
          <dd>{{ frameSequence }}</dd>
        </div>
      </dl>
    </aside>
  </section>
</template>
