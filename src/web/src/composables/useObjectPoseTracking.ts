import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import {
  CameraApiError,
  getCameraObjectPoses,
  type ObjectPoseTracking,
} from '../api/camera'

const objectPoseRefreshMilliseconds = 500
const objectPoseRequestTimeoutMilliseconds = 1_000

/**
 * Polls only the passive object-pose cache. It never opens another image stream,
 * changes camera settings, or sends a robot or gripper command.
 */
export function useObjectPoseTracking(cameraId: ComputedRef<string | null>, sourceRevision: Readonly<Ref<number>>) {
  const objectPose = ref<ObjectPoseTracking | null>(null)
  const errorMessage = ref<string | null>(null)
  const trackingEnabled = computed(() => objectPose.value?.enabled ?? false)

  let active = false
  let requestController: AbortController | undefined
  let refreshTimer: ReturnType<typeof window.setTimeout> | undefined
  let sessionVersion = 0

  /** Starts a bounded metadata poll only after the preview owns a logical camera. */
  function start(): void {
    active = true
    sessionVersion += 1
    refreshNow(sessionVersion)
  }

  /** Cancels browser work and prevents a stale response from reaching the live overlay. */
  function stop(): void {
    active = false
    sessionVersion += 1
    requestController?.abort()
    requestController = undefined
    if (refreshTimer !== undefined) {
      window.clearTimeout(refreshTimer)
      refreshTimer = undefined
    }
    objectPose.value = null
    errorMessage.value = null
  }

  /** Reads one server-owned cache snapshot and then schedules the next passive poll. */
  async function refreshNow(session: number): Promise<void> {
    const currentCameraId = cameraId.value
    const currentSourceRevision = sourceRevision.value
    if (!active || session !== sessionVersion || currentCameraId === null) {
      scheduleRefresh(session)
      return
    }

    requestController?.abort()
    const controller = new AbortController()
    requestController = controller
    let requestTimedOut = false
    const timeout = window.setTimeout(() => {
      requestTimedOut = true
      controller.abort()
    }, objectPoseRequestTimeoutMilliseconds)

    try {
      const nextObjectPose = await getCameraObjectPoses(currentCameraId, controller.signal)
      if (
        !active ||
        session !== sessionVersion ||
        currentCameraId !== cameraId.value ||
        currentSourceRevision !== sourceRevision.value
      ) {
        return
      }
      objectPose.value = nextObjectPose
      errorMessage.value = null
    } catch (error) {
      if (
        (requestTimedOut || !isAbortError(error)) &&
        active &&
        session === sessionVersion &&
        currentSourceRevision === sourceRevision.value
      ) {
        markObjectPoseUnavailable()
        errorMessage.value = displayObjectPoseError(error)
      }
    } finally {
      window.clearTimeout(timeout)
      if (requestController === controller) {
        requestController = undefined
      }
      scheduleRefresh(session)
    }
  }

  /** Keeps one polling timer alive for this preview instance. */
  function scheduleRefresh(session: number): void {
    if (!active || session !== sessionVersion) {
      return
    }
    if (refreshTimer !== undefined) {
      window.clearTimeout(refreshTimer)
    }
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      refreshNow(session)
    }, objectPoseRefreshMilliseconds)
  }

  /** Removes prior geometry when a request can no longer prove it belongs to the live frame. */
  function markObjectPoseUnavailable(): void {
    if (objectPose.value === null) {
      return
    }
    objectPose.value = {
      ...objectPose.value,
      overlayFresh: false,
      valid: false,
      objects: [],
      reason: '工件位姿状态暂时不可用。',
    }
  }

  watch([cameraId, sourceRevision], () => {
    objectPose.value = null
    errorMessage.value = null
    if (active) {
      sessionVersion += 1
      refreshNow(sessionVersion)
    }
  })

  return {
    errorMessage,
    objectPose,
    start,
    stop,
    trackingEnabled,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayObjectPoseError(error: unknown): string {
  if (error instanceof CameraApiError) {
    if (error.code === 'object_pose_disabled') {
      return '工件位姿识别尚未在当前本机配置中启用。'
    }
    return error.message
  }
  return '工件位姿状态暂时不可用。'
}
