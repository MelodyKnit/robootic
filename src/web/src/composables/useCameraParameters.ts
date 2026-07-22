import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'

import {
  CameraApiError,
  applyRestartCameraParameters,
  getCameraParameters,
  updateLiveCameraParameter,
  type CameraParameter,
  type CameraParameterUpdateResult,
  type CameraParameterValue,
  type CameraParameters,
} from '../api/camera'

/**
 * Owns parameter loading, immediate writes, and staged restart-required changes.
 * It deliberately keeps parameter writes separate from the MJPEG preview composable
 * so a visual reconnect can never accidentally replay an old camera setting request.
 */
export function useCameraParameters(cameraId: Ref<string | null>) {
  const cameraParameters = ref<CameraParameters | null>(null)
  const errorMessage = ref<string | null>(null)
  const isLoading = ref(false)
  const isApplying = ref(false)
  const liveValues = ref<Record<string, CameraParameterValue>>({})
  const restartDrafts = ref<Record<string, CameraParameterValue>>({})
  let requestController: AbortController | undefined
  let requestVersion = 0

  const parameters = computed(() => cameraParameters.value?.parameters ?? [])
  const writeEnabled = computed(() => cameraParameters.value?.writeEnabled ?? false)
  const restartChanges = computed(() => {
    const current = cameraParameters.value
    if (current === null) {
      return {} as Record<string, CameraParameterValue>
    }

    const values: Record<string, CameraParameterValue> = {}
    for (const parameter of current.parameters) {
      if (parameter.applyMode !== 'restart') {
        continue
      }
      const draft = restartDrafts.value[parameter.key]
      if (draft !== undefined && draft !== parameter.value) {
        values[parameter.key] = draft
      }
    }
    return values
  })
  const hasRestartChanges = computed(() => Object.keys(restartChanges.value).length > 0)
  const hasRestartParameters = computed(() => parameters.value.some((parameter) => parameter.applyMode === 'restart'))

  /** Returns the current UI value while retaining type information from the descriptor. */
  function valueFor(parameter: CameraParameter): CameraParameterValue {
    if (parameter.applyMode === 'restart') {
      return restartDrafts.value[parameter.key] ?? parameter.value
    }
    return liveValues.value[parameter.key] ?? parameter.value
  }

  /** Stores a local value until the caller uses its matching apply control. */
  function setDraft(parameter: CameraParameter, value: CameraParameterValue): void {
    if (parameter.applyMode === 'restart') {
      restartDrafts.value = { ...restartDrafts.value, [parameter.key]: value }
      return
    }
    liveValues.value = { ...liveValues.value, [parameter.key]: value }
  }

  /** Disables manual sliders when their corresponding automatic camera mode is active. */
  function isManualControlDisabled(parameter: CameraParameter): boolean {
    if (isApplying.value || !writeEnabled.value) {
      return true
    }
    if (parameter.key === 'exposure_time_us') {
      return liveValues.value.exposure_auto !== 'Off'
    }
    if (parameter.key === 'gain_db') {
      return liveValues.value.gain_auto !== 'Off'
    }
    if (parameter.key === 'frame_rate_fps') {
      return liveValues.value.frame_rate_enabled !== true
    }
    return false
  }

  /** Loads the active camera's actual parameter capabilities and resets staged values. */
  async function load(): Promise<void> {
    const selectedCameraId = cameraId.value
    const version = ++requestVersion
    requestController?.abort()
    requestController = undefined
    if (selectedCameraId === null) {
      cameraParameters.value = null
      liveValues.value = {}
      restartDrafts.value = {}
      errorMessage.value = null
      return
    }

    requestController = new AbortController()
    isLoading.value = true
    errorMessage.value = null
    try {
      const response = await getCameraParameters(selectedCameraId, requestController.signal)
      if (version !== requestVersion || cameraId.value !== selectedCameraId) {
        return
      }
      synchronise(response, true)
    } catch (error) {
      if (isAbortError(error) || version !== requestVersion) {
        return
      }
      cameraParameters.value = null
      errorMessage.value = displayError(error)
    } finally {
      if (version === requestVersion) {
        isLoading.value = false
      }
    }
  }

  /** Sends one stream-safe setting as soon as the user commits its field change. */
  async function applyLive(parameter: CameraParameter): Promise<void> {
    const selectedCameraId = cameraId.value
    if (selectedCameraId === null || parameter.applyMode !== 'live' || !writeEnabled.value) {
      return
    }

    isApplying.value = true
    errorMessage.value = null
    try {
      const response = await updateLiveCameraParameter(selectedCameraId, parameter.key, valueFor(parameter))
      synchronise(response, false)
    } catch (error) {
      const message = displayError(error)
      await load()
      errorMessage.value = message
    } finally {
      isApplying.value = false
    }
  }

  /** Applies all staged locked settings only after the user explicitly saves them. */
  async function applyRestartChanges(): Promise<void> {
    const selectedCameraId = cameraId.value
    if (selectedCameraId === null || !writeEnabled.value || !hasRestartChanges.value) {
      return
    }

    isApplying.value = true
    errorMessage.value = null
    try {
      const response = await applyRestartCameraParameters(selectedCameraId, restartChanges.value)
      synchronise(response, true)
      if (!response.restartedAcquisition) {
        errorMessage.value = '相机未报告重新采集状态。'
      }
    } catch (error) {
      const message = displayError(error)
      await load()
      errorMessage.value = message
    } finally {
      isApplying.value = false
    }
  }

  /** Synchronizes browser values from a successful server response. */
  function synchronise(response: CameraParameters | CameraParameterUpdateResult, resetRestartDrafts: boolean): void {
    const nextLiveValues: Record<string, CameraParameterValue> = {}
    const nextRestartDrafts: Record<string, CameraParameterValue> = {}
    for (const parameter of response.parameters) {
      if (parameter.applyMode === 'live') {
        nextLiveValues[parameter.key] = parameter.value
      } else {
        nextRestartDrafts[parameter.key] = resetRestartDrafts
          ? parameter.value
          : (restartDrafts.value[parameter.key] ?? parameter.value)
      }
    }
    cameraParameters.value = response
    liveValues.value = nextLiveValues
    restartDrafts.value = nextRestartDrafts
  }

  watch(
    cameraId,
    () => {
      void load()
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    requestController?.abort()
  })

  return {
    errorMessage,
    hasRestartChanges,
    hasRestartParameters,
    isApplying,
    isLoading,
    isManualControlDisabled,
    parameters,
    restartChanges,
    setDraft,
    applyLive,
    applyRestartChanges,
    load,
    valueFor,
    writeEnabled,
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function displayError(error: unknown): string {
  if (error instanceof CameraApiError) {
    return error.message
  }
  return '相机参数请求失败，请检查设备状态后重试。'
}
