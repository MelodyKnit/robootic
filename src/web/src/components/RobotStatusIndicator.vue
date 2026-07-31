<template>
  <div class="flex items-center gap-1">
    <!-- 连接状态 -->
    <div
      :class="[
        'w-2 h-2 rounded-full',
        connected ? 'bg-green-500' : 'bg-red-500',
      ]"
      :title="connected ? '已连接' : '未连接'"
    />

    <!-- 上电状态 -->
    <div
      v-if="connected"
      :class="[
        'w-2 h-2 rounded-full',
        powered ? 'bg-emerald-500' : 'bg-slate-600',
      ]"
      :title="powered ? '已上电' : '未上电'"
    />

    <!-- 使能状态 -->
    <div
      v-if="connected && powered"
      :class="[
        'w-2 h-2 rounded-full',
        enabled ? 'bg-blue-500' : 'bg-slate-600',
      ]"
      :title="enabled ? '已使能' : '未使能'"
    />

    <!-- 故障状态 -->
    <div
      v-if="faulted || emergencyStopped"
      class="w-2 h-2 rounded-full bg-red-500 animate-pulse"
      :title="emergencyStopped ? '急停' : '故障'"
    />
  </div>
</template>

<script setup lang="ts">
interface Props {
  connected: boolean
  powered: boolean
  enabled: boolean
  faulted: boolean
  emergencyStopped: boolean
}

defineProps<Props>()
</script>
