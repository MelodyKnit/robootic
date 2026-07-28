<script setup lang="ts">
import { toRef } from 'vue'

import type { CameraParameter } from '../api/camera'
import { useCameraParameters } from '../composables/useCameraParameters'

const props = defineProps<{
  cameraId: string | null
}>()

const {
  applyLive,
  applyRestartChanges,
  errorMessage,
  hasRestartChanges,
  hasRestartParameters,
  isApplying,
  isLoading,
  isManualControlDisabled,
  parameters,
  setDraft,
  valueFor,
  writeEnabled,
} = useCameraParameters(toRef(props, 'cameraId'))

const parameterLabels: Record<string, string> = {
  exposure_auto: '自动曝光',
  exposure_time_us: '曝光时间',
  gain_auto: '自动增益',
  gain_db: '增益',
  frame_rate_enabled: '帧率控制',
  frame_rate_fps: '帧率',
  pixel_format: '像素格式',
}

/** Resolves a compact localized label while retaining a readable fallback for future adapters. */
function parameterLabel(parameter: CameraParameter): string {
  return parameterLabels[parameter.key] ?? parameter.key
}

/** Preserves exact device values when MVS does not report a writable float increment. */
function sliderStep(parameter: CameraParameter): number | 'any' {
  if (parameter.step !== null && parameter.step > 0) {
    return parameter.step
  }
  return 'any'
}

function numericValue(parameter: CameraParameter): number {
  const value = valueFor(parameter)
  return typeof value === 'number' ? value : 0
}

function enumValue(parameter: CameraParameter): string {
  const value = valueFor(parameter)
  return typeof value === 'string' ? value : ''
}

function booleanValue(parameter: CameraParameter): boolean {
  const value = valueFor(parameter)
  return typeof value === 'boolean' && value
}

function updateNumberDraft(parameter: CameraParameter, event: Event): void {
  const value = Number((event.target as HTMLInputElement).value)
  if (Number.isFinite(value)) {
    setDraft(parameter, value)
  }
}

function updateEnumDraft(parameter: CameraParameter, event: Event): void {
  setDraft(parameter, (event.target as HTMLSelectElement).value)
}

function updateBooleanDraft(parameter: CameraParameter, event: Event): void {
  setDraft(parameter, (event.target as HTMLInputElement).checked)
}

function commitLiveParameter(parameter: CameraParameter, event: Event): void {
  if (event.isTrusted && parameter.applyMode === 'live') {
    void applyLive(parameter)
  }
}

function unitLabel(parameter: CameraParameter): string {
  return parameter.unit === null ? '' : parameter.unit
}

function controlDisabled(parameter: CameraParameter): boolean {
  return parameter.applyMode === 'live'
    ? isManualControlDisabled(parameter)
    : isApplying.value || !writeEnabled.value
}
</script>

