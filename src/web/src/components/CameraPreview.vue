<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import CameraAdapterPanel from './CameraAdapterPanel.vue'
import GripperControlPanel from './GripperControlPanel.vue'
import HardwareAdapterSection from './HardwareAdapterSection.vue'
import PluginWorkspace from './PluginWorkspace.vue'
import PoseSkeletonOverlay from './PoseSkeletonOverlay.vue'
import RobotControlPanel from './RobotControlPanel.vue'
import { type CameraState } from '../api/camera'
import { useCameraPreview } from '../composables/useCameraPreview'
import { usePoseTracking } from '../composables/usePoseTracking'
import { useVisionAnalysis } from '../composables/useVisionAnalysis'

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

const streamImage = ref<HTMLImageElement | null>(null)

const stateLabels: Record<CameraState, string> = {
  starting: '正在启动',
  streaming: '正在采集',
  degraded: '已降级',
  stopped: '已停止',
}

const cameraStateLabel = computed(() => (camera.value === null ? '未连接' : stateLabels[camera.value.state]))
const formattedFrameTime = computed(() => {
  const timestamp = camera.value?.latestFrameAt
  if (timestamp === null || timestamp === undefined) {
    return '暂无画面'
  }
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(timestamp * 1_000))
})

/** Keeps the stream as the sole image source while stale model results stay off the live preview. */
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
  void start()
  void startPoseTracking()
  void startVisionAnalysis()
})

onBeforeUnmount(() => {
  stopVisionAnalysis()
  stopPoseTracking()
  stop()
})
</script>

<template>
  <div class="min-h-0 w-full flex-1 overflow-y-auto bg-slate-900 xl:overflow-hidden">
    <div class="grid min-h-full grid-cols-1 xl:h-full xl:grid-cols-[minmax(17rem,22rem)_minmax(0,1fr)_minmax(23rem,30rem)]">
      <PluginWorkspace
        :pose="pose"
        :pose-error-message="poseErrorMessage"
        :is-updating-target="isUpdatingTarget"
        :tracking-enabled="trackingEnabled"
        :analysis="analysis"
        :analysis-error-message="analysisErrorMessage"
        @select-target="selectTarget"
      />

      <section class="relative flex min-h-[28rem] min-w-0 flex-col bg-slate-900 p-3 sm:p-5 xl:min-h-0 xl:overflow-hidden" aria-label="相机实时画面">
        <header class="mb-4 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-slate-800 bg-slate-950/80 px-4 py-2.5 shadow-md">
          <h1 class="text-sm font-bold tracking-tight text-slate-100">相机预览</h1>
          <span class="text-xs font-semibold text-slate-500">|</span>
          <span class="max-w-[12rem] truncate text-xs font-semibold text-slate-400" :title="camera?.cameraId ?? ''">
            {{ camera?.cameraId ?? '正在连接相机...' }}
          </span>
          <span
            class="inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold"
            :class="{
              'border-emerald-800/50 bg-emerald-950/50 text-emerald-400': camera?.state === 'streaming',
              'border-amber-800/50 bg-amber-950/50 text-amber-400': camera?.state === 'starting',
              'border-rose-800/50 bg-rose-950/50 text-rose-400': camera?.state === 'stopped' || camera?.state === 'degraded' || !camera,
            }"
          >
            {{ cameraStateLabel }}
          </span>
          <span class="ml-auto text-xs font-semibold text-slate-500" :title="`最新帧时间: ${formattedFrameTime}`">{{ formattedFrameTime }}</span>
        </header>

        <div class="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg border border-slate-800 bg-slate-950 shadow-inner">
          <div v-if="streamUrl" class="relative inline-flex max-h-full max-w-full">
            <img
              ref="streamImage"
              :key="streamUrl"
              data-testid="camera-stream"
              :src="streamUrl"
              alt="实时相机画面"
              class="block max-h-full max-w-full object-contain object-center"
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
          <div v-if="!streamUrl" class="flex flex-col items-center p-6 text-center" role="status">
            <div class="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-slate-800 border-t-slate-500"></div>
            <p class="text-xs text-slate-500">正在等待相机画面...</p>
          </div>
          <div
            v-if="!frameAvailable && streamUrl"
            class="absolute inset-0 flex flex-col items-center justify-center bg-slate-950/90 backdrop-blur-sm"
            role="status"
          >
            <div class="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-slate-800 border-t-slate-400"></div>
            <p class="text-xs text-slate-400">正在建立画面流...</p>
          </div>
        </div>

        <p v-if="errorMessage" class="mt-3 shrink-0 rounded border border-rose-900/40 bg-rose-950/20 px-3 py-2 text-xs text-rose-400" role="alert">
          {{ errorMessage }}
        </p>
      </section>

      <aside class="flex min-h-[28rem] flex-col border-t border-slate-800 bg-slate-950 xl:min-h-0 xl:overflow-y-auto xl:border-l xl:border-t-0" aria-label="硬件适配器">
        <header class="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 px-4 py-3 backdrop-blur">
          <p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Adapter</p>
          <h2 class="mt-0.5 text-sm font-bold text-slate-100">硬件模块</h2>
        </header>
        <HardwareAdapterSection adapter-id="camera" label="相机适配器" test-id="camera-adapter-section">
          <CameraAdapterPanel :camera="camera" :is-loading="isLoading" @reconnect="start" />
        </HardwareAdapterSection>
        <HardwareAdapterSection adapter-id="gripper" label="夹爪适配器" test-id="gripper-adapter">
          <GripperControlPanel />
        </HardwareAdapterSection>
        <HardwareAdapterSection adapter-id="robot" label="机械臂适配器" test-id="robot-adapter">
          <RobotControlPanel />
        </HardwareAdapterSection>
      </aside>
    </div>
  </div>
</template>
