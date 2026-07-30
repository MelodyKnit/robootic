<script setup lang="ts">
import { computed } from 'vue'

import type { ObjectPose, ObjectPoseTracking } from '../api/camera'

const props = defineProps<{
  objectPose: ObjectPoseTracking | null
  errorMessage: string | null
}>()

const primaryObject = computed(() => props.objectPose?.objects[0] ?? null)
const objectCount = computed(() => props.objectPose?.objects.length ?? 0)
const statusText = computed(() => {
  if (props.errorMessage !== null) {
    return props.errorMessage
  }
  if (props.objectPose === null) {
    return '正在读取工件位姿状态'
  }
  if (!props.objectPose.enabled) {
    return '识别未启用'
  }
  if (props.objectPose.valid && objectCount.value === 1) {
    return '位姿有效'
  }
  return objectPoseReasonLabel(props.objectPose.reason)
})
const statusClass = computed(() => {
  if (props.errorMessage !== null) {
    return 'text-rose-300'
  }
  if (props.objectPose?.valid && objectCount.value === 1) {
    return 'text-emerald-400'
  }
  return 'text-amber-400'
})
const latencyText = computed(() => {
  const latency = props.objectPose?.inferenceLatencyMs
  return latency === null || latency === undefined ? '暂无' : `${Math.round(latency)} ms`
})
const translationText = computed(() => translationLabel(primaryObject.value))
const yawText = computed(() => yawLabel(primaryObject.value))
const yawPeriodText = computed(() => {
  const yawPeriod = primaryObject.value?.yawPeriodRad
  return yawPeriod === null || yawPeriod === undefined ? '暂无' : `${yawPeriod.toFixed(3)} rad`
})

function translationLabel(objectPose: ObjectPose | null): string {
  const translation = objectPose?.translationMm
  if (translation === null || translation === undefined) {
    return '暂无'
  }
  return `${translation.x.toFixed(1)} / ${translation.y.toFixed(1)} / ${translation.z.toFixed(1)} mm`
}

function yawLabel(objectPose: ObjectPose | null): string {
  const yaw = objectPose?.orientationRpyRad?.yaw
  if (yaw === null || yaw === undefined) {
    return '暂无'
  }
  const degrees = yaw * 180 / Math.PI
  return `${degrees.toFixed(1)}°${objectPose?.orientationDefined ? '' : '（方向不唯一）'}`
}

function dofLabel(values: string[] | undefined): string {
  return values === undefined || values.length === 0 ? '暂无' : values.join(' / ')
}

function objectPoseReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    object_pose_disabled: '识别未启用',
    object_pose_waiting_for_frame: '等待相机画面',
    object_pose_unstable: '结果稳定性待确认',
    object_pose_analysis_failed: '工件分析失败',
    object_pose_projection_failed: '平面投影失败',
    no_foreground: '未发现工件',
    multiple_candidates: '发现多个候选工件',
    candidate_shape_mismatch: '轮廓与工件档案不符',
    directional_yaw_ambiguous: '头尾方向无法确认',
    orientation_undefined: '工件方向无法确认',
    planarity_suspected: '工件平放状态待确认',
    calibration_id_mismatch: '标定与当前相机不匹配',
    calibration_camera_mismatch: '标定相机不匹配',
    calibration_unavailable: '标定不可用',
    calibration_invalid: '标定不可用',
    calibration_reprojection_error_excessive: '相机标定误差超限',
    background_reference_unavailable: '空桌背景不可用',
    background_unavailable: '空桌背景不可用',
    background_dimensions_mismatch: '背景与当前画面不匹配',
    foreground_area_excessive: '画面前景异常',
  }
  return labels[reason] ?? reason
}
</script>

<template>
  <section class="shrink-0 border-b border-slate-900 p-5" aria-label="工件位姿">
    <div class="mb-3 flex items-center justify-between gap-3">
      <h3 class="text-[10px] font-bold uppercase tracking-widest text-slate-500">工件位姿</h3>
      <span class="shrink-0 text-[10px] font-semibold" :class="statusClass">
        {{ objectPose?.valid && objectCount === 1 ? '有效' : '待确认' }}
      </span>
    </div>

    <dl class="grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
      <div>
        <dt class="text-slate-500">候选数量</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ objectCount }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">推理延迟</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ latencyText }}</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">工件档案</dt>
        <dd class="mt-0.5 truncate font-mono font-semibold text-slate-300" :title="primaryObject?.profileId ?? ''">
          {{ primaryObject?.profileId ?? '暂无' }}
        </dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">坐标系</dt>
        <dd class="mt-0.5 truncate font-mono font-semibold text-slate-300" :title="primaryObject?.coordinateFrame ?? ''">
          {{ primaryObject?.coordinateFrame ?? '暂无' }}
        </dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">X / Y / Z</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ translationText }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">Yaw</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ yawText }}</dd>
      </div>
      <div>
        <dt class="text-slate-500">Yaw 周期</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ yawPeriodText }}</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">视觉观测自由度</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ dofLabel(primaryObject?.observedDof) }}</dd>
      </div>
      <div class="col-span-2">
        <dt class="text-slate-500">台面推导自由度</dt>
        <dd class="mt-0.5 font-semibold text-slate-300">{{ dofLabel(primaryObject?.derivedDof) }}</dd>
      </div>
    </dl>

    <p v-if="primaryObject?.warning" class="mt-3 text-[11px] leading-4 text-amber-300" role="status">
      {{ primaryObject.warning }}
    </p>
    <p class="mt-3 text-[11px] leading-4" :class="statusClass" :role="errorMessage ? 'alert' : 'status'">
      {{ statusText }}
    </p>
  </section>
</template>
