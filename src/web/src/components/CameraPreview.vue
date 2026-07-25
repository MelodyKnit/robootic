<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import CameraParameterPanel from './CameraParameterPanel.vue'
import PoseSkeletonOverlay from './PoseSkeletonOverlay.vue'
import PoseTargetPanel from './PoseTargetPanel.vue'
import VisionAnalysisPanel from './VisionAnalysisPanel.vue'
import { useCameraPreview } from '../composables/useCameraPreview'
import { usePoseTracking } from '../composables/usePoseTracking'
import { useVisionAnalysis } from '../composables/useVisionAnalysis'
import { type CameraState } from '../api/camera'

const {
  camera,
  errorMessage,
  frameAvailable,
  isLoading,
  start,
  stop,
  streamUrl,
  onFrameLoaded,
  onStreamError,
} = useCameraPreview()

const selectedCameraId = computed(() => camera.value?.cameraId ?? null)
const {
  errorMessage: poseErrorMessage,
  isUpdatingTarget,
  pose,
  selectTarget,
  start: startPoseTracking,
  stop: stopPoseTracking,
  trackingEnabled,
} = usePoseTracking(selectedCameraId)
const {
  analysis,
  errorMessage: analysisErrorMessage,
  start: startVisionAnalysis,
  stop: stopVisionAnalysis,
} = useVisionAnalysis(selectedCameraId)

const activeTab = ref<'vision' | 'parameter'>('vision')

const streamImage = ref<HTMLImageElement | null>(null)

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

/**
 * The stream remains the sole image source. A fresh person pose remains useful
 * for visual analysis even when the selected joint does not qualify as a lock.
 * Target validity controls follow eligibility, not skeleton rendering.
 */
const poseForOverlay = computed(() => {
  const currentPose = pose.value
  if (
    currentPose === null ||
    !currentPose.enabled ||
    currentPose.person === null ||
    !currentPose.overlayFresh
  ) {
    return null
  }
  return currentPose
})

/** Keeps detection boxes aligned with the exact frame that produced the pose. */
const analysisForOverlay = computed(() => {
  const currentPose = poseForOverlay.value
  const currentAnalysis = analysis.value
  if (
    currentPose === null ||
    currentPose.capturedAt === null ||
    currentAnalysis === null ||
    currentAnalysis.inferenceCapturedAt === null ||
    Math.abs(currentPose.capturedAt - currentAnalysis.inferenceCapturedAt) > 0.001
  ) {
    return null
  }
  return currentAnalysis
})

onMounted(() => {
  start()
  startPoseTracking()
  startVisionAnalysis()
})

onBeforeUnmount(() => {
  stopVisionAnalysis()
  stopPoseTracking()
  stop()
})
</script>

