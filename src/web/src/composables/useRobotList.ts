import { computed, ref } from 'vue'

import {
  getRobotStatus,
  listRobots,
  RobotApiError,
  type RobotStatus,
} from '../api/robot'

const statusRefreshMilliseconds = 2_000

/**
 * 管理机械臂列表和状态的Composable
 * 提供机械臂列表获取、状态轮询和选择功能
 */
export function useRobotList() {
  const robots = ref<RobotStatus[]>([])
  const selectedRobotId = ref<string | null>(null)
  const selectedRobot = computed(() =>
    robots.value.find((r) => r.id === selectedRobotId.value) ?? null
  )
  const errorMessage = ref<string | null>(null)
  const isLoading = ref(false)

  let active = false
  let statusTimer: ReturnType<typeof window.setTimeout> | undefined
  let listRequestController: AbortController | undefined
  let statusRequestController: AbortController | undefined
  let sessionVersion = 0

  /** 加载机械臂列表 */
  async function loadRobotList(session: number): Promise<void> {
    if (!active || session !== sessionVersion) {
      return
    }

    try {
      listRequestController?.abort()
      const controller = new AbortController()
      listRequestController = controller

      const robotList = await listRobots(controller.signal)

      if (!active || session !== sessionVersion) {
        return
      }

      robots.value = robotList
      errorMessage.value = null

      // 如果有机械臂但没有选中任何一个，自动选中第一个
      if (robotList.length > 0 && selectedRobotId.value === null) {
        selectedRobotId.value = robotList[0].id
      }

      // 如果当前选中的机械臂不在列表中，清除选择
      if (
        selectedRobotId.value !== null &&
        !robotList.some((r) => r.id === selectedRobotId.value)
      ) {
        selectedRobotId.value = null
      }
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        errorMessage.value = displayError(error)
        console.error('加载机械臂列表失败:', error)
      }
    }
  }

  /** 轮询选中机械臂的状态 */
  async function refreshStatus(session: number): Promise<void> {
    const robotId = selectedRobotId.value
    if (!active || session !== sessionVersion || robotId === null) {
      return
    }

    try {
      statusRequestController?.abort()
      const controller = new AbortController()
      statusRequestController = controller

      const status = await getRobotStatus(robotId, controller.signal)

      if (
        !active ||
        session !== sessionVersion ||
        selectedRobotId.value !== robotId
      ) {
        return
      }

      // 更新列表中对应机械臂的状态
      const index = robots.value.findIndex((r) => r.id === robotId)
      if (index !== -1) {
        robots.value[index] = status
      }
    } catch (error) {
      if (
        !isAbortError(error) &&
        active &&
        session === sessionVersion &&
        selectedRobotId.value === robotId
      ) {
        console.error('刷新机械臂状态失败:', error)
      }
    }
  }

  /** 启动轮询 */
  function scheduleNextPoll(session: number): void {
    if (!active || session !== sessionVersion) {
      return
    }

    statusTimer = window.setTimeout(async () => {
      if (!active || session !== sessionVersion) {
        return
      }

      await refreshStatus(session)
      scheduleNextPoll(session)
    }, statusRefreshMilliseconds)
  }

  /** 启动机械臂列表监控 */
  async function start(): Promise<void> {
    if (active) {
      return
    }

    active = true
    sessionVersion += 1
    isLoading.value = true
    errorMessage.value = null

    const session = sessionVersion

    try {
      await loadRobotList(session)
    } catch (error) {
      if (active && session === sessionVersion) {
        errorMessage.value = displayError(error)
      }
    } finally {
      if (active && session === sessionVersion) {
        isLoading.value = false
      }
    }

    scheduleNextPoll(session)
  }

  /** 停止监控 */
  function stop(): void {
    active = false
    sessionVersion += 1

    if (statusTimer !== undefined) {
      window.clearTimeout(statusTimer)
      statusTimer = undefined
    }

    listRequestController?.abort()
    listRequestController = undefined
    statusRequestController?.abort()
    statusRequestController = undefined
  }

  /** 手动刷新机械臂列表 */
  async function refresh(): Promise<void> {
    if (!active) {
      return
    }

    isLoading.value = true
    errorMessage.value = null

    try {
      await loadRobotList(sessionVersion)
    } catch (error) {
      if (active) {
        errorMessage.value = displayError(error)
      }
    } finally {
      if (active) {
        isLoading.value = false
      }
    }
  }

  /** 选择机械臂 */
  function selectRobot(robotId: string | null): void {
    selectedRobotId.value = robotId
  }

  function isAbortError(error: unknown): boolean {
    return error instanceof Error && error.name === 'AbortError'
  }

  function displayError(error: unknown): string {
    if (error instanceof RobotApiError) {
      return error.message
    }
    if (error instanceof Error) {
      return error.message
    }
    return '未知错误'
  }

  return {
    // 状态
    robots,
    selectedRobotId,
    selectedRobot,
    errorMessage,
    isLoading,

    // 方法
    start,
    stop,
    refresh,
    selectRobot,
  }
}
