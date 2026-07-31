<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import CameraAdapterPanel from './CameraAdapterPanel.vue'
import GripperControlPanel from './GripperControlPanel.vue'
import HardwareAdapterSection from './HardwareAdapterSection.vue'
import ObjectDetectionOverlay from './ObjectDetectionOverlay.vue'
import ObjectPoseOverlay from './ObjectPoseOverlay.vue'
import PluginWorkspace from './PluginWorkspace.vue'
import PoseSkeletonOverlay from './PoseSkeletonOverlay.vue'
import RecordingToolbar from './RecordingToolbar.vue'
import RobotControlPanel from './RobotControlPanel.vue'
import { type CameraState } from '../api/camera'
import { useCameraPreview } from '../composables/useCameraPreview'
import { useObjectDetectionTracking } from '../composables/useObjectDetectionTracking'
import { useObjectPoseTracking } from '../composables/useObjectPoseTracking'
import { usePoseTracking } from '../composables/usePoseTracking'
import { usePluginCatalog } from '../composables/usePluginCatalog'
import { useVisionAnalysis } from '../composables/useVisionAnalysis'

type AdapterTab = 'camera' | 'gripper' | 'robot'
const activeAdapterTab = ref<AdapterTab>('camera')

const {
  activatingPluginIds,
  errorMessage: pluginErrorMessage,
  isActivating: isActivatingPlugin,
  isLoading: isLoadingPlugins,
  isReloading,
  plugins,
  reloadingPluginIds,
  reload,
  reloadablePlugins,
  refresh: refreshPlugins,
  setActivation: setPluginActivation,
} = usePluginCatalog()

const {
  camera,
  devices,
  discoveryError,
  errorMessage,
  frameAvailable,
  isLoading,
  isSelecting,
  refreshCameras,
  selectedDeviceId,
  selectionEnabled,
  selectCameraDevice,
  sourceRevision,
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
} = usePoseTracking(selectedCameraId, sourceRevision)
const {
  analysis,
  errorMessage: analysisErrorMessage,
  start: startVisionAnalysis,
  stop: stopVisionAnalysis,
} = useVisionAnalysis(selectedCameraId, sourceRevision)
const {
  errorMessage: objectPoseErrorMessage,
  objectPose,
  start: startObjectPoseTracking,
  stop: stopObjectPoseTracking,
} = useObjectPoseTracking(selectedCameraId, sourceRevision)
const {
  detection,
  errorMessage: detectionErrorMessage,
  isSelectingModel: isSelectingDetectionModel,
  selectModel: selectDetectionModel,
  start: startObjectDetectionTracking,
  stop: stopObjectDetectionTracking,
} = useObjectDetectionTracking(selectedCameraId, sourceRevision)

const streamImage = ref<HTMLImageElement | null>(null)

/** Returns whether a configured plugin is both selected and ready to serve cached results. */
function isPluginRunning(pluginId: string, uiKind: string): boolean {
  return plugins.value.some((plugin) => (
    (plugin.pluginId === pluginId || plugin.uiKind === uiKind)
    && plugin.enabled
    && plugin.state === 'running'
  ))
}

const visualPoseAnalysisEnabled = computed(() => isPluginRunning(
  'visual-pose-analysis',
  'visual-pose-analysis',
))
const objectPoseAnalysisEnabled = computed(() => isPluginRunning(
  'object-pose-analysis',
  'object-pose-analysis',
))
const objectDetectionAnalysisEnabled = computed(() => isPluginRunning(
  'object-detection-analysis',
  'object-detection-analysis',
))

/** Binds passive browser polling to the persisted lifecycle of the visual-pose plugin. */
watch(visualPoseAnalysisEnabled, (enabled) => {
  if (enabled) {
    startPoseTracking()
    startVisionAnalysis()
    return
  }
  stopPoseTracking()
  stopVisionAnalysis()
}, { immediate: true })

/** Stops reading and clears the overlay as soon as the object-pose plugin is disabled. */
watch(objectPoseAnalysisEnabled, (enabled) => {
  if (enabled) {
    startObjectPoseTracking()
    return
  }
  stopObjectPoseTracking()
}, { immediate: true })

