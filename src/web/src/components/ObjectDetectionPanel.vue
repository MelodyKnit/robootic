<script setup lang="ts">
import { computed } from 'vue'

import type { DetectionTracking } from '../api/camera'

const props = defineProps<{
  detection: DetectionTracking | null
  errorMessage: string | null
  isSelectingModel: boolean
}>()

const emit = defineEmits<{
  selectModel: [modelId: string]
}>()

const detectionCount = computed(() => props.detection?.detections.length ?? 0)
const statusText = computed(() => {
  if (props.errorMessage !== null) {
    return props.errorMessage
  }
  if (props.detection === null) {
    return '正在读取检测状态'
  }
  if (!props.detection.enabled) {
    return '识别未启用'
  }
  if (props.detection.valid && props.detection.overlayFresh) {
    return `${detectionCount.value} 个目标`
  }
  return detectionReasonLabel(props.detection.reason)
})
const statusClass = computed(() => {
  if (props.errorMessage !== null) {
    return 'text-rose-300'
  }
  if (props.detection?.valid && props.detection.overlayFresh) {
    return 'text-emerald-400'
  }
  return 'text-amber-400'
})
const latencyText = computed(() => {
  const latency = props.detection?.inferenceLatencyMs
  return latency === null || latency === undefined ? '暂无' : `${Math.round(latency)} ms`
})
const selectedModelId = computed(() => props.detection?.selectedModelId ?? '')
const overlayValid = computed(() => props.detection?.valid === true && props.detection.overlayFresh)

function detectionReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    object_detection_disabled: '识别未启用',
    object_detection_waiting_for_frame: '等待相机画面',
    detection_model_unavailable: '检测模型不可用',
    detection_model_selection_disabled: '模型选择已禁用',
    detection_inference_failed: '模型推理失败',
    no_detections: '未检测到目标',
    stale_result: '检测结果已过期',
    camera_binding_mismatch: '插件相机绑定不匹配',
  }
  return labels[reason] ?? reason
}
</script>

<template>
  <section class="shrink-0 border-b border-slate-900 p-5" aria-label="通用物品检测">
    <div class="mb-3 flex items-center justify-between gap-3">
      <h3 class="text-[10px] font-bold uppercase tracking-widest text-slate-500">物品检测</h3>
      <span class="shrink-0 text-[10px] font-semibold" :class="statusClass">
        {{ overlayValid ? '有效' : '待确认' }}
      </span>
    </div>

    <div>
      <label class="block text-[10px] text-slate-500" :for="'detection-model-selector'">
        检测模型
        <select
          id="detection-model-selector"
          data-testid="detection-model-selector-object-detection-analysis"
          class="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] text-slate-200 outline-none transition focus:border-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          :value="selectedModelId"
          :disabled="isSelectingModel || !detection?.enabled || detection?.models.length === 0"
          @change="emit('selectModel', ($event.target as HTMLSelectElement).value)"
        >
          <option v-if="detection?.models.length === 0" value="">暂无已注册模型</option>
          <option
            v-for="model in detection?.models ?? []"
            :key="model.modelId"
            :value="model.modelId"
            :disabled="!model.available"
          >
            {{ model.displayName }} · {{ model.provider }}{{ model.available ? '' : '（不可用）' }}
          </option>
        </select>
      </label>
    </div>

    <dl class="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
      <div>
        <dt class="text-slate-500">目标数量</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ detectionCount }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">推理延迟</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ latencyText }}</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">当前模型</dt>
        <dd class="mt-0.5 truncate font-mono font-semibold text-slate-300" :title="selectedModelId">
          {{ selectedModelId || '暂无' }}
        </dd>
      </div>
    </dl>

    <p class="mt-3 text-[11px] leading-4" :class="statusClass" :role="errorMessage ? 'alert' : 'status'">
      {{ statusText }}
    </p>
  </section>
</template>
