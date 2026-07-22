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

function applyModeLabel(parameter: CameraParameter): string {
  return parameter.applyMode === 'live' ? '立即生效并保存' : '保存并重新采集'
}
</script>

<template>
  <section v-if="cameraId !== null" class="parameter-panel" aria-label="相机参数">
    <div class="parameter-panel-header">
      <div>
        <h2>相机参数</h2>
        <p v-if="isLoading" class="parameter-state">正在读取</p>
        <p v-else-if="!writeEnabled && parameters.length > 0" class="parameter-state">本机配置已禁用写入</p>
      </div>
    </div>

    <p v-if="errorMessage" class="error-message" role="alert">{{ errorMessage }}</p>

    <div v-else-if="parameters.length > 0" class="parameter-grid">
      <div v-for="parameter in parameters" :key="parameter.key" class="parameter-field">
        <div class="parameter-label-row">
          <label :for="`camera-parameter-${parameter.key}`">{{ parameterLabel(parameter) }}</label>
          <span class="parameter-apply-mode">{{ applyModeLabel(parameter) }}</span>
        </div>

        <template v-if="parameter.kind === 'float'">
          <div class="numeric-control-row">
            <input
              :id="`camera-parameter-${parameter.key}`"
              class="parameter-range"
              type="range"
              :min="parameter.minimum ?? undefined"
              :max="parameter.maximum ?? undefined"
              :step="sliderStep(parameter)"
              :value="numericValue(parameter)"
              :disabled="controlDisabled(parameter)"
              @input="updateNumberDraft(parameter, $event)"
              @change="commitLiveParameter(parameter, $event)"
            />
            <input
              :id="`camera-parameter-value-${parameter.key}`"
              :aria-label="`${parameterLabel(parameter)}数值`"
              class="parameter-number"
              type="number"
              :min="parameter.minimum ?? undefined"
              :max="parameter.maximum ?? undefined"
              :step="sliderStep(parameter)"
              :value="numericValue(parameter)"
              :disabled="controlDisabled(parameter)"
              @input="updateNumberDraft(parameter, $event)"
              @change="commitLiveParameter(parameter, $event)"
            />
            <span v-if="unitLabel(parameter)" class="parameter-unit">{{ unitLabel(parameter) }}</span>
          </div>
        </template>

        <select
          v-else-if="parameter.kind === 'enum'"
          :id="`camera-parameter-${parameter.key}`"
          class="parameter-select"
          :value="enumValue(parameter)"
          :disabled="controlDisabled(parameter)"
          @change="updateEnumDraft(parameter, $event); commitLiveParameter(parameter, $event)"
        >
          <option v-for="option in parameter.options" :key="option" :value="option">{{ option }}</option>
        </select>

        <label v-else class="parameter-toggle" :for="`camera-parameter-${parameter.key}`">
          <input
            :id="`camera-parameter-${parameter.key}`"
            type="checkbox"
            :checked="booleanValue(parameter)"
            :disabled="controlDisabled(parameter)"
            @change="updateBooleanDraft(parameter, $event); commitLiveParameter(parameter, $event)"
          />
          <span>{{ booleanValue(parameter) ? '已启用' : '已关闭' }}</span>
        </label>
      </div>
    </div>

    <div v-if="hasRestartParameters" class="parameter-actions">
      <button
        class="apply-restart-button"
        type="button"
        :disabled="isApplying || !writeEnabled || !hasRestartChanges"
        @click="applyRestartChanges"
      >
        {{ isApplying ? '正在应用' : '保存并重新采集' }}
      </button>
    </div>
  </section>
</template>
