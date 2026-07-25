import { computed, ref, watch, type ComputedRef } from 'vue'

import {
  CameraApiError,
  getCameraPose,
  updatePoseTarget,
  type PoseTracking,
} from '../api/camera'

// A five-FPS source typically exposes a fresh-pose window of about 200 milliseconds.
const poseRefreshMilliseconds = 200
const poseRequestTimeoutMilliseconds = 1_000

/**
 * Owns the browser's latest-pose polling and target selection state. The composable
 * never opens a camera stream or sends a robot command; it consumes pose metadata
 * produced by the server-side, bounded inference pipeline.
 */
export function usePoseTracking(cameraId: ComputedRef<string | null>) {
  const pose = ref<PoseTracking | null>(null)
  const errorMessage = ref<string | null>(null)
  const isUpdatingTarget = ref(false)
  const trackingEnabled = computed(() => pose.value?.enabled ?? false)

  let active = false
  let requestController: AbortController | undefined
  let refreshTimer: ReturnType<typeof window.setTimeout> | undefined
  let sessionVersion = 0

  /** Starts metadata polling only after the preview has selected one camera. */
  function start(): void {
    active = true
    sessionVersion += 1
    refreshNow(sessionVersion)
  }

  /** Cancels all pending browser work when the preview is unmounted or restarted. */
  function stop(): void {
    active = false
    sessionVersion += 1
    requestController?.abort()
    requestController = undefined
    if (refreshTimer !== undefined) {
      window.clearTimeout(refreshTimer)
      refreshTimer = undefined
    }
  }

  /** Persists a user choice through the explicit pose-target API resource. */
  async function selectTarget(targetJoint: string): Promise<void> {
    const currentCameraId = cameraId.value
    if (currentCameraId === null || isUpdatingTarget.value) {
      return
    }

    isUpdatingTarget.value = true
    try {
      const nextPose = await updatePoseTarget(currentCameraId, targetJoint)
      if (active && cameraId.value === currentCameraId) {
        pose.value = nextPose
        errorMessage.value = null
      }
    } catch (error) {
      if (active && cameraId.value === currentCameraId) {
        errorMessage.value = displayPoseError(error)
      }
    } finally {
      isUpdatingTarget.value = false
    }
  }

  /** Retrieves a cached pose snapshot then schedules the next bounded poll. */
  async function refreshNow(session: number): Promise<void> {
    const currentCameraId = cameraId.value
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
    }, poseRequestTimeoutMilliseconds)
    try {
      const nextPose = await getCameraPose(currentCameraId, controller.signal)
      if (!active || session !== sessionVersion || currentCameraId !== cameraId.value) {
        return
      }
      pose.value = nextPose
      errorMessage.value = null
    } catch (error) {
      if ((requestTimedOut || !isAbortError(error)) && active && session === sessionVersion) {
        if (error instanceof CameraApiError && error.code === 'pose_tracking_disabled') {
          pose.value = null
        } else {
          markPoseUnavailable()
        }
        errorMessage.value = displayPoseError(error)
      }
    } finally {
      window.clearTimeout(timeout)
      if (requestController === controller) {
        requestController = undefined
      }
      scheduleRefresh(session)
    }
  }

  /** Keeps exactly one polling timer active for the mounted camera preview. */
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
    }, poseRefreshMilliseconds)
  }

  /** Prevents a failed metadata read from leaving a stale skeleton over live MJPEG pixels. */
  function markPoseUnavailable(): void {
    if (pose.value === null) {
      return
    }

    pose.value = {
      ...pose.value,
      overlayFresh: false,
      valid: false,
      target: null,
      motion: null,
      reason: '人体姿态状态暂时不可用。',
    }
  }

  watch(cameraId, () => {
    pose.value = null
    errorMessage.value = null
    if (active) {
      sessionVersion += 1
      refreshNow(sessionVersion)
    }
  })

  return {
    errorMessage,
    isUpdatingTarget,
    pose,
    selectTarget,
    start,
    stop,
    trackingEnabled,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayPoseError(error: unknown): string {
  if (error instanceof CameraApiError) {
    if (error.code === 'pose_tracking_disabled') {
      return '人体姿态追踪尚未在当前本机配置中启用。'
    }
    return error.message
  }
  return '人体姿态状态暂时不可用。'
}
