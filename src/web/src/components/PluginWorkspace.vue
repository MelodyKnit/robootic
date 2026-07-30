<script setup lang="ts">
import { computed } from 'vue'

import type { CameraDevice, DetectionTracking, ObjectPoseTracking, PoseTracking, VisionAnalysis } from '../api/camera'
import type { RuntimePlugin } from '../api/plugins'
import CameraBindingPanel from './CameraBindingPanel.vue'
import ObjectDetectionPanel from './ObjectDetectionPanel.vue'
import ObjectPosePanel from './ObjectPosePanel.vue'
import PoseTargetPanel from './PoseTargetPanel.vue'
import VisionAnalysisPanel from './VisionAnalysisPanel.vue'

const props = defineProps<{
  activatingPluginIds: string[]
  analysis: VisionAnalysis | null
  analysisErrorMessage: string | null
  cameraDevices: CameraDevice[]
  cameraSelectionEnabled: boolean
  detection: DetectionTracking | null
  detectionErrorMessage: string | null
  isActivatingPlugin: boolean
  isPluginCatalogLoading: boolean
  isReloadingPlugin: boolean
  isSelectingCamera: boolean
  isSelectingDetectionModel: boolean
  isUpdatingTarget: boolean
  objectPose: ObjectPoseTracking | null
  objectPoseErrorMessage: string | null
  pluginErrorMessage: string | null
  plugins: RuntimePlugin[]
  pose: PoseTracking | null
  poseErrorMessage: string | null
  reloadingPluginIds: string[]
  selectedCameraDeviceId: string | null
  trackingEnabled: boolean
}>()

const emit = defineEmits<{
  reloadAllPlugins: []
  reloadPlugin: [pluginId: string]
  selectCameraDevice: [deviceId: string]
  selectDetectionModel: [modelId: string]
  selectTarget: [targetJoint: string]
  setPluginActivation: [pluginId: string, enabled: boolean]
}>()

const canReloadAll = computed(() => (
  props.plugins.some((plugin) => plugin.enabled && plugin.reloadable)
  && !props.isReloadingPlugin
  && !props.isActivatingPlugin
))

/** Keeps the trusted visual panel isolated from future registered plugin panels. */
function isVisualPosePlugin(plugin: RuntimePlugin): boolean {
  return plugin.pluginId === 'visual-pose-analysis' || plugin.uiKind === 'visual-pose-analysis'
}

/** Keeps known-workpiece data separate from the human-pose plugin panel. */
function isObjectPosePlugin(plugin: RuntimePlugin): boolean {
  return plugin.pluginId === 'object-pose-analysis' || plugin.uiKind === 'object-pose-analysis'
}

/** Keeps semantic model detections independent from calibrated known-workpiece pose. */
function isObjectDetectionPlugin(plugin: RuntimePlugin): boolean {
  return plugin.pluginId === 'object-detection-analysis' || plugin.uiKind === 'object-detection-analysis'
}

/** A binding declaration makes a plugin detail pane useful even without a custom result panel. */
function hasPluginDetails(plugin: RuntimePlugin): boolean {
  return plugin.cameraBinding !== null
    || isVisualPosePlugin(plugin)
    || isObjectPosePlugin(plugin)
    || isObjectDetectionPlugin(plugin)
}

function detailsLabel(plugin: RuntimePlugin, expanded: boolean): string {
  const action = expanded ? '收起' : '展开'
  if (isVisualPosePlugin(plugin)) {
    return `${action}姿态与成像详情`
  }
  if (isObjectPosePlugin(plugin)) {
    return `${action}工件位姿详情`
  }
  if (isObjectDetectionPlugin(plugin)) {
    return `${action}工件检测详情`
  }
  return `${action}相机输入详情`
}

function isReloadingPlugin(pluginId: string): boolean {
  return props.reloadingPluginIds.includes(pluginId)
}

function isPluginActivationPending(pluginId: string): boolean {
  return props.activatingPluginIds.includes(pluginId)
}

function pluginIsActive(plugin: RuntimePlugin): boolean {
  return plugin.enabled && plugin.state === 'running'
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    starting: '启动中',
    running: '运行中',
    stopped: '已停止',
    failed: '异常',
    reloading: '重载中',
  }
  return labels[state] ?? state
}

