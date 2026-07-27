import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  GripperApiError,
  armGripper,
  disarmGripper,
  getGripperStatus,
  initializeGripper,
  listGrippers,
  reconnectGripper,
  submitGripperCommand,
  type GripperCommandRequest,
  type GripperStatus,
} from '../api/gripper'

const statusRefreshMilliseconds = 1_000
const clockRefreshMilliseconds = 250

export type GripperOperationState = 'idle' | 'submitting' | 'moving' | 'completed' | 'failed'

/**
 * Owns one browser-local gripper authorization and serializes all manual actions.
 * The authorization token intentionally remains in memory so refreshing the page
 * cannot restore physical control without fresh operator confirmation.
 */
export function useGripperControl() {
  const gripper = ref<GripperStatus | null>(null)
  const errorMessage = ref<string | null>(null)
  const operationMessage = ref<string | null>(null)
  const operationState = ref<GripperOperationState>('idle')
  const isLoading = ref(false)
  const isBusy = ref(false)
  const armToken = ref<string | null>(null)
  const armExpiresAt = ref<number | null>(null)
  const nowSeconds = ref(Date.now() / 1_000)

  let active = false
  let sessionVersion = 0
  let statusTimer: ReturnType<typeof window.setTimeout> | undefined
  let clockTimer: ReturnType<typeof window.setInterval> | undefined
  let requestController: AbortController | undefined

  const isArmed = computed(
    () => armToken.value !== null && armExpiresAt.value !== null && armExpiresAt.value > nowSeconds.value,
  )
  const armSecondsRemaining = computed(() => {
    if (!isArmed.value || armExpiresAt.value === null) {
      return 0
    }
    return Math.max(0, Math.ceil(armExpiresAt.value - nowSeconds.value))
  })

  function clearAuthorization(): void {
    armToken.value = null
    armExpiresAt.value = null
  }

  function synchronizeStatus(status: GripperStatus): void {
    gripper.value = status
    if (!status.connected || !status.controlsEnabled) {
      clearAuthorization()
    } else if (armToken.value !== null && status.armedUntil !== null) {
      armExpiresAt.value = status.armedUntil
    }

    if (operationState.value === 'moving' && !status.moving) {
      operationState.value = 'completed'
      operationMessage.value = '夹爪动作已完成。'
    }
  }

  async function load(session: number): Promise<void> {
    requestController?.abort()
    requestController = new AbortController()
    const grippers = await listGrippers(requestController.signal)
    if (!active || session !== sessionVersion) {
      return
    }
    if (grippers.length === 0) {
      // A successful empty collection is the documented no-gripper state, not a transport failure.
      gripper.value = null
      return
    }
    if (grippers.length > 1) {
      throw new GripperApiError(409, 'multiple_grippers', '网页控制暂时只支持一个夹爪。')
    }
    synchronizeStatus(grippers[0])
  }

  async function refreshStatus(session: number): Promise<void> {
    const gripperId = gripper.value?.id
    if (!active || session !== sessionVersion || gripperId === undefined) {
      return
    }

    try {
      requestController?.abort()
      requestController = new AbortController()
      const status = await getGripperStatus(gripperId, requestController.signal)
      if (active && session === sessionVersion && gripper.value?.id === gripperId) {
        synchronizeStatus(status)
        if (status.lastError !== null) {
          errorMessage.value = status.lastError
        }
      }
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        clearAuthorization()
        errorMessage.value = displayError(error)
        if (gripper.value !== null) {
          gripper.value = {
            ...gripper.value,
            connected: false,
            lastError: errorMessage.value,
          }
        }
      }
    }
  }

  function scheduleStatusRefresh(session: number): void {
    if (!active || session !== sessionVersion) {
      return
    }
    statusTimer = window.setTimeout(async () => {
      await refreshStatus(session)
      scheduleStatusRefresh(session)
    }, statusRefreshMilliseconds)
  }

  async function start(): Promise<void> {
    stop()
    const session = ++sessionVersion
    active = true
    isLoading.value = true
    errorMessage.value = null
    operationMessage.value = null
    operationState.value = 'idle'
    clockTimer = window.setInterval(() => {
      nowSeconds.value = Date.now() / 1_000
      if (armToken.value !== null && !isArmed.value) {
        clearAuthorization()
        operationMessage.value = '控制授权已过期。'
      }
    }, clockRefreshMilliseconds)

    try {
      await load(session)
      scheduleStatusRefresh(session)
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        errorMessage.value = displayListLoadError(error)
      }
    } finally {
      if (active && session === sessionVersion) {
        isLoading.value = false
      }
    }
  }

  function stop(): void {
    active = false
    sessionVersion += 1
    requestController?.abort()
    requestController = undefined
    if (statusTimer !== undefined) {
      window.clearTimeout(statusTimer)
      statusTimer = undefined
    }
    if (clockTimer !== undefined) {
      window.clearInterval(clockTimer)
      clockTimer = undefined
    }
    clearAuthorization()
  }

  async function reconnect(): Promise<void> {
    const gripperId = gripper.value?.id
    if (gripperId === undefined || isBusy.value) {
      return
    }
    clearAuthorization()
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在重新连接夹爪...'
    try {
      synchronizeStatus(await reconnectGripper(gripperId))
      operationState.value = 'completed'
      operationMessage.value = '夹爪连接已刷新。'
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      if (gripper.value !== null) {
        gripper.value = { ...gripper.value, connected: false, lastError: errorMessage.value }
      }
    } finally {
      isBusy.value = false
    }
  }

  async function arm(workcellClear: boolean, emergencyStopVerified: boolean): Promise<boolean> {
    const gripperId = gripper.value?.id
    if (gripperId === undefined || isBusy.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在申请临时控制授权...'
    try {
      const result = await armGripper(gripperId, { workcellClear, emergencyStopVerified })
      armToken.value = result.armToken
      armExpiresAt.value = result.expiresAt
      synchronizeStatus(result.status)
      operationState.value = 'completed'
      operationMessage.value = '夹爪控制已临时解锁。'
      return true
    } catch (error) {
      clearAuthorization()
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
    }
  }

  async function disarm(): Promise<void> {
    const gripperId = gripper.value?.id
    const token = armToken.value
    clearAuthorization()
    operationState.value = 'idle'
    operationMessage.value = '夹爪控制已锁定。'
    if (gripperId === undefined || token === null) {
      return
    }
    try {
      await disarmGripper(gripperId, token)
    } catch (error) {
      errorMessage.value = displayError(error)
    }
  }

  function releaseAuthorizationOnPageExit(): void {
    const gripperId = gripper.value?.id
    const token = armToken.value
    clearAuthorization()
    if (gripperId === undefined || token === null) {
      return
    }
    void disarmGripper(gripperId, token, true).catch(() => undefined)
  }

  async function initialize(): Promise<boolean> {
    const gripperId = gripper.value?.id
    const token = armToken.value
    if (gripperId === undefined || token === null || isBusy.value || !isArmed.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '初始化指令已提交...'
    try {
      const status = await initializeGripper(gripperId, token, createIdempotencyKey())
      synchronizeStatus(status)
      operationState.value = status.initializing ? 'moving' : 'completed'
      operationMessage.value = status.initializing ? '夹爪正在初始化。' : '夹爪初始化已完成。'
      return true
    } catch (error) {
      clearAuthorization()
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
    }
  }

  async function execute(command: GripperCommandRequest): Promise<boolean> {
    const gripperId = gripper.value?.id
    const token = armToken.value
    if (gripperId === undefined || token === null || isBusy.value || !isArmed.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在提交夹爪动作...'
    try {
      const status = await submitGripperCommand(gripperId, token, createIdempotencyKey(), command)
      synchronizeStatus(status)
      operationState.value = status.moving ? 'moving' : 'completed'
      operationMessage.value = status.moving ? '设备已接收，夹爪正在运动。' : '夹爪动作已完成。'
      return true
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
    }
  }

  onMounted(() => {
    window.addEventListener('pagehide', releaseAuthorizationOnPageExit)
    void start()
  })

  onBeforeUnmount(() => {
    window.removeEventListener('pagehide', releaseAuthorizationOnPageExit)
    releaseAuthorizationOnPageExit()
    stop()
  })

  return {
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
    start,
  }
}

function createIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayError(error: unknown): string {
  if (error instanceof GripperApiError) {
    return error.message
  }
  return '夹爪控制服务请求失败，请检查配置和设备连接。'
}

function displayListLoadError(error: unknown): string {
  if (error instanceof GripperApiError && error.status === 404) {
    return '夹爪控制接口不可用，请重启服务。'
  }
  return displayError(error)
}
