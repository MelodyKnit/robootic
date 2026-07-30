<script setup lang="ts">
import { computed } from 'vue'

import type { CameraDevice } from '../api/camera'
import type { PluginCameraBinding } from '../api/plugins'

const props = defineProps<{
  binding: PluginCameraBinding
  cameraDevices: CameraDevice[]
  cameraSelectionEnabled: boolean
  isSelectingCamera: boolean
  pluginId: string
  selectedCameraDeviceId: string | null
}>()

const emit = defineEmits<{
  selectDevice: [deviceId: string]
}>()

const selectedCameraDevice = computed(() => (
  props.cameraDevices.find((device) => device.deviceId === props.selectedCameraDeviceId) ?? null
))
const isSharedSingleSource = computed(() => (
  props.binding.mode === 'shared_single_source'
  && props.binding.minimumSources === 1
  && props.binding.maximumSources === 1
))
const sourceLabel = computed(() => (
  props.binding.cameraIds.length === 0 ? '尚未绑定逻辑相机' : props.binding.cameraIds.join('、')
))

function cameraDeviceLabel(device: CameraDevice): string {
  const model = device.modelName && !device.displayName.includes(device.modelName)
    ? ` · ${device.modelName}`
    : ''
  return `${device.displayName}${model}`
}

function requestCameraSelection(event: Event): void {
  const deviceId = (event.target as HTMLSelectElement).value
  if (deviceId && deviceId !== props.selectedCameraDeviceId) {
    emit('selectDevice', deviceId)
  }
}
</script>

<template>
  <section
    class="border-b border-slate-800/80 px-3 py-2.5"
    :data-testid="`plugin-camera-binding-${props.pluginId}`"
  >
    <div class="mb-2 flex items-center justify-between gap-2">
      <label
        class="text-[11px] font-semibold text-slate-400"
        :for="`plugin-camera-selector-${props.pluginId}`"
      >
        相机输入
      </label>
      <span
        class="rounded border border-sky-900/60 bg-sky-950/30 px-1.5 py-0.5 text-[9px] font-semibold text-sky-300"
        :title="isSharedSingleSource ? '当前服务只有一条共享采集流，切换会同步影响中央画面和全部视觉插件。' : '当前运行时尚未提供独立多相机采集。'"
      >
        {{ isSharedSingleSource ? '共享单源' : '逻辑绑定' }}
      </span>
    </div>

    <template v-if="isSharedSingleSource">
      <select
        :id="`plugin-camera-selector-${props.pluginId}`"
        :data-testid="`plugin-camera-selector-${props.pluginId}`"
        class="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-[11px] font-semibold text-slate-200 outline-none transition focus:border-sky-500 focus:ring-1 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-55"
        :value="props.selectedCameraDeviceId ?? ''"
        :disabled="!props.cameraSelectionEnabled || props.isSelectingCamera || props.cameraDevices.length === 0"
        @change="requestCameraSelection"
      >
        <option v-if="props.selectedCameraDeviceId === null" value="">请选择采集相机</option>
        <option v-for="device in props.cameraDevices" :key="device.deviceId" :value="device.deviceId">
          {{ cameraDeviceLabel(device) }}
        </option>
      </select>
      <div class="mt-1.5 flex items-center justify-between gap-2 text-[10px]">
        <span class="truncate text-slate-500" :title="selectedCameraDevice?.deviceId ?? ''">
          {{ selectedCameraDevice?.displayName ?? (props.cameraDevices.length === 0 ? '未发现相机' : '尚未选择') }}
        </span>
        <span v-if="props.isSelectingCamera" class="shrink-0 font-semibold text-amber-300" role="status">正在切换</span>
        <span
          v-else-if="selectedCameraDevice"
          class="shrink-0 font-semibold"
          :class="selectedCameraDevice.calibrated ? 'text-emerald-400' : 'text-slate-500'"
        >
          {{ selectedCameraDevice.calibrated ? '已标定' : '未标定' }}
        </span>
        <span v-else-if="!props.cameraSelectionEnabled" class="shrink-0 text-slate-500">选择已禁用</span>
      </div>
    </template>

    <p v-else class="text-[10px] text-slate-500">
      当前绑定逻辑源：{{ sourceLabel }}
    </p>
    <p v-if="props.binding.state !== 'satisfied'" class="mt-1.5 text-[10px] text-amber-300">
      相机来源尚未满足插件要求，已停止接收新帧。
    </p>
  </section>
</template>
