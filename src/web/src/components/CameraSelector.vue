<script setup lang="ts">
import { computed } from 'vue'

import type { CameraDevice, CameraError } from '../api/camera'

const props = defineProps<{
  devices: CameraDevice[]
  selectedDeviceId: string | null
  selectionEnabled: boolean
  discoveryError: CameraError | null
  isLoading: boolean
  isSelecting: boolean
}>()

const emit = defineEmits<{
  refresh: []
  select: [deviceId: string]
}>()

const selectedDevice = computed(() => (
  props.devices.find((device) => device.deviceId === props.selectedDeviceId) ?? null
))
const selectorDisabled = computed(() => (
  !props.selectionEnabled || props.isLoading || props.isSelecting || props.devices.length === 0
))

/** Keeps backend identifiers available while leading with the operator-facing name. */
function deviceLabel(device: CameraDevice): string {
  const model = device.modelName && !device.displayName.includes(device.modelName)
    ? ` · ${device.modelName}`
    : ''
  return `${device.displayName}${model} · ${device.transport}`
}

function requestSelection(event: Event): void {
  const deviceId = (event.target as HTMLSelectElement).value
  if (deviceId && deviceId !== props.selectedDeviceId) {
    emit('select', deviceId)
  }
}
</script>

<template>
  <section class="border-b border-slate-800 px-4 py-3" aria-label="采集相机选择">
    <div class="mb-2 flex items-center justify-between gap-3">
      <label class="text-[11px] font-semibold text-slate-400" for="camera-device-selector">采集设备</label>
      <button
        class="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] font-semibold text-slate-300 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-45"
        type="button"
        :disabled="isLoading || isSelecting"
        @click="emit('refresh')"
      >
        {{ isLoading ? '刷新中' : '刷新列表' }}
      </button>
    </div>

    <select
      id="camera-device-selector"
      data-testid="camera-device-selector"
      class="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs font-semibold text-slate-200 outline-none transition focus:border-sky-500 focus:ring-1 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-55"
      :value="selectedDeviceId ?? ''"
      :disabled="selectorDisabled"
      @change="requestSelection"
    >
      <option v-if="selectedDeviceId === null" value="">请选择采集设备</option>
      <option v-for="device in devices" :key="device.deviceId" :value="device.deviceId">
        {{ deviceLabel(device) }}
      </option>
    </select>

    <div class="mt-2 flex items-center justify-between gap-3 text-[10px]">
      <span class="truncate text-slate-500" :title="selectedDevice?.deviceId ?? ''">
        {{ selectedDevice?.displayName ?? (devices.length === 0 ? '未发现设备' : '尚未选择') }}
      </span>
      <span v-if="isSelecting" class="shrink-0 font-semibold text-amber-300" role="status">正在切换</span>
      <span v-else-if="selectedDevice" class="shrink-0 font-semibold" :class="selectedDevice.calibrated ? 'text-emerald-400' : 'text-slate-500'">
        {{ selectedDevice.calibrated ? '已标定' : '未标定' }}
      </span>
      <span v-else-if="!selectionEnabled" class="shrink-0 text-slate-500">选择已禁用</span>
    </div>

    <p
      v-if="discoveryError"
      class="mt-2 rounded border border-rose-900/50 bg-rose-950/20 px-2.5 py-2 text-[11px] text-rose-300"
      role="alert"
    >
      {{ discoveryError.message }}
    </p>
  </section>
</template>
