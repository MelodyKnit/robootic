<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { JointMovePreview, JointVector, RobotStatus } from '../api/robot'
import { useJakaControl } from '../composables/useJakaControl'

type ConfirmationMode = 'arm' | 'power-on' | 'enable' | 'move'

const {
  arm,
  armSecondsRemaining,
  createPreview,
  disarm,
  enable,
  errorMessage,
  executePreview,
  isArmed,
  isBusy,
  isLoading,
  nowSeconds,
  operationMessage,
  operationState,
  powerOn,
  reconnect,
  robot,
} = useJakaControl()

const jointNames = ['J1', 'J2', 'J3', 'J4', 'J5', 'J6'] as const
const jointDraftDegrees = ref<number[]>([0, 0, 0, 0, 0, 0])
const speedRadPerSecond = ref(0.1)
const stepDegrees = ref(2.0)
const draftStepModeEnabled = ref(false)
const confirmationMode = ref<ConfirmationMode | null>(null)
const workcellClear = ref(false)
const emergencyStopAvailable = ref(false)
const powerOnApproved = ref(false)
const enableApproved = ref(false)
const previewApproved = ref(false)
const preview = ref<JointMovePreview | null>(null)
let draftedForRobotId: string | null = null

const modeLabel = computed(() => (robot.value?.mode === 'physical' ? '真机 JAKA' : '仿真'))
const statusLabel = computed(() => robotStatusLabel(robot.value))
const speedMaximum = computed(() => robot.value?.maximumJointSpeedRadPerSecond ?? 0.5)
const draftsAreFinite = computed(
  () => jointDraftDegrees.value.length === 6 && jointDraftDegrees.value.every(Number.isFinite),
)
const isReadOnly = computed(
  () => robot.value !== null && (!robot.value.controlsEnabled || !robot.value.manualMotionEnabled),
)
const canArm = computed(
  () =>
    robot.value !== null &&
    robot.value.controlsEnabled &&
    robot.value.manualMotionEnabled &&
    robot.value.connected &&
    !robot.value.moving &&
    !robot.value.faulted &&
    !robot.value.emergencyStopped &&
    !isBusy.value &&
    !isArmed.value,
)
const canPowerOn = computed(
  () =>
    robot.value !== null &&
    robot.value.controlsEnabled &&
    robot.value.manualMotionEnabled &&
    robot.value.connected &&
    !robot.value.powered &&
    !robot.value.moving &&
    !robot.value.faulted &&
    !robot.value.emergencyStopped &&
    isArmed.value &&
    !isBusy.value,
)
const canEnable = computed(
  () =>
    robot.value !== null &&
    robot.value.controlsEnabled &&
    robot.value.manualMotionEnabled &&
    robot.value.enablePermitted &&
    robot.value.connected &&
    robot.value.powered &&
    !robot.value.enabled &&
    !robot.value.moving &&
    !robot.value.faulted &&
    !robot.value.emergencyStopped &&
    isArmed.value &&
    !isBusy.value,
)
const canCreatePreview = computed(
  () =>
    robot.value !== null &&
    robot.value.controlsEnabled &&
    robot.value.manualMotionEnabled &&
    robot.value.connected &&
    robot.value.powered &&
    robot.value.enabled &&
    !robot.value.moving &&
    !robot.value.faulted &&
    !robot.value.emergencyStopped &&
    isArmed.value &&
    !isBusy.value &&
    draftsAreFinite.value &&
    Number.isFinite(speedRadPerSecond.value) &&
    speedRadPerSecond.value > 0 &&
    speedRadPerSecond.value <= speedMaximum.value,
)
const previewIsFresh = computed(
  () => preview.value !== null && preview.value.expiresAt > nowSeconds.value,
)
const canConfirmPreview = computed(
  () => previewIsFresh.value && canCreatePreview.value && !isBusy.value,
)
const confirmationReady = computed(() => {
  if (confirmationMode.value === 'arm') {
    return workcellClear.value && emergencyStopAvailable.value
  }
  if (confirmationMode.value === 'power-on') {
    return powerOnApproved.value
  }
  if (confirmationMode.value === 'enable') {
    return enableApproved.value
  }
  return previewIsFresh.value && previewApproved.value
})
const jointRows = computed(() => {
  const status = robot.value
  if (status === null) {
    return []
  }
  return status.jointPositionsRad.map((positionRad, index) => ({
    name: jointNames[index] ?? `J${index + 1}`,
    currentRad: positionRad,
    currentDegrees: radiansToDegrees(positionRad),
    lowerDegrees: radiansToDegrees(status.jointLowerLimitsRad[index]),
    upperDegrees: radiansToDegrees(status.jointUpperLimitsRad[index]),
    draftDegrees: jointDraftDegrees.value[index] ?? radiansToDegrees(positionRad),
  }))
})

