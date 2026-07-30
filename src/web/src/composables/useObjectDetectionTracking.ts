import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import {
  CameraApiError,
  getCameraDetections,
  selectObjectDetectionModel,
  type DetectionTracking,
} from '../api/camera'

const detectionRefreshMilliseconds = 500
const detectionRequestTimeoutMilliseconds = 1_000

/**
 * Polls only the generic detector cache. The composable never opens a camera
 * stream, changes camera parameters, or sends a robot/gripper command.
 */
export function useObjectDetectionTracking(
  cameraId: ComputedRef<string | null>,
  sourceRevision: Readonly<Ref<number>>,
) {
  const detection = ref<DetectionTracking | null>(null)
  const errorMessage = ref<string | null>(null)
  const isSelectingModel = ref(false)
  const trackingEnabled = computed(() => detection.value?.enabled ?? false)

  let active = false
  let requestController: AbortController | undefined
  let modelSelectionController: AbortController | undefined
  let refreshTimer: ReturnType<typeof window.setTimeout> | undefined
  let sessionVersion = 0

  /** Starts bounded metadata polling after the preview owns a logical camera. */
  function start(): void {
    stop()
    active = true
    sessionVersion += 1
    void refreshNow(sessionVersion)
  }

  /** Cancels browser work and prevents stale detector data from reaching the UI. */
  function stop(): void {
    active = false
    sessionVersion += 1
    requestController?.abort()
    requestController = undefined
    modelSelectionController?.abort()
    modelSelectionController = undefined
    isSelectingModel.value = false
    if (refreshTimer !== undefined) {
      window.clearTimeout(refreshTimer)
      refreshTimer = undefined
    }
    detection.value = null
    errorMessage.value = null
  }

  /** Reads one server-owned detector snapshot and schedules the next passive poll. */
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
    }, detectionRequestTimeoutMilliseconds)

    try {
      const nextDetection = await getCameraDetections(currentCameraId, controller.signal)
      if (
        !active ||
        session !== sessionVersion ||
        currentCameraId !== cameraId.value ||
        currentSourceRevision !== sourceRevision.value
      ) {
        return
      }
      detection.value = nextDetection
      errorMessage.value = null
    } catch (error) {
      if (
        (requestTimedOut || !isAbortError(error)) &&
        active &&
        session === sessionVersion &&
        currentSourceRevision === sourceRevision.value
      ) {
        markDetectionUnavailable()
        errorMessage.value = displayDetectionError(error)
      }
    } finally {
      window.clearTimeout(timeout)
      if (requestController === controller) {
        requestController = undefined
      }
      scheduleRefresh(session)
    }
  }

  /** Keeps a single bounded polling timer alive for this preview instance. */
  function scheduleRefresh(session: number): void {
    if (!active || session !== sessionVersion) {
      return
    }
    if (refreshTimer !== undefined) {
      window.clearTimeout(refreshTimer)
    }
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      void refreshNow(session)
    }, detectionRefreshMilliseconds)
  }

  /** Selects a model through the backend-owned allow-list and updates the cache atomically. */
  async function selectModel(modelId: string): Promise<void> {
    const currentCameraId = cameraId.value
    const currentSourceRevision = sourceRevision.value
    if (
      !active ||
      currentCameraId === null ||
      isSelectingModel.value ||
      !detection.value?.models.some((model) => model.modelId === modelId && model.available) ||
      detection.value.selectedModelId === modelId
    ) {
      return
    }

    const session = sessionVersion
    modelSelectionController?.abort()
    const controller = new AbortController()
    modelSelectionController = controller
    isSelectingModel.value = true
    errorMessage.value = null
    try {
      const nextDetection = await selectObjectDetectionModel(currentCameraId, modelId, controller.signal)
      if (
        !active ||
        session !== sessionVersion ||
        currentCameraId !== cameraId.value ||
        currentSourceRevision !== sourceRevision.value
      ) {
        return
      }
      detection.value = nextDetection
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        errorMessage.value = displayDetectionError(error)
      }
    } finally {
      if (modelSelectionController === controller) {
        modelSelectionController = undefined
      }
      if (active && session === sessionVersion) {
        isSelectingModel.value = false
      }
    }
  }

  /** Removes old boxes whenever a response no longer belongs to the live source. */
  function markDetectionUnavailable(): void {
    if (detection.value === null) {
      return
    }
    detection.value = {
      ...detection.value,
      overlayFresh: false,
      valid: false,
      detections: [],
      reason: '物品检测状态暂时不可用。',
    }
  }

  watch([cameraId, sourceRevision], () => {
    detection.value = null
    errorMessage.value = null
    if (active) {
      sessionVersion += 1
      void refreshNow(sessionVersion)
    }
  })

  return {
    detection,
    errorMessage,
    isSelectingModel,
    selectModel,
    start,
    stop,
    trackingEnabled,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayDetectionError(error: unknown): string {
  if (error instanceof CameraApiError) {
    const labels: Record<string, string> = {
      object_detection_disabled: '通用物品检测尚未启用。',
      detection_model_unavailable: '所选检测模型当前不可用。',
      detection_model_selection_disabled: '当前配置不允许切换检测模型。',
    }
    return labels[error.code] ?? error.message
  }
  return '物品检测状态暂时不可用。'
}
