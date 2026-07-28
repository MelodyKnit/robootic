<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { GripperCommandRequest, GripperStatus } from '../api/gripper'
import { useGripperControl } from '../composables/useGripperControl'

type ConfirmationMode = 'arm' | 'initialize'

const {
  arm,
  armSecondsRemaining,
  disarm,
  errorMessage,
  execute,
  gripper,
  initialize,
  isArmed,
  isBusy,
  isLoading,
  operationMessage,
  operationState,
  reconnect,
} = useGripperControl()

const targetPosition = ref(0)
const forcePercent = ref(20)
const speedPercent = ref(1)
const confirmationMode = ref<ConfirmationMode | null>(null)
const workcellClear = ref(false)
const emergencyStopVerified = ref(false)
const initializationApproved = ref(false)
let draftedForGripperId: string | null = null

const modeLabel = computed(() => (gripper.value?.mode === 'physical' ? '真机 TCP' : '仿真'))
const positionLabel = computed(() =>
  gripper.value?.positionIsFeedback === false ? '已设定目标位置' : '当前位置',
)
const statusLabel = computed(() => {
  const status = gripper.value
  if (status === null) {
    return isLoading.value ? '正在读取' : '不可用'
  }
  if (!status.connected) {
    return '连接断开'
  }
  if (status.initializing) {
    return '正在初始化'
  }
  if (status.moving) {
    return '正在运动'
  }
  if (status.gripping) {
    return '已夹持'
  }
  if (!status.initialized) {
    return '等待初始化'
  }
  return '已就绪'
})
const positionMinimum = computed(() => {
  const status = gripper.value
  return status?.minimumPosition ?? 0
})
const positionMaximum = computed(() => {
  const status = gripper.value
  return status?.maximumPosition ?? 1_000
})
const canArm = computed(
  () =>
    gripper.value !== null &&
    gripper.value.controlsEnabled &&
    gripper.value.connected &&
    !gripper.value.moving &&
    !isBusy.value &&
    !isArmed.value,
)
const canInitialize = computed(
  () =>
    gripper.value !== null &&
    gripper.value.controlsEnabled &&
    gripper.value.connected &&
    !gripper.value.initialized &&
    !gripper.value.initializing &&
    !gripper.value.moving &&
    isArmed.value &&
    !isBusy.value,
)
const canMove = computed(
  () =>
    gripper.value !== null &&
    gripper.value.controlsEnabled &&
    gripper.value.connected &&
    gripper.value.initialized &&
    !gripper.value.initializing &&
    !gripper.value.moving &&
    isArmed.value &&
    !isBusy.value,
)
const canStop = computed(
  () =>
    gripper.value !== null &&
    gripper.value.mode === 'simulation' &&
    gripper.value.supportsStop &&
    gripper.value.controlsEnabled &&
    gripper.value.connected &&
    isArmed.value &&
    !isBusy.value,
)
const confirmationReady = computed(() => {
  if (confirmationMode.value === 'arm') {
    return workcellClear.value && emergencyStopVerified.value
  }
  return initializationApproved.value
})

watch(
  gripper,
  (status) => {
    if (status === null || status.id === draftedForGripperId) {
      return
    }
    draftedForGripperId = status.id
    targetPosition.value = clampInteger(
      status.targetPosition,
      status.minimumPosition,
      status.maximumPosition,
    )
    forcePercent.value = status.forceMin
    speedPercent.value = status.speedMin
  },
  { immediate: true },
)

function openArmConfirmation(): void {
  workcellClear.value = false
  emergencyStopVerified.value = false
  confirmationMode.value = 'arm'
}

function openInitializationConfirmation(): void {
  initializationApproved.value = false
  confirmationMode.value = 'initialize'
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
    if (await arm(workcellClear.value, emergencyStopVerified.value)) {
      confirmationMode.value = null
    }
    return
  }
  if (confirmationMode.value === 'initialize') {
    await initialize()
    confirmationMode.value = null
  }
}

function commandParameters(action: GripperCommandRequest['action']): GripperCommandRequest {
  const status = gripper.value
  const command: GripperCommandRequest = {
    action,
    forcePercent: forcePercent.value,
  }
  if (status?.mode === 'simulation' && status.speedSupported) {
    command.speedPercent = speedPercent.value
  }
  if (action === 'move_to_position') {
    command.targetPosition = targetPosition.value
  }
  return command
}

function submitAction(action: GripperCommandRequest['action']): void {
  void execute(action === 'stop' ? { action } : commandParameters(action))
}

function updateIntegerDraft(target: 'position' | 'force' | 'speed', event: Event): void {
  const value = Number((event.target as HTMLInputElement).value)
  if (!Number.isFinite(value)) {
    return
  }
  const status = gripper.value
  if (target === 'position') {
    targetPosition.value = clampInteger(value, positionMinimum.value, positionMaximum.value)
  } else if (target === 'force' && status !== null) {
    forcePercent.value = clampInteger(value, status.forceMin, status.forceMax)
  } else if (target === 'speed' && status !== null) {
    speedPercent.value = clampInteger(value, status.speedMin, status.speedMax)
  }
}