watch(
  robot,
  (status) => {
    if (status === null) {
      return
    }
    if (status.id !== draftedForRobotId) {
      draftedForRobotId = status.id
      resetDraftToReadback(status)
    }
    if (
      preview.value !== null &&
      !isBusy.value &&
      !sameJointVector(status.jointPositionsRad, preview.value.sourceJointPositionsRad, 0.001)
    ) {
      preview.value = null
      previewApproved.value = false
    }
  },
  { immediate: true },
)

watch(isArmed, (armed) => {
  // A preview belongs to the current short-lived browser authorization only.
  // Removing it here prevents a newly armed session from displaying an old plan.
  if (!armed) {
    discardPreview()
  }
})

function openArmConfirmation(): void {
  workcellClear.value = false
  emergencyStopAvailable.value = false
  confirmationMode.value = 'arm'
}

function openPowerOnConfirmation(): void {
  powerOnApproved.value = false
  confirmationMode.value = 'power-on'
}

function openEnableConfirmation(): void {
  enableApproved.value = false
  confirmationMode.value = 'enable'
}

function openMoveConfirmation(): void {
  if (!previewIsFresh.value) {
    discardPreview()
    operationMessage.value = '关节运动预览已过期，请重新生成。'
    operationState.value = 'failed'
    return
  }
  previewApproved.value = false
  confirmationMode.value = 'move'
}

function closeConfirmation(): void {
  if (!isBusy.value) {
    confirmationMode.value = null
  }
}

async function confirmAction(): Promise<void> {
  if (!confirmationReady.value) {
    return
  }
  if (confirmationMode.value === 'arm') {
    if (
      await arm({
        workcellClear: workcellClear.value,
        emergencyStopAvailable: emergencyStopAvailable.value,
      })
    ) {
      confirmationMode.value = null
    }
    return
  }
  if (confirmationMode.value === 'power-on') {
    if (await powerOn()) {
      confirmationMode.value = null
    }
    return
  }
  if (confirmationMode.value === 'enable') {
    if (await enable()) {
      confirmationMode.value = null
    }
    return
  }
  if (confirmationMode.value === 'move' && preview.value !== null) {
    const previewId = preview.value.id
    if (await executePreview(previewId)) {
      preview.value = null
      confirmationMode.value = null
    }
  }
}

async function generatePreview(): Promise<void> {
  if (!canCreatePreview.value) {
    return
  }
  const vector = jointDraftDegrees.value.map(degreesToRadians)
  if (!isJointVector(vector)) {
    return
  }
  preview.value = await createPreview({
    jointPositionsRad: vector,
    speedRadPerSecond: speedRadPerSecond.value,
  })
  previewApproved.value = false
}

/** Updates only the local target draft. Motion still requires preview and confirmation. */
function adjustJointDraft(index: number, deltaDegrees: number): void {
  const status = robot.value
  if (status === null) {
    return
  }
  const minimum = radiansToDegrees(status.jointLowerLimitsRad[index])
  const maximum = radiansToDegrees(status.jointUpperLimitsRad[index])
  const currentVal = jointDraftDegrees.value[index] ?? radiansToDegrees(status.jointPositionsRad[index])
  const newVal = clamp(currentVal + deltaDegrees, minimum, maximum)

  const nextDraft = [...jointDraftDegrees.value]
  nextDraft[index] = newVal
  jointDraftDegrees.value = nextDraft
  discardPreview()
}

function updateJointDraft(index: number, event: Event): void {
  const status = robot.value
  if (status === null) {
    return
  }
  const rawValue = Number((event.target as HTMLInputElement).value)
  if (!Number.isFinite(rawValue)) {
    return
  }
  const minimum = radiansToDegrees(status.jointLowerLimitsRad[index])
  const maximum = radiansToDegrees(status.jointUpperLimitsRad[index])
  const nextDraft = [...jointDraftDegrees.value]
  nextDraft[index] = clamp(rawValue, minimum, maximum)
  jointDraftDegrees.value = nextDraft
  discardPreview()
}

