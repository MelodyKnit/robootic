import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  RobotApiError,
  armRobot,
  disarmRobot,
  enableRobot,
  executeJointMovePreview,
  getRobotStatus,
  listRobots,
  previewJointMove,
  reconnectRobot,
  type ArmRobotRequest,
  type JointMovePreview,
  type JointMovePreviewRequest,
  type RobotStatus,
} from '../api/robot'

const statusRefreshMilliseconds = 1_000
const clockRefreshMilliseconds = 250

export type RobotOperationState = 'idle' | 'submitting' | 'moving' | 'completed' | 'failed'

/**
 * Owns one browser-local JAKA authorization, status polling, and single-operation state.
 * The token intentionally remains only in memory so a refresh or tab change cannot
 * restore physical control without a new operator confirmation.
 */
export function useJakaControl() {
  const robot = ref<RobotStatus | null>(null)
  const errorMessage = ref<string | null>(null)
  const operationMessage = ref<string | null>(null)
  const operationState = ref<RobotOperationState>('idle')
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

  function synchronizeStatus(status: RobotStatus): void {
    robot.value = status
    if (
      !status.connected ||
      !status.controlsEnabled ||
      !status.manualMotionEnabled ||
      status.moving ||
      status.faulted ||
      status.emergencyStopped
    ) {
      clearAuthorization()
      return
    }
    if (armToken.value !== null) {
      if (status.armedUntil === null) {
        clearAuthorization()
      } else {
        armExpiresAt.value = status.armedUntil
      }
    }
  }

  async function load(session: number): Promise<void> {
    requestController?.abort()
    requestController = new AbortController()
    const robots = await listRobots(requestController.signal)
    if (!active || session !== sessionVersion) {
      return
    }
    if (robots.length === 0) {
      robot.value = null
      return
    }
    if (robots.length > 1) {
      throw new RobotApiError(409, 'multiple_robots', '网页控制暂时只支持一个机械臂。')
    }
    synchronizeStatus(robots[0])
  }

  async function refreshStatus(session: number): Promise<void> {
    const robotId = robot.value?.id
    // A physical JAKA joint_move blocks its SDK session. Skip this polling cycle
    // while a manual operation owns the service instead of stacking status reads.
    if (!active || session !== sessionVersion || robotId === undefined || isBusy.value) {
      return
    }
    try {
      requestController?.abort()
      requestController = new AbortController()
      const status = await getRobotStatus(robotId, requestController.signal)
      if (active && session === sessionVersion && robot.value?.id === robotId) {
        synchronizeStatus(status)
        errorMessage.value = status.lastError
      }
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        clearAuthorization()
        errorMessage.value = displayError(error)
        if (robot.value !== null) {
          robot.value = {
            ...robot.value,
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

  async function refreshAfterOperation(): Promise<void> {
    if (active) {
      await refreshStatus(sessionVersion)
    }
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
        operationMessage.value = '机械臂控制授权已过期。'
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
    const robotId = robot.value?.id
    if (robotId === undefined || isBusy.value) {
      return
    }
    clearAuthorization()
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在重新连接机械臂...'
    try {
      synchronizeStatus(await reconnectRobot(robotId))
      operationState.value = 'completed'
      operationMessage.value = '机械臂连接已刷新。'
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      if (robot.value !== null) {
        robot.value = { ...robot.value, connected: false, lastError: errorMessage.value }
      }
    } finally {
      isBusy.value = false
      void refreshAfterOperation()
    }
  }

  async function arm(request: ArmRobotRequest): Promise<boolean> {
    const robotId = robot.value?.id
    if (robotId === undefined || isBusy.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在申请临时机械臂控制授权...'
    try {
      const result = await armRobot(robotId, request)
      armToken.value = result.armToken
      armExpiresAt.value = result.expiresAt
      synchronizeStatus(result.status)
      operationState.value = 'completed'
      operationMessage.value = '机械臂控制已临时解锁。'
      return true
    } catch (error) {
      clearAuthorization()
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
      void refreshAfterOperation()
    }
  }

  async function disarm(): Promise<void> {
    const robotId = robot.value?.id
    const token = armToken.value
    clearAuthorization()
    operationState.value = 'idle'
    operationMessage.value = '机械臂控制已锁定。'
    if (robotId === undefined || token === null) {
      return
    }
    try {
      await disarmRobot(robotId, token)
    } catch (error) {
      errorMessage.value = displayError(error)
    }
  }

  function releaseAuthorizationOnPageExit(): void {
    const robotId = robot.value?.id
    const token = armToken.value
    clearAuthorization()
    if (robotId === undefined || token === null) {
      return
    }
    void disarmRobot(robotId, token, true).catch(() => undefined)
  }

  async function enable(): Promise<boolean> {
    const robotId = robot.value?.id
    const token = armToken.value
    if (robotId === undefined || token === null || isBusy.value || !isArmed.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在请求伺服使能...'
    try {
      const result = await enableRobot(robotId, token, createIdempotencyKey())
      synchronizeStatus(result.status)
      operationState.value = 'completed'
      operationMessage.value = '机械臂伺服已使能。'
      return true
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
      void refreshAfterOperation()
    }
  }

  async function createPreview(request: JointMovePreviewRequest): Promise<JointMovePreview | null> {
    const robotId = robot.value?.id
    const token = armToken.value
    if (robotId === undefined || token === null || isBusy.value || !isArmed.value) {
      return null
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'submitting'
    operationMessage.value = '正在生成关节运动预览...'
    try {
      const preview = await previewJointMove(robotId, token, request)
      synchronizeStatus(preview.status)
      operationState.value = 'completed'
      operationMessage.value = '已生成运动预览，尚未发送关节运动。'
      return preview
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return null
    } finally {
      isBusy.value = false
    }
  }

  async function executePreview(previewId: string): Promise<boolean> {
    const robotId = robot.value?.id
    const token = armToken.value
    if (robotId === undefined || token === null || isBusy.value || !isArmed.value) {
      return false
    }
    isBusy.value = true
    errorMessage.value = null
    operationState.value = 'moving'
    operationMessage.value = '关节运动执行中...'
    try {
      const result = await executeJointMovePreview(robotId, token, createIdempotencyKey(), previewId)
      synchronizeStatus(result.status)
      operationState.value = 'completed'
      operationMessage.value = '关节运动命令已完成。'
      return true
    } catch (error) {
      operationState.value = 'failed'
      operationMessage.value = null
      errorMessage.value = displayError(error)
      return false
    } finally {
      isBusy.value = false
      void refreshAfterOperation()
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
    reconnect,
    robot,
  }
}

function createIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayError(error: unknown): string {
  if (error instanceof RobotApiError) {
    return error.message
  }
  return '机械臂控制服务请求失败，请检查配置和设备连接。'
}

function displayListLoadError(error: unknown): string {
  if (error instanceof RobotApiError && error.status === 404) {
    return '机械臂控制接口不可用，请重启服务。'
  }
  return displayError(error)
}