<template>
  <section v-if="cameraId !== null" class="p-5 flex flex-col bg-slate-950 text-slate-300 select-none" aria-label="相机参数">
    <div class="mb-4 shrink-0 flex items-center justify-between">
      <div>
        <h2 class="text-xs font-bold text-slate-500 uppercase tracking-widest">相机参数</h2>
        <p v-if="isLoading" class="text-xs text-amber-500 font-medium mt-1">正在连接获取设备参数...</p>
        <p v-else-if="!writeEnabled && parameters.length > 0" class="text-xs text-slate-500 mt-1">
          当前配置为只读
        </p>
      </div>
    </div>

    <!-- Error feedback. -->
    <p v-if="errorMessage" class="mb-4 text-xs text-rose-400 bg-rose-950/20 border border-rose-900/40 rounded py-2 px-3 shrink-0" role="alert">
      {{ errorMessage }}
    </p>

    <!-- Scrollable parameter form. -->
    <div v-else-if="parameters.length > 0" class="space-y-4">
      <div v-for="parameter in parameters" :key="parameter.key" class="pb-4 border-b border-slate-900 last:border-0">
        <div class="flex justify-between items-baseline mb-2">
          <label :for="`camera-parameter-${parameter.key}`" class="text-xs font-bold text-slate-400">
            {{ parameterLabel(parameter) }}
          </label>
          <span
            class="text-[9px] font-bold px-1.5 py-0.5 rounded border"
            :class="parameter.applyMode === 'live' ? 'bg-emerald-950/50 text-emerald-400 border-emerald-900/50' : 'bg-amber-950/50 text-amber-400 border-amber-900/50'"
          >
            {{ parameter.applyMode === 'live' ? '实时保存' : '保存重连' }}
          </span>
        </div>

        <!-- Floating-point parameter control. -->
        <template v-if="parameter.kind === 'float'">
          <div class="flex items-center gap-3">
            <input
              :id="`camera-parameter-${parameter.key}`"
              class="flex-1 h-1 bg-slate-900 rounded-lg appearance-none cursor-pointer accent-emerald-500 hover:accent-emerald-400 transition"
              type="range"
              :min="parameter.minimum ?? undefined"
              :max="parameter.maximum ?? undefined"
              :step="sliderStep(parameter)"
              :value="numericValue(parameter)"
              :disabled="controlDisabled(parameter)"
              @input="updateNumberDraft(parameter, $event)"
              @change="commitLiveParameter(parameter, $event)"
            />
            <div class="relative shrink-0 w-24">
              <input
                :id="`camera-parameter-value-${parameter.key}`"
                :aria-label="`${parameterLabel(parameter)}数值`"
                class="w-full text-right pr-6 py-1 text-xs font-mono font-bold text-slate-200 bg-slate-900/50 border border-slate-800 rounded focus:outline-none focus:ring-1 focus:ring-slate-700 hover:border-slate-700 disabled:opacity-50 transition"
                type="number"
                :min="parameter.minimum ?? undefined"
                :max="parameter.maximum ?? undefined"
                :step="sliderStep(parameter)"
                :value="numericValue(parameter)"
                :disabled="controlDisabled(parameter)"
                @input="updateNumberDraft(parameter, $event)"
                @change="commitLiveParameter(parameter, $event)"
              />
              <span v-if="unitLabel(parameter)" class="absolute right-2 top-1/2 -translate-y-1/2 text-[9px] font-bold text-slate-500 uppercase">
                {{ unitLabel(parameter) }}
              </span>
            </div>
          </div>
        </template>

        <!-- Enumerated parameter control. -->
        <select
          v-else-if="parameter.kind === 'enum'"
          :id="`camera-parameter-${parameter.key}`"
          class="w-full py-1.5 px-3 text-xs font-semibold text-slate-300 bg-slate-900/50 border border-slate-800 rounded focus:outline-none focus:ring-1 focus:ring-slate-700 hover:border-slate-700 disabled:opacity-50 transition cursor-pointer"
          :value="enumValue(parameter)"
          :disabled="controlDisabled(parameter)"
          @change="updateEnumDraft(parameter, $event); commitLiveParameter(parameter, $event)"
        >
          <option v-for="option in parameter.options" :key="option" :value="option" class="bg-slate-950 text-slate-300 font-semibold">{{ option }}</option>
        </select>

        <!-- Boolean parameter control. -->
        <label v-else class="flex items-center gap-3 cursor-pointer select-none" :for="`camera-parameter-${parameter.key}`">
          <input
            :id="`camera-parameter-${parameter.key}`"
            type="checkbox"
            class="w-3.5 h-3.5 text-emerald-600 bg-slate-900 border-slate-800 rounded focus:ring-emerald-500 accent-emerald-500"
            :checked="booleanValue(parameter)"
            :disabled="controlDisabled(parameter)"
            @change="updateBooleanDraft(parameter, $event); commitLiveParameter(parameter, $event)"
          />
          <span class="text-xs font-semibold text-slate-400">
            {{ booleanValue(parameter) ? '已激活' : '非活动状态' }}
          </span>
        </label>
      </div>
    </div>

    <!-- Explicit action area for settings that require acquisition restart. -->
    <div v-if="hasRestartParameters" class="mt-5 pt-4 border-t border-slate-900 shrink-0">
      <button
        class="w-full py-2 px-4 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs shadow-md shadow-emerald-500/10 hover:shadow-emerald-500/20 active:scale-[0.99] transition disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none focus:outline-none"
        type="button"
        :disabled="isApplying || !writeEnabled || !hasRestartChanges"
        @click="applyRestartChanges"
      >
        {{ isApplying ? '正在重启并保存参数...' : '保存并重新采集' }}
      </button>
    </div>
  </section>
</template>
