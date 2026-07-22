import { computed, ref } from 'vue'

import {
  CameraApiError,
  getCameraStatus,
  getCameraStreamUrl,
  listCameras,
  type CameraStatus,
} from '../api/camera'

const statusRefreshMilliseconds = 2_000
const reconnectDelayMilliseconds = 1_000

/**
 * Owns the UI state for the one-camera preview screen. The composable separates
 * API polling and MJPEG recovery from the visual component, which keeps future
 * camera selection or richer status displays localized to this boundary.
 */
export function useCameraPreview() {
  const camera = ref<CameraStatus | null>(null)
  const selectedCameraId = ref<string | null>(null)
  const errorMessage = ref<string | null>(null)
  const isLoading = ref(false)
  const frameAvailable = ref(false)
  const streamAttempt = ref(0)
  const streamUrl = computed(() => {
    if (selectedCameraId.value === null) {
      return ''
    }

    const baseUrl = getCameraStreamUrl(selectedCameraId.value)
    const separator = baseUrl.includes('?') ? '&' : '?'
    return `${baseUrl}${separator}attempt=${streamAttempt.value}`
  })

  let active = false
  let statusTimer: ReturnType<typeof window.setTimeout> | undefined
  let reconnectTimer: ReturnType<typeof window.setTimeout> | undefined
  let requestController: AbortController | undefined
  let sessionVersion = 0

  /** Starts a new status request while cancelling any stale request first. */
  async function requestCameraList(session: number): Promise<void> {
    requestController?.abort()
    requestController = new AbortController()

    const cameras = await listCameras(requestController.signal)
    if (cameras.length === 0) {
      throw new CameraApiError(503, 'camera_unavailable', '当前没有可预览的相机。')
    }
    if (cameras.length > 1) {
      throw new CameraApiError(409, 'multiple_cameras', '当前配置包含多个相机，网页预览暂不支持选择。')
    }

    if (!active || session !== sessionVersion) {
      return
    }

    const nextCamera = cameras[0]
    selectedCameraId.value = nextCamera.cameraId
    camera.value = nextCamera
    frameAvailable.value = false
    streamAttempt.value += 1
  }

  /** Polls metadata independently from the MJPEG connection. */
  async function refreshStatus(session: number): Promise<void> {
    const cameraId = selectedCameraId.value
    if (!active || session !== sessionVersion || cameraId === null) {
      return
    }

    try {
      requestController?.abort()
      requestController = new AbortController()
      const nextCamera = await getCameraStatus(cameraId, requestController.signal)
      if (!active || session !== sessionVersion || selectedCameraId.value !== cameraId) {
        return
      }

      camera.value = nextCamera
      errorMessage.value = nextCamera.error?.message ?? null
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        errorMessage.value = displayError(error)
      }
    }
  }

  /** Keeps a single metadata timer alive for the mounted preview instance. */
  function scheduleStatusRefresh(session: number): void {
    if (!active || session !== sessionVersion) {
      return
    }

    statusTimer = window.setTimeout(async () => {
      await refreshStatus(session)
      scheduleStatusRefresh(session)
    }, statusRefreshMilliseconds)
  }

  /** Starts the read-only screen without issuing commands to robot hardware. */
  async function start(): Promise<void> {
    stop()
    const session = ++sessionVersion
    active = true
    isLoading.value = true
    errorMessage.value = null

    try {
      await requestCameraList(session)
      if (active && session === sessionVersion) {
        scheduleStatusRefresh(session)
      }
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        errorMessage.value = displayError(error)
      }
    } finally {
      if (active) {
        isLoading.value = false
      }
    }
  }

  /** Releases timers and fetches when the component is unmounted or restarted. */
  function stop(): void {
    active = false
    sessionVersion += 1
    requestController?.abort()
    requestController = undefined

    if (statusTimer !== undefined) {
      window.clearTimeout(statusTimer)
      statusTimer = undefined
    }
    if (reconnectTimer !== undefined) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = undefined
    }
  }

  /** Marks the active stream as usable after its first decoded MJPEG frame. */
  function onFrameLoaded(): void {
    frameAvailable.value = true
    errorMessage.value = camera.value?.error?.message ?? null
  }

  /** Replaces a failed image element after a short delay to reopen the MJPEG stream. */
  function onStreamError(): void {
    if (!active || selectedCameraId.value === null) {
      return
    }

    frameAvailable.value = false
    errorMessage.value = '画面流连接中断，正在重新连接。'
    if (reconnectTimer !== undefined) {
      return
    }

    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = undefined
      if (active) {
        streamAttempt.value += 1
      }
    }, reconnectDelayMilliseconds)
  }

  return {
    camera,
    errorMessage,
    frameAvailable,
    isLoading,
    start,
    stop,
    streamAttempt,
    streamUrl,
    onFrameLoaded,
    onStreamError,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayError(error: unknown): string {
  if (error instanceof CameraApiError) {
    return error.message
  }

  return '无法连接相机预览服务。'
}