/** Stops reading and clears the overlay as soon as the detector plugin is disabled. */
watch(objectDetectionAnalysisEnabled, (enabled) => {
  if (enabled) {
    startObjectDetectionTracking()
    return
  }
  stopObjectDetectionTracking()
}, { immediate: true })

function requestPluginActivation(pluginId: string, enabled: boolean): void {
  void setPluginActivation(pluginId, enabled)
}

function requestPluginReload(pluginId: string): void {
  void reload([pluginId])
}

function requestAllPluginReloads(): void {
  void reload(reloadablePlugins.value.map((plugin) => plugin.pluginId))
}

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

/** Object contours are independent of human pose and disappear on any unsafe snapshot. */
const objectPoseForOverlay = computed(() => {
  const currentObjectPose = objectPose.value
  if (
    currentObjectPose === null ||
    !currentObjectPose.enabled ||
    !currentObjectPose.valid ||
    !currentObjectPose.overlayFresh ||
    currentObjectPose.objects.length !== 1
  ) {
    return null
  }
  return currentObjectPose
})

/** Hide semantic boxes whenever the server cannot prove freshness for the live MJPEG frame. */
const detectionForOverlay = computed(() => {
  const currentDetection = detection.value
  if (
    currentDetection === null ||
    !currentDetection.enabled ||
    !currentDetection.valid ||
    !currentDetection.overlayFresh ||
    currentDetection.detections.length === 0
  ) {
    return null
  }
  return currentDetection
})

onMounted(() => {
  void start()
  void refreshPlugins()
})

onBeforeUnmount(() => {
  stopObjectPoseTracking()
  stopObjectDetectionTracking()
  stopVisionAnalysis()
  stopPoseTracking()
  stop()
})
</script>