function stateClass(plugin: RuntimePlugin): string {
  if (plugin.error !== null || plugin.state === 'failed') {
    return 'border-rose-900/60 bg-rose-950/30 text-rose-300'
  }
  if (pluginIsActive(plugin)) {
    return 'border-emerald-900/60 bg-emerald-950/30 text-emerald-300'
  }
  return 'border-amber-900/60 bg-amber-950/30 text-amber-300'
}

function capabilityLabel(capability: string): string {
  const labels: Record<string, string> = {
    frame_consumer: '帧消费',
    object_pose: '工件位姿',
    'object-detection': '物品检测',
    object_detection: '物品检测',
    'bounding-boxes': '框选叠加',
    pose_tracking: '姿态追踪',
    vision_analysis: '视觉分析',
  }
  return labels[capability] ?? capability
}

function requestPluginReload(pluginId: string): void {
  emit('reloadPlugin', pluginId)
}

function requestAllReloads(): void {
  emit('reloadAllPlugins')
}

/** Emits the requested persisted lifecycle state; the parent owns the server request. */
function requestPluginActivation(plugin: RuntimePlugin, event: Event): void {
  emit('setPluginActivation', plugin.pluginId, (event.target as HTMLInputElement).checked)
}
</script>

<template>
  <aside
    class="flex min-h-[20rem] flex-col border-b border-slate-800 bg-slate-950 xl:min-h-0 xl:border-b-0 xl:border-r xl:overflow-y-auto"
    aria-label="功能插件"
    data-testid="plugin-workspace"
  >
    <header class="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/95 px-4 py-3 backdrop-blur">
      <div>
        <p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Plugin</p>
        <h2 class="mt-0.5 text-sm font-bold text-slate-100">功能模块</h2>
      </div>
      <button
        class="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
        type="button"
        :disabled="!canReloadAll"
        @click="requestAllReloads"
      >
        {{ props.isReloadingPlugin ? '重载中' : '重载全部' }}
      </button>
    </header>

    <p v-if="props.isPluginCatalogLoading && props.plugins.length === 0" class="px-4 py-3 text-xs text-slate-500" role="status">正在读取插件状态...</p>
    <p v-if="props.pluginErrorMessage" class="mx-4 mt-3 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-300" role="alert">
      {{ props.pluginErrorMessage }}
    </p>
    <p v-if="!props.isPluginCatalogLoading && !props.pluginErrorMessage && props.plugins.length === 0" class="px-4 py-3 text-xs text-slate-500">
      当前没有已配置的功能模块。
    </p>

    <div class="space-y-3 p-4">
      <article
        v-for="plugin in props.plugins"
        :key="plugin.pluginId"
        class="overflow-hidden rounded-lg border transition"
        :class="pluginIsActive(plugin) ? 'border-slate-800 bg-slate-900/45' : 'border-slate-900 bg-slate-950/60 opacity-80'"
        :data-testid="`plugin-card-${plugin.pluginId}`"
      >
        <div class="flex items-start justify-between gap-3 p-3">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <h3 class="truncate text-xs font-bold text-slate-100" :title="plugin.name">{{ plugin.name }}</h3>
              <span
                class="shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold"
                :class="isPluginActivationPending(plugin.pluginId) ? 'border-amber-900/60 bg-amber-950/30 text-amber-300' : stateClass(plugin)"
              >
                {{ isPluginActivationPending(plugin.pluginId) ? '正在更新' : (isReloadingPlugin(plugin.pluginId) ? '重载中' : (plugin.state === 'failed' ? '异常' : (plugin.enabled ? stateLabel(plugin.state) : '已关闭'))) }}
              </span>
            </div>
            <p class="mt-1 truncate font-mono text-[10px] text-slate-500">{{ plugin.pluginId }} · v{{ plugin.version }}</p>
          </div>
          <label
            class="relative inline-flex items-center"
            :class="plugin.lifecycleControllable ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'"
            :title="plugin.lifecycleControllable ? (plugin.enabled ? '关闭功能模块' : '开启功能模块') : '该模块的生命周期由部署配置管理'"
          >
            <input
              type="checkbox"
              class="peer sr-only"
              :data-testid="`plugin-activation-${plugin.pluginId}`"
              :checked="plugin.enabled"
              :disabled="!plugin.lifecycleControllable || isPluginActivationPending(plugin.pluginId) || props.isReloadingPlugin"
              @change="requestPluginActivation(plugin, $event)"
            />
            <div class="peer h-5 w-9 rounded-full bg-slate-800 after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:border after:border-slate-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-emerald-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none peer-disabled:opacity-50"></div>
          </label>
        </div>

        <div class="flex flex-wrap gap-1 border-y border-slate-800/80 px-3 py-2">
          <span v-for="capability in plugin.capabilities" :key="capability" class="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
            {{ capabilityLabel(capability) }}
          </span>
          <span v-if="plugin.capabilities.length === 0" class="text-[10px] text-slate-600">未声明能力</span>
        </div>

        <p v-if="plugin.error" class="border-b border-rose-900/40 bg-rose-950/20 px-3 py-2 text-[11px] text-rose-300" role="alert">
          {{ plugin.error.message }}
        </p>

        <template v-if="pluginIsActive(plugin)">
          <details
            v-if="hasPluginDetails(plugin)"
            class="group"
            :data-testid="`plugin-details-${plugin.pluginId}`"
          >
            <summary
              class="cursor-pointer list-none px-3 py-2.5 text-[11px] font-semibold marker:hidden hover:bg-slate-800/65"
              :class="isVisualPosePlugin(plugin) ? 'text-sky-300' : 'text-emerald-300'"
            >
              <span class="group-open:hidden">{{ detailsLabel(plugin, false) }}</span>
              <span class="hidden group-open:inline">{{ detailsLabel(plugin, true) }}</span>
            </summary>
            <div class="border-t border-slate-800">
              <CameraBindingPanel
                v-if="plugin.cameraBinding !== null"
                :binding="plugin.cameraBinding"
                :camera-devices="props.cameraDevices"
                :camera-selection-enabled="props.cameraSelectionEnabled"
                :is-selecting-camera="props.isSelectingCamera"
                :plugin-id="plugin.pluginId"
                :selected-camera-device-id="props.selectedCameraDeviceId"
                @select-device="emit('selectCameraDevice', $event)"
              />
              <PoseTargetPanel
                v-if="isVisualPosePlugin(plugin)"
                :pose="props.pose"
                :error-message="props.poseErrorMessage"
                :is-updating-target="props.isUpdatingTarget"
                :tracking-enabled="props.trackingEnabled"
                @select-target="emit('selectTarget', $event)"
              />
              <VisionAnalysisPanel
                v-if="isVisualPosePlugin(plugin)"
                :analysis="props.analysis"
                :error-message="props.analysisErrorMessage"
              />
              <ObjectPosePanel
                v-if="isObjectPosePlugin(plugin)"
                :object-pose="props.objectPose"
                :error-message="props.objectPoseErrorMessage"
              />
              <ObjectDetectionPanel
                v-else-if="isObjectDetectionPlugin(plugin)"
                :detection="props.detection"
                :error-message="props.detectionErrorMessage"
                :is-selecting-model="props.isSelectingDetectionModel"
                @select-model="emit('selectDetectionModel', $event)"
              />
            </div>
          </details>

          <div v-else class="px-3 py-2.5 text-[11px] text-slate-500">该已注册模块暂未提供专用网页面板。</div>
        </template>
        <p v-else class="px-3 py-2.5 text-[11px] text-slate-500" role="status">
          {{ plugin.state === 'failed' ? '模块未能启动；可修正错误后再次开启。' : (plugin.enabled ? '模块正在切换状态，暂不读取结果。' : '模块已关闭，已停止读取和显示该模块结果。') }}
        </p>

        <div class="flex justify-end border-t border-slate-800 px-3 py-2">
          <button
            class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[10px] font-semibold text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
            type="button"
            :aria-label="`重载 ${plugin.name}`"
            :disabled="!plugin.enabled || !plugin.reloadable || props.isReloadingPlugin || props.isActivatingPlugin"
            @click="requestPluginReload(plugin.pluginId)"
          >
            {{ isReloadingPlugin(plugin.pluginId) ? '重载中' : '重载' }}
          </button>
        </div>
      </article>
    </div>
  </aside>
</template>
