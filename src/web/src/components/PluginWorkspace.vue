<script setup lang="ts">
import { computed, onMounted } from 'vue'

import type { PoseTracking, VisionAnalysis } from '../api/camera'
import type { RuntimePlugin } from '../api/plugins'
import { usePluginCatalog } from '../composables/usePluginCatalog'
import PoseTargetPanel from './PoseTargetPanel.vue'
import VisionAnalysisPanel from './VisionAnalysisPanel.vue'

const props = defineProps<{
  analysis: VisionAnalysis | null
  analysisErrorMessage: string | null
  isUpdatingTarget: boolean
  pose: PoseTracking | null
  poseErrorMessage: string | null
  trackingEnabled: boolean
}>()

const emit = defineEmits<{
  selectTarget: [targetJoint: string]
}>()

const {
  errorMessage,
  isLoading,
  isReloading,
  plugins,
  reloadingPluginIds,
  reload,
  reloadablePlugins,
  refresh,
} = usePluginCatalog()

const canReloadAll = computed(() => reloadablePlugins.value.length > 0 && !isReloading.value)

onMounted(() => {
  void refresh()
})

/** Keeps the trusted visual panel isolated from future registered plugin panels. */
function isVisualPosePlugin(plugin: RuntimePlugin): boolean {
  return plugin.pluginId === 'visual-pose-analysis' || plugin.uiKind === 'visual-pose-analysis'
}

function isReloadingPlugin(pluginId: string): boolean {
  return reloadingPluginIds.value.includes(pluginId)
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
  if (plugin.state === 'running') {
    return 'border-emerald-900/60 bg-emerald-950/30 text-emerald-300'
  }
  return 'border-amber-900/60 bg-amber-950/30 text-amber-300'
}

function capabilityLabel(capability: string): string {
  const labels: Record<string, string> = {
    frame_consumer: '帧消费',
    pose_tracking: '姿态追踪',
    vision_analysis: '视觉分析',
  }
  return labels[capability] ?? capability
}

function requestPluginReload(pluginId: string): void {
  void reload([pluginId])
}

function requestAllReloads(): void {
  void reload(reloadablePlugins.value.map((plugin) => plugin.pluginId))
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
        {{ isReloading ? '重载中' : '重载全部' }}
      </button>
    </header>

    <p v-if="isLoading && plugins.length === 0" class="px-4 py-3 text-xs text-slate-500" role="status">正在读取插件状态...</p>
    <p v-if="errorMessage" class="mx-4 mt-3 rounded border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-xs text-rose-300" role="alert">
      {{ errorMessage }}
    </p>
    <p v-if="!isLoading && !errorMessage && plugins.length === 0" class="px-4 py-3 text-xs text-slate-500">
      当前没有已配置的功能模块。
    </p>

    <div class="space-y-3 p-4">
      <article
        v-for="plugin in plugins"
        :key="plugin.pluginId"
        class="overflow-hidden rounded-lg border border-slate-800 bg-slate-900/45"
        :data-testid="`plugin-card-${plugin.pluginId}`"
      >
        <div class="flex items-start justify-between gap-3 p-3">
          <div class="min-w-0">
            <h3 class="truncate text-xs font-bold text-slate-100" :title="plugin.name">{{ plugin.name }}</h3>
            <p class="mt-1 truncate font-mono text-[10px] text-slate-500">{{ plugin.pluginId }} · v{{ plugin.version }}</p>
          </div>
          <span class="shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold" :class="stateClass(plugin)">
            {{ isReloadingPlugin(plugin.pluginId) ? '重载中' : stateLabel(plugin.state) }}
          </span>
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

        <details
          v-if="isVisualPosePlugin(plugin)"
          class="group"
          :data-testid="`plugin-details-${plugin.pluginId}`"
        >
          <summary class="cursor-pointer list-none px-3 py-2.5 text-[11px] font-semibold text-sky-300 marker:hidden hover:bg-slate-800/65">
            <span class="group-open:hidden">展开姿态与成像详情</span>
            <span class="hidden group-open:inline">收起姿态与成像详情</span>
          </summary>
          <div class="border-t border-slate-800">
            <PoseTargetPanel
              :pose="props.pose"
              :error-message="props.poseErrorMessage"
              :is-updating-target="props.isUpdatingTarget"
              :tracking-enabled="props.trackingEnabled"
              @select-target="emit('selectTarget', $event)"
            />
            <VisionAnalysisPanel :analysis="props.analysis" :error-message="props.analysisErrorMessage" />
          </div>
        </details>

        <div v-else class="px-3 py-2.5 text-[11px] text-slate-500">该已注册模块暂未提供专用网页面板。</div>

        <div class="flex justify-end border-t border-slate-800 px-3 py-2">
          <button
            class="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[10px] font-semibold text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
            type="button"
            :aria-label="`重载 ${plugin.name}`"
            :disabled="!plugin.reloadable || isReloading"
            @click="requestPluginReload(plugin.pluginId)"
          >
            {{ isReloadingPlugin(plugin.pluginId) ? '重载中' : '重载' }}
          </button>
        </div>
      </article>
    </div>
  </aside>
</template>
