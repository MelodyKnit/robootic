import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import {
  CameraApiError,
  getCameraVisionAnalysis,
  type VisionAnalysis,
} from '../api/camera'

const analysisRefreshMilliseconds = 500

/**
 * Polls cached analysis metadata from the existing preview service. This composable
 * never opens an MJPEG stream, changes camera parameters, or triggers model inference.
 */
export function useVisionAnalysis(cameraId: ComputedRef<string | null>, sourceRevision: Readonly<Ref<number>>) {
  const analysis = ref<VisionAnalysis | null>(null)
  const errorMessage = ref<string | null>(null)
  const analysisAvailable = computed(() => analysis.value !== null)

  let active = false
  let requestController: AbortController | undefined
  let refreshTimer: ReturnType<typeof window.setTimeout> | undefined
  let sessionVersion = 0

  /** Starts one bounded polling sequence after a camera has been selected. */
  function start(): void {
    active = true
    sessionVersion += 1
    refreshNow(sessionVersion)
  }

  /** Cancels pending HTTP work and stops scheduling new analysis reads. */
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

  /** Reads one server-side cache entry then schedules the next passive refresh. */
  async function refreshNow(session: number): Promise<void> {
    const currentCameraId = cameraId.value
    const currentSourceRevision = sourceRevision.value
    if (!active || session !== sessionVersion || currentCameraId === null) {
      scheduleRefresh(session)
      return
    }

    requestController?.abort()
    requestController = new AbortController()
    try {
      const nextAnalysis = await getCameraVisionAnalysis(currentCameraId, requestController.signal)
      if (
        !active ||
        session !== sessionVersion ||
        currentCameraId !== cameraId.value ||
        currentSourceRevision !== sourceRevision.value
      ) {
        return
      }
      analysis.value = nextAnalysis
      errorMessage.value = null
    } catch (error) {
      if (
        !isAbortError(error) &&
        active &&
        session === sessionVersion &&
        currentSourceRevision === sourceRevision.value
      ) {
        errorMessage.value = displayAnalysisError(error)
      }
    } finally {
      scheduleRefresh(session)
    }
  }

  /** Keeps at most one timer alive for the mounted camera preview. */
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
    }, analysisRefreshMilliseconds)
  }

  watch([cameraId, sourceRevision], () => {
    analysis.value = null
    errorMessage.value = null
    if (active) {
      sessionVersion += 1
      refreshNow(sessionVersion)
    }
  })

  return {
    analysis,
    analysisAvailable,
    errorMessage,
    start,
    stop,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayAnalysisError(error: unknown): string {
  if (error instanceof CameraApiError) {
    return error.message
  }
  return '视觉分析状态暂时不可用。'
}