function updateSpeed(event: Event): void {
  const rawValue = Number((event.target as HTMLInputElement).value)
  if (!Number.isFinite(rawValue)) {
    return
  }
  speedRadPerSecond.value = clamp(rawValue, 0.01, speedMaximum.value)
  discardPreview()
}

function resetDraftToReadback(status = robot.value): void {
  if (status === null) {
    return
  }
  jointDraftDegrees.value = status.jointPositionsRad.map(radiansToDegrees)
  speedRadPerSecond.value = clamp(0.1, 0.01, status.maximumJointSpeedRadPerSecond)
  discardPreview()
}

function discardPreview(): void {
  preview.value = null
  previewApproved.value = false
  if (confirmationMode.value === 'move') {
    confirmationMode.value = null
  }
}

function robotStatusLabel(status: RobotStatus | null): string {
  if (status === null) {
    return isLoading.value ? '正在读取' : '不可用'
  }
  if (!status.connected) {
    return '连接断开'
  }
  if (status.emergencyStopped) {
    return '急停状态'
  }
  if (status.faulted) {
    return '故障'
  }
  if (status.moving) {
    return '运动中'
  }
  if (!status.powered) {
    return '未上电'
  }
  if (!status.enabled) {
    return '未使能'
  }
  return '已就绪'
}

function radiansToDegrees(value: number): number {
  return (value * 180) / Math.PI
}

function degreesToRadians(value: number): number {
  return (value * Math.PI) / 180
}

function formatDegrees(value: number): string {
  return `${value.toFixed(2)} deg`
}

function formatRadians(value: number): string {
  return `${value.toFixed(4)} rad`
}

function formatTimestamp(value: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(value * 1_000))
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function isJointVector(values: number[]): values is JointVector {
  return values.length === 6 && values.every(Number.isFinite)
}

function sameJointVector(first: JointVector, second: JointVector, tolerance = 0.000001): boolean {
  return first.every((value, index) => Math.abs(value - second[index]) < tolerance)
}
</script>