function gripStateLabel(status: GripperStatus): string {
  const labels: Record<string, string> = {
    idle: '空闲',
    moving: '运动中',
    reached: '到达目标',
    gripping: '夹持物体',
    dropped: '物体掉落',
    unknown: '未知',
  }
  return labels[status.gripState] ?? status.gripState
}

function clampInteger(value: number, minimum: number, maximum: number): number {
  return Math.round(Math.min(maximum, Math.max(minimum, value)))
}
</script>

<template>
  <section class="flex min-h-full flex-col bg-slate-950 p-5 text-slate-300 select-none" aria-label="夹爪控制">
    <div class="flex items-start justify-between gap-3 border-b border-slate-900 pb-4">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-xs font-bold text-slate-500">夹爪控制</h2>
          <span
            v-if="gripper"
            class="rounded border px-1.5 py-0.5 text-[9px] font-bold"
            :class="gripper.mode === 'physical' ? 'border-amber-900/60 bg-amber-950/40 text-amber-400' : 'border-sky-900/60 bg-sky-950/40 text-sky-400'"
          >
            {{ modeLabel }}
          </span>
        </div>
        <p class="mt-1 truncate text-xs font-semibold text-slate-300" :title="gripper?.id ?? ''">
          {{ gripper?.id ?? (isLoading ? '正在连接控制服务...' : (errorMessage ? '控制服务不可用' : '未配置夹爪')) }}
        </p>
      </div>
      <button
        type="button"
        class="shrink-0 rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="gripper === null || isBusy"
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

    <template v-if="gripper">
      <dl class="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-slate-900 py-4 text-xs">
        <div>
          <dt class="text-[10px] font-bold text-slate-600">运行状态</dt>
          <dd class="mt-1 font-semibold" :class="gripper.connected ? 'text-emerald-400' : 'text-rose-400'">{{ statusLabel }}</dd>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">{{ positionLabel }}</dt>
          <dd class="mt-1 font-mono font-bold text-slate-200">{{ gripper.targetPosition }}</dd>
          <p v-if="!gripper.positionIsFeedback" class="mt-1 text-[9px] text-amber-400">实时位置反馈未验证</p>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">初始化</dt>
          <dd class="mt-1 font-semibold text-slate-300">{{ gripper.initialized ? '已完成' : (gripper.initializing ? '进行中' : '未完成') }}</dd>
        </div>
        <div>
          <dt class="text-[10px] font-bold text-slate-600">夹持反馈</dt>
          <dd class="mt-1 font-semibold text-slate-300">{{ gripStateLabel(gripper) }}</dd>
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
        <p v-if="!gripper.controlsEnabled" class="mt-2 text-[10px] text-amber-400">当前配置未开放夹爪网页控制。</p>
      </div>

      <div class="border-b border-slate-900 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-bold text-slate-400">设备初始化</p>
            <p class="mt-1 text-[10px] text-slate-600">初始化会驱动夹爪执行标定动作。</p>
          </div>
          <button
            type="button"
            class="rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-bold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canInitialize"
            @click="openInitializationConfirmation"
          >
            {{ gripper.initializing ? '初始化中' : '初始化' }}
          </button>
        </div>
      </div>

      <fieldset class="space-y-4 py-4" :disabled="isBusy">
        <legend class="mb-4 text-xs font-bold text-slate-500">动作参数</legend>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <label for="gripper-target-position" class="text-xs font-bold text-slate-400">目标位置</label>
            <span class="font-mono text-[10px] text-slate-500">{{ positionMinimum }} - {{ positionMaximum }}</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              id="gripper-target-position"
              aria-label="目标位置"
              class="h-1 flex-1 cursor-pointer appearance-none rounded-lg bg-slate-900 accent-indigo-500"
              type="range"
              :min="positionMinimum"
              :max="positionMaximum"
              step="1"
              :value="targetPosition"
              @input="updateIntegerDraft('position', $event)"
            />
            <input
              aria-label="目标位置数值"
              class="w-20 rounded border border-slate-800 bg-slate-900/50 px-2 py-1 text-right font-mono text-xs font-bold text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-700"
              type="number"
              :min="positionMinimum"
              :max="positionMaximum"
              step="1"
              :value="targetPosition"
              @input="updateIntegerDraft('position', $event)"
            />
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <label for="gripper-force" class="text-xs font-bold text-slate-400">目标力</label>
            <span class="font-mono text-[10px] text-slate-500">{{ gripper.forceMin }}% - {{ gripper.forceMax }}%</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              id="gripper-force"
              aria-label="目标力"
              class="h-1 flex-1 cursor-pointer appearance-none rounded-lg bg-slate-900 accent-indigo-500"
              type="range"
              :min="gripper.forceMin"
              :max="gripper.forceMax"
              step="1"
              :value="forcePercent"
              @input="updateIntegerDraft('force', $event)"
            />
            <input
              aria-label="目标力数值"
              class="w-20 rounded border border-slate-800 bg-slate-900/50 px-2 py-1 text-right font-mono text-xs font-bold text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-700"
              type="number"
              :min="gripper.forceMin"
              :max="gripper.forceMax"
              step="1"
              :value="forcePercent"
              @input="updateIntegerDraft('force', $event)"
            />
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <label for="gripper-speed" class="text-xs font-bold text-slate-400">速度</label>
            <span v-if="!gripper.speedSupported" class="text-[10px] font-semibold text-amber-500">真机协议未开放</span>
            <span v-else class="font-mono text-[10px] text-slate-500">{{ gripper.speedMin }}% - {{ gripper.speedMax }}%</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              id="gripper-speed"
              aria-label="速度"
              class="h-1 flex-1 cursor-pointer appearance-none rounded-lg bg-slate-900 accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
              type="range"
              :min="gripper.speedMin"
              :max="gripper.speedMax"
              step="1"
              :value="speedPercent"
              :disabled="!gripper.speedSupported"
              @input="updateIntegerDraft('speed', $event)"
            />
            <input
              aria-label="速度数值"
              class="w-20 rounded border border-slate-800 bg-slate-900/50 px-2 py-1 text-right font-mono text-xs font-bold text-slate-200 disabled:opacity-40"
              type="number"
              :min="gripper.speedMin"
              :max="gripper.speedMax"
              step="1"
              :value="speedPercent"
              :disabled="!gripper.speedSupported"
              @input="updateIntegerDraft('speed', $event)"
            />
          </div>
        </div>
      </fieldset>

      <div class="grid grid-cols-2 gap-2 border-t border-slate-900 pt-4">
        <button
          type="button"
          class="rounded bg-indigo-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canMove"
          @click="submitAction('open')"
        >
          打开夹爪
        </button>
        <button
          type="button"
          class="rounded bg-indigo-600 px-3 py-2 text-xs font-bold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canMove"
          @click="submitAction('close')"
        >
          关闭夹爪
        </button>
        <button
          type="button"
          class="col-span-2 rounded border border-indigo-800/70 bg-indigo-950/40 px-3 py-2 text-xs font-bold text-indigo-200 transition hover:bg-indigo-950/70 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canMove"
          @click="submitAction('move_to_position')"
        >
          执行目标位置
        </button>
        <button
          v-if="gripper.mode === 'simulation' && gripper.supportsStop"
          type="button"
          class="col-span-2 rounded border border-rose-900/70 bg-rose-950/30 px-3 py-2 text-xs font-bold text-rose-300 transition hover:bg-rose-950/60 disabled:cursor-not-allowed disabled:opacity-40"
          :disabled="!canStop"
          @click="submitAction('stop')"
        >
          停止仿真动作
        </button>
      </div>

      <p v-if="gripper.mode === 'physical'" class="mt-3 rounded border border-amber-900/40 bg-amber-950/20 px-3 py-2 text-[10px] leading-4 text-amber-300">
        真机无软件停止接口。运动异常时使用现场独立急停；“立即锁定”只阻止后续指令。
      </p>

      <p
        v-if="operationMessage"
        class="mt-3 rounded border px-3 py-2 text-xs"
        :class="operationState === 'failed' ? 'border-rose-900/40 bg-rose-950/20 text-rose-400' : (operationState === 'moving' || operationState === 'submitting' ? 'border-amber-900/40 bg-amber-950/20 text-amber-300' : 'border-emerald-900/40 bg-emerald-950/20 text-emerald-400')"
        role="status"
      >
        {{ operationMessage }}
      </p>
    </template>

    <div v-else-if="isLoading" class="flex flex-1 items-center justify-center py-12 text-xs text-slate-500" role="status">
      正在读取夹爪状态...
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
        :aria-labelledby="confirmationMode === 'arm' ? 'gripper-arm-title' : 'gripper-initialize-title'"
      >
        <template v-if="confirmationMode === 'arm'">
          <h3 id="gripper-arm-title" class="text-sm font-bold text-slate-100">确认解锁夹爪控制</h3>
          <div class="mt-4 space-y-3">
            <label class="flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
              <input v-model="workcellClear" type="checkbox" class="mt-0.5 accent-emerald-500" />
              工作区已清空，夹爪运动不会碰撞人员或物体。
            </label>
            <label class="flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
              <input v-model="emergencyStopVerified" type="checkbox" class="mt-0.5 accent-emerald-500" />
              现场独立急停可用，并且我知道其位置。
            </label>
          </div>
        </template>
        <template v-else>
          <h3 id="gripper-initialize-title" class="text-sm font-bold text-slate-100">确认执行夹爪初始化</h3>
          <p class="mt-3 text-xs leading-5 text-amber-300">初始化会让夹爪运动。确认夹爪行程内无障碍物后再继续。</p>
          <label class="mt-4 flex cursor-pointer items-start gap-3 text-xs leading-5 text-slate-300">
            <input v-model="initializationApproved" type="checkbox" class="mt-0.5 accent-emerald-500" />
            已再次检查夹爪行程，可以执行普通初始化。
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