<template>
  <div class="flex-1 flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden min-h-0 w-full bg-slate-900">
    <!-- Camera preview area with a dark background that keeps attention on the image. -->
    <section class="flex-1 flex flex-col min-w-0 min-h-[26rem] lg:min-h-0 bg-slate-900 p-3 sm:p-5 overflow-hidden select-none relative" aria-label="相机实时画面">
      <!-- Preview header with camera identity and connection state. -->
      <div class="flex flex-wrap justify-between items-center gap-2 bg-slate-950/80 border border-slate-800 rounded-lg py-2.5 px-4 mb-4 shrink-0 shadow-md">
        <div class="flex items-center gap-3.5 min-w-0">
          <h1 class="text-sm font-bold text-slate-100 tracking-tight shrink-0">相机预览</h1>
          <span class="text-xs text-slate-500 font-semibold px-1 shrink-0">|</span>
          <h2 class="text-xs font-semibold text-slate-400 truncate" :title="camera?.cameraId ?? ''">
            {{ camera?.cameraId ?? '正在连接相机...' }}
          </h2>
          <span
            class="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold shrink-0"
            :class="{
              'bg-emerald-950/50 text-emerald-400 border border-emerald-800/50': camera?.state === 'streaming',
              'bg-amber-950/50 text-amber-400 border border-amber-800/50': camera?.state === 'starting',
              'bg-rose-950/50 text-rose-400 border border-rose-800/50': camera?.state === 'stopped' || camera?.state === 'degraded' || !camera
            }"
          >
            {{ cameraStateLabel }}
          </span>
          <span class="text-xs text-slate-500 font-semibold px-1 shrink-0">|</span>
          <span class="text-xs text-slate-400 truncate font-mono" :title="`最新更新帧时间: ${formattedFrameTime}`">
            {{ formattedFrameTime }}
          </span>
        </div>
        <button
          class="shrink-0 rounded border border-slate-700 bg-slate-900 hover:bg-slate-800 hover:border-slate-600 px-3 py-1 text-xs font-semibold text-slate-200 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
          type="button"
          :disabled="isLoading"
          @click="start"
        >
          重新连接
        </button>
      </div>

      <!-- Continuous MJPEG playback area. -->
      <div class="flex-1 relative border border-slate-800 rounded-lg bg-slate-950 flex justify-center items-center overflow-hidden shadow-inner">
        <div v-if="streamUrl" class="relative inline-flex max-w-full max-h-full">
          <img
            ref="streamImage"
            :key="streamUrl"
            data-testid="camera-stream"
            :src="streamUrl"
            alt="实时相机画面"
            class="block max-w-full max-h-full object-contain object-center aspect-auto"
            @load="onFrameLoaded"
            @error="onStreamError"
          />
          <PoseSkeletonOverlay
            v-if="poseForOverlay !== null"
            data-testid="pose-overlay"
            :pose="poseForOverlay"
            :analysis="analysisForOverlay"
            :image-element="streamImage"
          />
        </div>
        <p
          v-if="pose?.enabled && pose?.person !== null && !pose?.overlayFresh"
          data-testid="pose-overlay-stale"
          class="absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-slate-950/85 px-3 py-1.5 text-xs text-slate-300"
          role="status"
        >
          骨架已过期，保持实时画面
        </p>
        <div v-if="!streamUrl" class="text-center p-6 flex flex-col items-center" role="status">
          <div class="w-8 h-8 rounded-full border-2 border-slate-800 border-t-slate-500 animate-spin mb-3"></div>
          <p class="text-xs text-slate-500">正在等待相机画面...</p>
        </div>

        <div
          v-if="!frameAvailable && streamUrl"
          class="absolute inset-0 bg-slate-950/90 flex flex-col justify-center items-center backdrop-blur-sm"
          role="status"
        >
          <div class="w-8 h-8 rounded-full border-2 border-slate-800 border-t-slate-400 animate-spin mb-3"></div>
          <p class="text-xs text-slate-400">正在建立画面流...</p>
        </div>
      </div>

      <p v-if="errorMessage" class="mt-3 text-xs text-rose-400 bg-rose-950/20 border border-rose-900/40 rounded py-2 px-3 shrink-0" role="alert">
        {{ errorMessage }}
      </p>
    </section>

    <!-- Status and parameter panel. -->
    <aside class="w-full lg:w-[400px] min-h-[24rem] lg:min-h-0 lg:h-full lg:shrink-0 flex flex-col bg-slate-950 border-t lg:border-t-0 lg:border-l border-slate-800/80 overflow-hidden" aria-label="状态与配置">
      <!-- Tab Switcher -->
      <div class="flex border-b border-slate-900 bg-slate-900/10 shrink-0">
        <button
          class="flex-1 py-3 text-xs font-bold uppercase tracking-wider border-b-2 text-center transition"
          :class="activeTab === 'vision' ? 'text-indigo-400 border-indigo-500 bg-slate-900/20' : 'text-slate-400 border-transparent hover:text-slate-200'"
          @click="activeTab = 'vision'"
        >
          视觉与姿态分析
        </button>
        <button
          class="flex-1 py-3 text-xs font-bold uppercase tracking-wider border-b-2 text-center transition"
          :class="activeTab === 'parameter' ? 'text-indigo-400 border-indigo-500 bg-slate-900/20' : 'text-slate-400 border-transparent hover:text-slate-200'"
          id="camera-parameter-tab-button"
          @click="activeTab = 'parameter'"
        >
          相机配置设置
        </button>
      </div>

      <!-- Tab Contents (scrollable container) -->
      <div class="flex-1 overflow-y-auto min-h-0 bg-slate-950">
        <div v-show="activeTab === 'vision'" class="flex flex-col">
          <PoseTargetPanel
            :pose="pose"
            :error-message="poseErrorMessage"
            :is-updating-target="isUpdatingTarget"
            :tracking-enabled="trackingEnabled"
            @select-target="selectTarget"
          />

          <VisionAnalysisPanel :analysis="analysis" :error-message="analysisErrorMessage" />
        </div>

        <div v-show="activeTab === 'parameter'" class="h-full">
          <CameraParameterPanel :camera-id="camera?.cameraId ?? null" />
        </div>
      </div>
    </aside>
  </div>
</template>