<template>
  <section class="flex min-h-full flex-col bg-slate-950 p-5 text-slate-300 select-none" aria-label="机械臂控制">
    <div class="flex items-start justify-between gap-3 border-b border-slate-900 pb-4">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-xs font-bold text-slate-500">机械臂控制</h2>
          <span
            v-if="robot"
            class="rounded border px-1.5 py-0.5 text-[9px] font-bold"
            :class="robot.mode === 'physical' ? 'border-amber-900/60 bg-amber-950/40 text-amber-400' : 'border-sky-900/60 bg-sky-950/40 text-sky-400'"
          >
            {{ modeLabel }}
          </span>
        </div>
        <p class="mt-1 truncate text-xs font-semibold text-slate-300" :title="robot?.id ?? ''">
          {{ robot?.id ?? (isLoading ? '正在连接控制服务...' : (errorMessage ? '控制服务不可用' : '未配置机械臂')) }}
        </p>
      </div>
      <button
        type="button"
        class="shrink-0 rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="robot === null || isBusy"
        @click="reconnect"
      >
        重新连接
      </button>
    </div>

    <p
      v-if="errorMessage"
      class="mt-4 rounded border border-rose-900/40 bg-rose-950/20 px-3 py-2 text-xs text-rose-400"
      role="alert"
    >
      {{ errorMessage }}
    </p>

    <template v-if="robot">
      <dl class="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-slate-900 py-4 text-xs">
        <div>
          <dt class="text-[10px] font-bold text-slate-600">运行状态</dt>
          <dd class="mt-1 font-semibold" :class="statusLabel === '已就绪' ? 'text-emerald-400' : 'text-amber-400'">{{ statusLabel }}</dd>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">最新读取</dt>
          <dd class="mt-1 truncate text-[10px] font-semibold text-slate-300" :title="formatTimestamp(robot.capturedAt)">{{ formatTimestamp(robot.capturedAt) }}</dd>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">电源</dt>
          <dd class="mt-1 font-semibold" :class="robot.powered ? 'text-emerald-400' : 'text-amber-400'">
            {{ robot.powered ? '已上电' : '未上电' }}
          </dd>
          <dd v-if="!robot.powered" class="mt-0.5 text-[9px] text-slate-500">需在控制器手动上电</dd>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">伺服</dt>
          <dd class="mt-1 font-semibold text-slate-300">{{ robot.enabled ? '已使能' : '未使能' }}</dd>
        </div>
      </dl>

      <div class="border-b border-slate-900 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-bold text-slate-400">控制授权</p>
            <p class="mt-1 text-[10px]" :class="isArmed ? 'text-emerald-400' : 'text-slate-600'">
              {{ isArmed ? `已解锁，剩余 ${armSecondsRemaining} 秒` : '当前已锁定' }}
            </p>
          </div>
          <button
            v-if="!isArmed"
            type="button"
            class="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canArm"
            @click="openArmConfirmation"
          >
            解锁控制
          </button>
          <button
            v-else
            type="button"
            class="rounded border border-amber-800/70 bg-amber-950/30 px-3 py-1.5 text-xs font-bold text-amber-300 transition hover:bg-amber-950/60"
            @click="disarm"
          >
            立即锁定
          </button>
        </div>
        <p v-if="isReadOnly" class="mt-2 text-[10px] text-amber-400">当前本机配置仅允许读取机械臂状态。</p>
      </div>

      <div class="border-b border-slate-900 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-bold text-slate-400">控制器上电</p>
            <p v-if="robot.powered" class="mt-1 text-[10px] text-emerald-400">✓ 控制器已上电</p>
            <p v-else class="mt-1 text-[10px] text-slate-600">需要上电后才能使能伺服。</p>
          </div>
          <button
            type="button"
            class="rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-bold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canPowerOn"
            @click="openPowerOnConfirmation"
          >
            {{ robot.powered ? '已上电' : '上电控制器' }}
          </button>
        </div>
      </div>

      <div class="border-b border-slate-900 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-bold text-slate-400">伺服使能</p>
            <p v-if="robot.powered && !robot.enabled" class="mt-1 text-[10px] text-slate-600">上电后可使能伺服。</p>
            <p v-else-if="robot.enabled" class="mt-1 text-[10px] text-emerald-400">✓ 伺服已使能</p>
            <p v-else class="mt-1 text-[10px] text-amber-400">⚠ 请先上电控制器</p>
          </div>
          <button
            type="button"
            class="rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-bold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canEnable"
            @click="openEnableConfirmation"
          >
            {{ robot.enabled ? '伺服已使能' : '使能伺服' }}
          </button>
        </div>
      </div>

      <fieldset class="space-y-3 py-4" :disabled="isBusy || robot === null">
        <div class="flex items-center justify-between gap-3">
          <legend class="text-xs font-bold text-slate-500">J1 - J6 绝对关节目标</legend>
          <div class="flex items-center gap-3">
            <label class="flex cursor-pointer items-center gap-1.5 text-[11px] font-semibold text-slate-300">
              <input v-model="draftStepModeEnabled" type="checkbox" class="accent-sky-500" aria-label="启用草稿步进微调" />
              启用草稿步进微调
            </label>
            <button
              type="button"
              class="text-[10px] font-semibold text-sky-400 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="isBusy || robot === null"
              @click="resetDraftToReadback()"
            >
              同步当前读数
            </button>
          </div>
        </div>

        <div v-if="draftStepModeEnabled" class="flex items-center justify-between gap-3 rounded border border-sky-900/50 bg-sky-950/30 p-2">
          <span class="text-[11px] font-semibold text-sky-300">草稿微调增量 (deg):</span>
          <div class="flex items-center gap-1">
            <button
              v-for="step in [0.5, 1.0, 2.0, 5.0]"
              :key="step"
              type="button"
              class="rounded px-2 py-0.5 text-[10px] font-bold transition"
              :class="stepDegrees === step ? 'bg-sky-600 text-white' : 'bg-slate-900 text-slate-400 hover:bg-slate-800'"
              @click="stepDegrees = step"
            >
              ±{{ step }}°
            </button>
          </div>
        </div>

        <div v-for="(joint, index) in jointRows" :key="joint.name" class="rounded border border-slate-900 bg-slate-900/30 p-2.5">
          <div class="flex items-start justify-between gap-3">
            <div>
              <p class="text-xs font-bold text-slate-300">{{ joint.name }}</p>
              <p class="mt-0.5 font-mono text-[10px] text-slate-500">{{ formatDegrees(joint.currentDegrees) }} / {{ formatRadians(joint.currentRad) }}</p>
            </div>
            <div class="flex items-center gap-2">
              <div v-if="draftStepModeEnabled" class="flex items-center gap-1">
                <button
                  type="button"
                  class="rounded bg-slate-800 hover:bg-sky-700 px-2 py-1 text-xs font-bold text-slate-200 transition disabled:opacity-30 disabled:cursor-not-allowed"
                  :aria-label="`减少 ${joint.name} 草稿角度`"
                  :disabled="isBusy || robot === null"
                  title="减少草稿角度"
                  @click="adjustJointDraft(index, -stepDegrees)"
                >
                  -
                </button>
                <button
                  type="button"
                  class="rounded bg-slate-800 hover:bg-sky-700 px-2 py-1 text-xs font-bold text-slate-200 transition disabled:opacity-30 disabled:cursor-not-allowed"
                  :aria-label="`增加 ${joint.name} 草稿角度`"
                  :disabled="isBusy || robot === null"
                  title="增加草稿角度"
                  @click="adjustJointDraft(index, stepDegrees)"
                >
                  +
                </button>
              </div>
              <label class="block text-right text-[10px] font-semibold text-slate-500" :for="`robot-joint-${index}`">
                目标角度 deg
                <input
                  :id="`robot-joint-${index}`"
                  :aria-label="`${joint.name} 目标角度`"
                  class="mt-1 block w-24 rounded border border-slate-800 bg-slate-950 px-2 py-1 text-right font-mono text-xs font-bold text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-700 disabled:opacity-40"
                  type="number"
                  :min="joint.lowerDegrees"
                  :max="joint.upperDegrees"
                  step="0.1"
                  :value="joint.draftDegrees"
                  @change="updateJointDraft(index, $event)"
                  @input="updateJointDraft(index, $event)"
                />
              </label>
            </div>
          </div>
          <p class="mt-1 text-[9px] text-slate-600">软件范围 {{ formatDegrees(joint.lowerDegrees) }} 至 {{ formatDegrees(joint.upperDegrees) }}</p>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between gap-3">
            <label for="robot-joint-speed" class="text-xs font-bold text-slate-400">关节速度</label>
            <span class="font-mono text-[10px] text-slate-500">最高 {{ speedMaximum.toFixed(2) }} rad/s</span>
          </div>
          <input
            id="robot-joint-speed"
            aria-label="关节速度"
            class="w-full rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5 text-right font-mono text-xs font-bold text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-700 disabled:opacity-40"
            type="number"
            min="0.01"
            :max="speedMaximum"
            step="0.01"
            :value="speedRadPerSecond"
            @change="updateSpeed"
            @input="updateSpeed"
          />
        </div>
      </fieldset>

      <div class="space-y-2 border-t border-slate-900 pt-4">
        <button
          type="button"
          class="w-full rounded bg-indigo-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canCreatePreview"
          @click="generatePreview"
        >
          生成动作预览
        </button>
        <button
          type="button"
          class="w-full rounded border border-indigo-800/70 bg-indigo-950/40 px-3 py-2 text-xs font-bold text-indigo-200 transition hover:bg-indigo-950/70 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canConfirmPreview"
          @click="openMoveConfirmation"
        >
          确认并发送预览
        </button>
      </div>

      <p
        v-if="operationMessage"
        class="mt-3 rounded border px-3 py-2 text-xs"
        :class="operationState === 'failed' ? 'border-rose-900/40 bg-rose-950/20 text-rose-400' : (operationState === 'submitting' || operationState === 'moving' ? 'border-amber-900/40 bg-amber-950/20 text-amber-300' : 'border-emerald-900/40 bg-emerald-950/20 text-emerald-400')"
        role="status"
      >
        {{ operationMessage }}
      </p>
    </template>

    <div v-else-if="isLoading" class="flex flex-1 items-center justify-center py-12 text-xs text-slate-500" role="status">
      正在读取机械臂状态...
    </div>

    <div
      v-if="confirmationMode !== null"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      @click.self="closeConfirmation"
    >
      <section
        class="w-full max-w-md rounded-lg border border-slate-700 bg-slate-950 p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="`robot-${confirmationMode}-title`"
      >
        <template v-if="confirmationMode === 'arm'">
          <h3 id="robot-arm-title" class="text-sm font-bold text-slate-100">确认解锁机械臂控制</h3>
          <div class="mt-4 space-y-3">
            <label class="flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
              <input v-model="workcellClear" type="checkbox" class="mt-0.5 accent-emerald-500" />
              工作区已清空，机械臂运动不会碰撞人员或物体。
            </label>
            <label class="flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
              <input v-model="emergencyStopAvailable" type="checkbox" class="mt-0.5 accent-emerald-500" />
              现场独立急停可用，并且我知道其位置。
            </label>
          </div>
        </template>
        <template v-else-if="confirmationMode === 'power-on'">
          <h3 id="robot-power-on-title" class="text-sm font-bold text-slate-100">⚠️ 确认控制器上电</h3>
          <div class="mt-4 space-y-3 rounded border border-amber-900/40 bg-amber-950/20 p-3">
            <p class="text-xs font-bold text-amber-300">上电前安全检查清单：</p>
            <ul class="space-y-2 text-xs leading-5 text-amber-200">
              <li>✓ 工作区域已清空，无人员</li>
              <li>✓ 机械臂姿态安全，无障碍物</li>
              <li>✓ 急停按钮在可触及范围内</li>
              <li>✓ 已通知周围人员</li>
            </ul>
          </div>
          <p class="mt-3 text-xs leading-5 text-slate-400">
            远程上电会启动电机电源。此操作仅在实验室受控环境下使用，生产环境应使用控制柜物理按钮。
          </p>
          <label class="mt-4 flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
            <input v-model="powerOnApproved" type="checkbox" class="mt-0.5 accent-emerald-500" />
            我已完成上述安全检查，确认可以远程上电。
          </label>
        </template>
        <template v-else-if="confirmationMode === 'enable'">
          <h3 id="robot-enable-title" class="text-sm font-bold text-slate-100">确认使能 JAKA 伺服</h3>
          <p class="mt-3 text-xs leading-5 text-amber-300">此操作不会自动上电或发送关节运动，但会让已上电机械臂进入可执行状态。</p>
          <label class="mt-4 flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
            <input v-model="enableApproved" type="checkbox" class="mt-0.5 accent-emerald-500" />
            已确认机械臂已上电、无故障且现场独立急停可用。
          </label>
        </template>
        <template v-else-if="preview !== null">
          <h3 id="robot-move-title" class="text-sm font-bold text-slate-100">确认发送关节运动</h3>
          <dl class="mt-4 space-y-2 rounded border border-slate-800 bg-slate-900/40 p-3 text-xs">
            <div v-for="(name, index) in jointNames" :key="name" class="grid grid-cols-[2rem_1fr_1fr] gap-2 font-mono text-[10px]">
              <dt class="font-bold text-slate-300">{{ name }}</dt>
              <dd class="text-slate-400">{{ formatDegrees(radiansToDegrees(preview.sourceJointPositionsRad[index])) }}</dd>
              <dd class="text-right text-sky-300">{{ formatDegrees(radiansToDegrees(preview.targetJointPositionsRad[index])) }}</dd>
            </div>
          </dl>
          <p class="mt-3 text-xs text-slate-400">预计时长 {{ preview.estimatedDurationSeconds.toFixed(2) }} 秒。</p>
          <p class="mt-3 rounded border border-rose-900/40 bg-rose-950/20 px-3 py-2 text-[10px] leading-4 text-rose-300">
            网页不提供软件急停。运动异常请立即使用现场独立急停；“立即锁定”只阻止后续网页指令。
          </p>
          <label class="mt-4 flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
            <input v-model="previewApproved" type="checkbox" class="mt-0.5 accent-emerald-500" />
            已核对六轴目标、预计时长和工作区，可以发送这一条关节运动。
          </label>
        </template>

        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            class="rounded border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:bg-slate-900 disabled:opacity-50"
            :disabled="isBusy"
            @click="closeConfirmation"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!confirmationReady || isBusy"
            @click="confirmAction"
          >
            {{ isBusy ? '正在提交...' : '确认继续' }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>
