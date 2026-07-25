<script setup lang="ts">
import { computed } from 'vue'

import type { PoseTracking } from '../api/camera'

const props = defineProps<{
  pose: PoseTracking | null
  errorMessage: string | null
  isUpdatingTarget: boolean
  trackingEnabled: boolean
}>()

const emit = defineEmits<{
  selectTarget: [targetJoint: string]
}>()

const jointOptions = [
  ['nose', '鼻尖'], ['left_eye', '左眼'], ['right_eye', '右眼'],
  ['left_ear', '左耳'], ['right_ear', '右耳'],
  ['left_shoulder', '左肩'], ['right_shoulder', '右肩'],
  ['left_elbow', '左肘'], ['right_elbow', '右肘'],
  ['left_wrist', '左手腕'], ['right_wrist', '右手腕'],
  ['left_hip', '左髋'], ['right_hip', '右髋'],
  ['left_knee', '左膝'], ['right_knee', '右膝'],
  ['left_ankle', '左踝'], ['right_ankle', '右踝'],
] as const

const statusText = computed(() => {
  if (props.errorMessage !== null) {
    return props.errorMessage
  }
  if (props.pose === null) {
    return '正在读取姿态状态'
  }
  return props.pose.valid ? '已锁定目标关节' : '未检测到可锁定目标'
})

const latencyText = computed(() => {
  const latency = props.pose?.inferenceLatencyMs
  return latency === null || latency === undefined ? '暂无' : `${Math.round(latency)} ms`
})

const motionText = computed(() => {
  const motion = props.pose?.motion
  if (motion === null || motion === undefined) {
    return '正在建立基线'
  }
  return motion.moving ? '检测到运动' : '静止或微小运动'
})

const motionSpeedText = computed(() => {
  const speed = props.pose?.motion?.speed
  return speed === null || speed === undefined ? '暂无' : `${speed.toFixed(3)} 归一化坐标/秒`
})

/** Emits one explicit target selection; the parent owns persistence and request state. */
function changeTarget(event: Event): void {
  const target = event.target as HTMLSelectElement
  emit('selectTarget', target.value)
}
</script>

<template>
  <section class="p-5 border-b border-slate-900 shrink-0" aria-label="人体姿态">
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">人体姿态</h3>
      <span
        class="text-[10px] font-semibold"
        :class="pose?.valid ? 'text-emerald-400' : 'text-amber-400'"
      >
        {{ pose?.valid ? '已锁定' : '未锁定' }}
      </span>
    </div>

    <label class="block text-xs font-medium text-slate-300 mb-1.5" for="pose-target-joint">锁定关节</label>
    <select
      id="pose-target-joint"
      class="w-full rounded border border-slate-700 bg-slate-900 px-2.5 py-2 text-xs text-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
      :value="pose?.targetJoint ?? 'right_wrist'"
      :disabled="!trackingEnabled || isUpdatingTarget"
      @change="changeTarget"
    >
      <option v-for="[value, label] in jointOptions" :key="value" :value="value">{{ label }}</option>
    </select>

    <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
      <div>
        <dt class="text-slate-500">推理延迟</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ latencyText }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">连续丢失</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ pose?.lostFrames ?? 0 }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">运动状态</dt>
        <dd class="mt-0.5 font-semibold" :class="pose?.motion?.moving ? 'text-emerald-400' : 'text-slate-300'">
          {{ motionText }}
        </dd>
      </div>
      <div>
        <dt class="text-slate-500">关节速度</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ motionSpeedText }}</dd>
      </div>
    </dl>
    <p class="mt-3 text-[11px] leading-4" :class="pose?.valid ? 'text-slate-400' : 'text-amber-300'">
      {{ statusText }}
    </p>
  </section>
</template>