<template>
  <div class="min-h-0 w-full flex-1 overflow-y-auto bg-slate-900 xl:overflow-hidden">
    <div class="grid min-h-full grid-cols-1 xl:h-full xl:grid-cols-[minmax(17rem,22rem)_minmax(0,1fr)_minmax(23rem,30rem)]">
      <PluginWorkspace
        :camera-devices="devices"
        :camera-selection-enabled="selectionEnabled"
        :plugins="plugins"
        :plugin-error-message="pluginErrorMessage"
        :is-plugin-catalog-loading="isLoadingPlugins"
        :is-activating-plugin="isActivatingPlugin"
        :activating-plugin-ids="activatingPluginIds"
        :is-reloading-plugin="isReloading"
        :reloading-plugin-ids="reloadingPluginIds"
        :is-selecting-camera="isSelecting"
        :selected-camera-device-id="selectedDeviceId"
        :pose="pose"
        :pose-error-message="poseErrorMessage"
        :is-updating-target="isUpdatingTarget"
        :tracking-enabled="trackingEnabled"
        :analysis="analysis"
        :analysis-error-message="analysisErrorMessage"
        :object-pose="objectPose"
        :object-pose-error-message="objectPoseErrorMessage"
        :detection="detection"
        :detection-error-message="detectionErrorMessage"
        :is-selecting-detection-model="isSelectingDetectionModel"
        @set-plugin-activation="requestPluginActivation"
        @reload-plugin="requestPluginReload"
        @reload-all-plugins="requestAllPluginReloads"
        @select-camera-device="selectCameraDevice"
        @select-detection-model="selectDetectionModel"
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
          <RecordingToolbar :camera-id="camera?.cameraId || 'hikvision-usb'" />

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
            <ObjectPoseOverlay
              v-if="objectPoseForOverlay !== null"
              data-testid="object-pose-overlay"
              :object-pose="objectPoseForOverlay"
              :image-element="streamImage"
            />
            <ObjectDetectionOverlay
              v-if="detectionForOverlay !== null"
              data-testid="object-detection-overlay"
              :detection="detectionForOverlay"
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
          <p
            v-if="objectPose?.enabled && objectPose.capturedAt !== null && !objectPose.overlayFresh"
            data-testid="object-pose-overlay-stale"
            class="absolute bottom-12 left-1/2 -translate-x-1/2 rounded bg-slate-950/85 px-3 py-1.5 text-xs text-slate-300"
            role="status"
          >
            工件结果已过期，保持实时画面
          </p>
          <p
            v-if="detection?.enabled && detection.capturedAt !== null && !detection.overlayFresh"
            data-testid="object-detection-overlay-stale"
            class="absolute bottom-20 left-1/2 -translate-x-1/2 rounded bg-slate-950/85 px-3 py-1.5 text-xs text-slate-300"
            role="status"
          >
            物品检测结果已过期，保持实时画面
          </p>
          <div v-if="!streamUrl" class="flex flex-col items-center p-6 text-center" role="status">
            <div class="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-slate-800 border-t-slate-500"></div>
            <p class="text-xs text-slate-500">正在等待相机画面...</p>
          </div>
          <div
            v-if="isSelecting || (!frameAvailable && streamUrl)"
            class="absolute inset-0 flex flex-col items-center justify-center bg-slate-950/90 backdrop-blur-sm"
            role="status"
          >
            <div class="mb-3 h-8 w-8 animate-spin rounded-full border-2 border-slate-800 border-t-slate-400"></div>
            <p class="text-xs text-slate-400">{{ isSelecting ? '正在切换采集设备...' : '正在等待所选相机画面...' }}</p>
          </div>
        </div>

        <p v-if="errorMessage" class="mt-3 shrink-0 rounded border border-rose-900/40 bg-rose-950/20 px-3 py-2 text-xs text-rose-400" role="alert">
          {{ errorMessage }}
        </p>
      </section>

      <aside class="flex min-h-[28rem] flex-col border-t border-slate-800 bg-slate-950 xl:min-h-0 xl:overflow-y-auto xl:border-l xl:border-t-0" aria-label="硬件适配器">
        <header class="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
          <div class="px-4 pt-3 pb-1">
            <p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Adapter</p>
            <h2 class="mt-0.5 text-sm font-bold text-slate-100">硬件模块</h2>
          </div>
          <!-- 顶部横向切换选项卡 -->
          <nav class="flex border-t border-slate-900 bg-slate-900/40 px-2" aria-label="硬件选项卡">
            <button
              type="button"
              class="flex-1 py-2 text-center text-xs font-bold transition border-b-2"
              :class="activeAdapterTab === 'camera' ? 'border-sky-500 text-sky-400 bg-slate-900/60' : 'border-transparent text-slate-400 hover:text-slate-200'"
              @click="activeAdapterTab = 'camera'"
            >
              相机适配器
            </button>
            <button
              type="button"
              class="flex-1 py-2 text-center text-xs font-bold transition border-b-2"
              :class="activeAdapterTab === 'gripper' ? 'border-sky-500 text-sky-400 bg-slate-900/60' : 'border-transparent text-slate-400 hover:text-slate-200'"
              @click="activeAdapterTab = 'gripper'"
            >
              夹爪适配器
            </button>
            <button
              type="button"
              class="flex-1 py-2 text-center text-xs font-bold transition border-b-2"
              :class="activeAdapterTab === 'robot' ? 'border-sky-500 text-sky-400 bg-slate-900/60' : 'border-transparent text-slate-400 hover:text-slate-200'"
              @click="activeAdapterTab = 'robot'"
            >
              机械臂适配器
            </button>
          </nav>
        </header>

        <div class="flex-1 overflow-y-auto">
          <HardwareAdapterSection v-show="activeAdapterTab === 'camera'" adapter-id="camera" label="相机适配器" test-id="camera-adapter-section">
            <CameraAdapterPanel
              :camera="camera"
              :devices="devices"
              :discovery-error="discoveryError"
              :is-loading="isLoading"
              :is-selecting="isSelecting"
              :selected-device-id="selectedDeviceId"
              :selection-enabled="selectionEnabled"
              :source-revision="sourceRevision"
              @reconnect="start"
              @refresh-cameras="refreshCameras"
              @select-device="selectCameraDevice"
            />
          </HardwareAdapterSection>
          <HardwareAdapterSection v-show="activeAdapterTab === 'gripper'" adapter-id="gripper" label="夹爪适配器" test-id="gripper-adapter">
            <GripperControlPanel />
          </HardwareAdapterSection>
          <HardwareAdapterSection v-show="activeAdapterTab === 'robot'" adapter-id="robot" label="机械臂适配器" test-id="robot-adapter">
            <RobotControlPanel />
          </HardwareAdapterSection>
        </div>
      </aside>
    </div>
  </div>
</template>
