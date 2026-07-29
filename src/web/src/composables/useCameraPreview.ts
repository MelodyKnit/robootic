import { computed, ref } from 'vue'

import {
  CameraApiError,
  getCameraStatus,
  getCameraStreamUrl,
  listCameras,
  selectCameraDevice as requestCameraDeviceSelection,
  type CameraCatalog,
  type CameraDevice,
  type CameraError,
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
  const devices = ref<CameraDevice[]>([])
  const selectedCameraId = ref<string | null>(null)
  const selectedDeviceId = ref<string | null>(null)
  const selectionEnabled = ref(false)
  const discoveryError = ref<CameraError | null>(null)
  const statusErrorMessage = ref<string | null>(null)
  const operationErrorMessage = ref<string | null>(null)
  const errorMessage = computed(
    () => operationErrorMessage.value ?? statusErrorMessage.value,
  )
  const isLoading = ref(false)
  const isSelecting = ref(false)
  const frameAvailable = ref(false)
  const streamAttempt = ref(0)
  const sourceRevision = ref(0)
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
  let catalogRequestController: AbortController | undefined
  let statusRequestController: AbortController | undefined
  let selectionRequestController: AbortController | undefined
  let awaitingFrameAfterSelection: number | null | undefined
  let sessionVersion = 0

  /** Selects the sole logical preview pipeline represented by the current page. */
  function logicalCamera(catalog: CameraCatalog): CameraStatus {
    if (catalog.cameras.length === 0) {
      throw new CameraApiError(503, 'camera_unavailable', '当前没有可预览的相机。')
    }
    if (catalog.cameras.length > 1) {
      throw new CameraApiError(409, 'multiple_cameras', '当前配置包含多个逻辑相机，网页暂不支持切换逻辑相机。')
    }
    return catalog.cameras[0]
  }

  /** Applies one server-owned catalog snapshot without rebuilding the MJPEG element. */
  function applyCatalog(catalog: CameraCatalog, trackSourceChange: boolean): void {
    const nextCamera = logicalCamera(catalog)
    const previousDeviceId = selectedDeviceId.value
    const previousFrameAt = camera.value?.latestFrameAt ?? null
    const sourceChanged = trackSourceChange && previousDeviceId !== catalog.selectedDeviceId

    camera.value = nextCamera
    selectedCameraId.value = nextCamera.cameraId
    devices.value = catalog.devices
    selectedDeviceId.value = catalog.selectedDeviceId
    selectionEnabled.value = catalog.selectionEnabled
    discoveryError.value = catalog.discoveryError
    statusErrorMessage.value = nextCamera.error?.message ?? null

    if (sourceChanged) {
      sourceRevision.value += 1
      frameAvailable.value = false
      awaitingFrameAfterSelection = previousFrameAt
    }
  }

  /** Loads discovery metadata while keeping status polling on its own request lane. */
  async function requestCameraList(session: number, trackSourceChange: boolean): Promise<void> {
    catalogRequestController?.abort()
    const controller = new AbortController()
    catalogRequestController = controller

    const catalog = await listCameras(controller.signal)

    if (!active || session !== sessionVersion) {
      return
    }

    applyCatalog(catalog, trackSourceChange)
  }

  /** Polls metadata independently from the MJPEG connection. */
  async function refreshStatus(session: number): Promise<void> {
    const cameraId = selectedCameraId.value
    const deviceId = selectedDeviceId.value
    const revision = sourceRevision.value
    if (!active || session !== sessionVersion || cameraId === null) {
      return
    }

    try {
      statusRequestController?.abort()
      const controller = new AbortController()
      statusRequestController = controller
      const nextCamera = await getCameraStatus(cameraId, controller.signal)
      if (
        !active ||
        session !== sessionVersion ||
        selectedCameraId.value !== cameraId ||
        selectedDeviceId.value !== deviceId ||
        sourceRevision.value !== revision
      ) {
        return
      }

      camera.value = nextCamera
      statusErrorMessage.value = nextCamera.error?.message ?? null
      if (
        awaitingFrameAfterSelection !== undefined &&
        nextCamera.latestFrameAt !== null &&
        (awaitingFrameAfterSelection === null || nextCamera.latestFrameAt > awaitingFrameAfterSelection)
      ) {
        awaitingFrameAfterSelection = undefined
        frameAvailable.value = true
      }
    } catch (error) {
      if (
        !isAbortError(error) &&
        active &&
        session === sessionVersion &&
        selectedDeviceId.value === deviceId &&
        sourceRevision.value === revision
      ) {
        statusErrorMessage.value = displayError(error)
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
    statusErrorMessage.value = null
    operationErrorMessage.value = null
    frameAvailable.value = false
    awaitingFrameAfterSelection = undefined
    streamAttempt.value += 1

    try {
      await requestCameraList(session, false)
      if (active && session === sessionVersion) {
        scheduleStatusRefresh(session)
      }
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        statusErrorMessage.value = displayError(error)
      }
    } finally {
      if (active && session === sessionVersion) {
        isLoading.value = false
      }
    }
  }

  /** Refreshes physical device discovery without reconnecting an unchanged MJPEG source. */
  async function refreshCameras(): Promise<void> {
    if (!active || isLoading.value || isSelecting.value) {
      return
    }

    const session = sessionVersion
    isLoading.value = true
    operationErrorMessage.value = null
    try {
      await requestCameraList(session, true)
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        operationErrorMessage.value = displayError(error)
      }
    } finally {
      if (active && session === sessionVersion) {
        isLoading.value = false
      }
    }
  }

  /** Atomically selects one backend-discovered device while preserving the old source on failure. */
  async function selectCameraDevice(deviceId: string): Promise<void> {
    const cameraId = selectedCameraId.value
    if (
      !active ||
      cameraId === null ||
      !selectionEnabled.value ||
      isSelecting.value ||
      deviceId === selectedDeviceId.value ||
      !devices.value.some((device) => device.deviceId === deviceId)
    ) {
      return
    }

    const session = sessionVersion
    statusRequestController?.abort()
    statusRequestController = undefined
    selectionRequestController?.abort()
    const controller = new AbortController()
    selectionRequestController = controller
    isSelecting.value = true
    operationErrorMessage.value = null
    try {
      const catalog = await requestCameraDeviceSelection(cameraId, deviceId, controller.signal)
      if (!active || session !== sessionVersion || selectedCameraId.value !== cameraId) {
        return
      }
      if (catalog.selectedDeviceId !== deviceId) {
        throw new CameraApiError(502, 'invalid_response', '相机服务未确认所选采集设备。')
      }

      applyCatalog(catalog, true)
      void refreshStatus(session)
    } catch (error) {
      if (!isAbortError(error) && active && session === sessionVersion) {
        operationErrorMessage.value = displayError(error)
      }
    } finally {
      if (active && session === sessionVersion) {
        isSelecting.value = false
      }
    }
  }

  /** Releases timers and fetches when the component is unmounted or restarted. */
  function stop(): void {
    active = false
    sessionVersion += 1
    catalogRequestController?.abort()
    catalogRequestController = undefined
    statusRequestController?.abort()
    statusRequestController = undefined
    selectionRequestController?.abort()
    selectionRequestController = undefined
    isSelecting.value = false
    awaitingFrameAfterSelection = undefined

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
    if (awaitingFrameAfterSelection !== undefined) {
      return
    }
    frameAvailable.value = true
    statusErrorMessage.value = camera.value?.error?.message ?? null
  }

  /** Replaces a failed image element after a short delay to reopen the MJPEG stream. */
  function onStreamError(): void {
    if (!active || selectedCameraId.value === null) {
      return
    }

    frameAvailable.value = false
    statusErrorMessage.value = '画面流连接中断，正在重新连接。'
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
    devices,
    discoveryError,
    errorMessage,
    frameAvailable,
    isLoading,
    isSelecting,
    refreshCameras,
    selectedDeviceId,
    selectionEnabled,
    selectCameraDevice,
    sourceRevision,
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
