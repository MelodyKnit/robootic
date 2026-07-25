<script setup lang="ts">
import { computed } from 'vue'

import type { JointVisibilityState, VisionAnalysis } from '../api/camera'

const props = defineProps<{
  analysis: VisionAnalysis | null
  errorMessage: string | null
}>()

const warningLabels: Record<string, string> = {
  resolution_low: '分辨率偏低',
  brightness_low: '画面偏暗',
  brightness_high: '画面偏亮',
  contrast_low: '对比度偏低',
  sharpness_low: '清晰度偏低',
  frame_unavailable: '图像不可用',
  frame_dimensions_invalid: '图像尺寸无效',
  mono_payload_size_invalid: '单色图像数据异常',
  rgb_payload_size_invalid: '彩色图像数据异常',
  pixel_format_unsupported: '像素格式不支持',
}

const jointStateLabels: Record<JointVisibilityState, string> = {
  detected: '可见',
  low_confidence: '置信度低',
  out_of_frame: '画面外',
  unavailable: '不可用',
}

const jointStateClasses: Record<JointVisibilityState, string> = {
  detected: 'text-emerald-400',
  low_confidence: 'text-amber-400',
  out_of_frame: 'text-rose-400',
  unavailable: 'text-slate-500',
}

const frameShape = computed(() => {
  const frame = props.analysis?.frame
  if (frame === null || frame === undefined || frame.width === null || frame.height === null) {
    return '暂无'
  }
  return `${frame.width} x ${frame.height}`
})

const pixelFormat = computed(() => props.analysis?.frame?.pixelFormat?.toUpperCase() ?? '暂无')
const visibleJointCount = computed(() => props.analysis?.visibleJointNames.length ?? 0)
const qualityWarnings = computed(() => props.analysis?.frame?.warnings ?? [])
const primaryPersonState = computed(() => {
  if (props.analysis === null || props.analysis.selectedPerson === null) {
    return '暂无主人体框'
  }
  return '已显示主人体框'
})

function metric(value: number | null | undefined): string {
  return value === null || value === undefined ? '暂无' : value.toFixed(1)
}

function warningLabel(value: string): string {
  return warningLabels[value] ?? value
}
</script>

<template>
  <section class="p-5 border-b border-slate-900 shrink-0" aria-label="成像与识别">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">成像与识别</h3>
      <span
        class="text-[10px] font-semibold"
        :class="analysis?.personCount ? 'text-emerald-400' : 'text-slate-400'"
      >
        {{ analysis?.personCount ? '检测到人员' : '未检测到人员' }}
      </span>
    </div>

    <dl class="grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
      <div>
        <dt class="text-slate-500">图像格式</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ pixelFormat }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">图像尺寸</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ frameShape }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">亮度 / 对比度</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">
          {{ metric(analysis?.frame?.brightnessMean) }} / {{ metric(analysis?.frame?.contrast) }}
        </dd>
      </div>
      <div>
        <dt class="text-slate-500">清晰度</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ metric(analysis?.frame?.sharpness) }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">检测人数</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ analysis?.personCount ?? 0 }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">可见关节</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ visibleJointCount }} / 17</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">主人体框</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ primaryPersonState }}</dd>
      </div>
    </dl>

    <div v-if="qualityWarnings.length" class="mt-3 flex flex-wrap gap-1.5" aria-label="成像警告">
      <span
        v-for="warning in qualityWarnings"
        :key="warning"
        class="rounded border border-amber-900/60 bg-amber-950/30 px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
      >
        {{ warningLabel(warning) }}
      </span>
    </div>

    <div v-if="analysis?.jointVisibility.length" class="mt-3 border-t border-slate-800 pt-3">
      <p class="text-[10px] font-semibold text-slate-500 mb-2">关节可见性</p>
      <div class="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[10px]">
        <div v-for="joint in analysis.jointVisibility" :key="joint.name" class="flex justify-between gap-2 min-w-0">
          <span class="truncate text-slate-400">{{ joint.name }}</span>
          <span class="shrink-0 font-medium" :class="jointStateClasses[joint.state]">
            {{ jointStateLabels[joint.state] }}
          </span>
        </div>
      </div>
    </div>

    <p v-if="errorMessage" class="mt-3 text-[11px] leading-4 text-rose-300" role="alert">{{ errorMessage }}</p>
    <p v-else-if="analysis && !analysis.poseEnabled" class="mt-3 text-[11px] leading-4 text-slate-500">
      当前只显示成像质量；姿态识别尚未在本机配置中启用。
    </p>
  </section>
</template>
