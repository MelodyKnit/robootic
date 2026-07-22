/**
 * Browser contracts for camera preview and its fixed safe parameter whitelist.
 * These endpoints never expose robot or gripper commands, SDK node names, or
 * persistent camera user-set operations.
 */
export type CameraState = 'starting' | 'streaming' | 'degraded' | 'stopped'
export type CameraParameterKind = 'float' | 'enum' | 'boolean'
export type CameraParameterApplyMode = 'live' | 'restart'
export type CameraParameterValue = number | string | boolean

export interface CameraError {
  code: string
  message: string
}

export interface CameraStatus {
  cameraId: string
  state: CameraState
  latestFrameAt: number | null
  error: CameraError | null
}

export interface CameraParameter {
  key: string
  kind: CameraParameterKind
  applyMode: CameraParameterApplyMode
  value: CameraParameterValue
  minimum: number | null
  maximum: number | null
  step: number | null
  unit: string | null
  options: string[]
}

export interface CameraParameters {
  cameraId: string
  writeEnabled: boolean
  parameters: CameraParameter[]
}

export interface CameraParameterUpdateResult extends CameraParameters {
  restartedAcquisition: boolean
}

interface CameraListResponse {
  cameras: CameraStatus[]
}

interface ApiErrorResponse {
  code?: unknown
  message?: unknown
}

interface JsonRequestOptions {
  method?: 'GET' | 'PATCH' | 'POST'
  body?: unknown
  signal?: AbortSignal
}

const allowedStates: CameraState[] = ['starting', 'streaming', 'degraded', 'stopped']
const allowedParameterKinds: CameraParameterKind[] = ['float', 'enum', 'boolean']
const allowedApplyModes: CameraParameterApplyMode[] = ['live', 'restart']

/**
 * Represents a rejected HTTP request with the stable error details returned by
 * the FastAPI service when they are available.
 */
export class CameraApiError extends Error {
  public readonly status: number
  public readonly code: string

  public constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'CameraApiError'
    this.status = status
    this.code = code
  }
}

/** Builds a same-origin URL unless a development API base URL was configured. */
function apiUrl(path: string): string {
  const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL
  if (!configuredBaseUrl) {
    return path
  }

  return `${configuredBaseUrl.replace(/\/$/, '')}${path}`
}

/** Converts a server status payload into a stable frontend data shape. */
function parseCameraStatus(payload: unknown): CameraStatus {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '相机服务返回了无效状态。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.camera_id !== 'string' ||
    !allowedStates.includes(value.state as CameraState) ||
    !isNullableNumber(value.latest_frame_at) ||
    !isNullableCameraError(value.error)
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机服务返回了不完整状态。')
  }

  return {
    cameraId: value.camera_id,
    state: value.state as CameraState,
    latestFrameAt: value.latest_frame_at as number | null,
    error: value.error as CameraError | null,
  }
}

/** Converts a server parameter list into values safe for UI controls. */
function parseCameraParameters(payload: unknown, includeRestartedAcquisition: boolean): CameraParameters | CameraParameterUpdateResult {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '相机参数响应无效。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.camera_id !== 'string' ||
    typeof value.write_enabled !== 'boolean' ||
    !Array.isArray(value.parameters) ||
    (includeRestartedAcquisition && typeof value.restarted_acquisition !== 'boolean')
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机参数响应不完整。')
  }

  const parsed: CameraParameters = {
    cameraId: value.camera_id,
    writeEnabled: value.write_enabled,
    parameters: value.parameters.map(parseCameraParameter),
  }
  if (!includeRestartedAcquisition) {
    return parsed
  }

  return {
    ...parsed,
    restartedAcquisition: value.restarted_acquisition as boolean,
  }
}

/** Validates one flexible API descriptor before it reaches form controls. */
function parseCameraParameter(payload: unknown): CameraParameter {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '相机参数条目无效。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.key !== 'string' ||
    !allowedParameterKinds.includes(value.kind as CameraParameterKind) ||
    !allowedApplyModes.includes(value.apply_mode as CameraParameterApplyMode) ||
    !isNullableNumber(value.minimum) ||
    !isNullableNumber(value.maximum) ||
    !isNullableNumber(value.step) ||
    !(value.unit === null || typeof value.unit === 'string') ||
    !Array.isArray(value.options) ||
    !value.options.every((option) => typeof option === 'string')
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机参数条目不完整。')
  }

  const kind = value.kind as CameraParameterKind
  if (!isParameterValueForKind(value.value, kind)) {
    throw new CameraApiError(502, 'invalid_response', '相机参数值类型无效。')
  }

  return {
    key: value.key,
    kind,
    applyMode: value.apply_mode as CameraParameterApplyMode,
    value: value.value,
    minimum: value.minimum as number | null,
    maximum: value.maximum as number | null,
    step: value.step as number | null,
    unit: value.unit as string | null,
    options: value.options as string[],
  }
}

function isParameterValueForKind(value: unknown, kind: CameraParameterKind): value is CameraParameterValue {
  if (kind === 'float') {
    return typeof value === 'number' && Number.isFinite(value)
  }
  if (kind === 'enum') {
    return typeof value === 'string'
  }
  return typeof value === 'boolean'
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function isNullableCameraError(value: unknown): value is CameraError | null {
  if (value === null) {
    return true
  }

  if (typeof value !== 'object') {
    return false
  }

  const error = value as Record<string, unknown>
  return typeof error.code === 'string' && typeof error.message === 'string'
}

async function requestJson(path: string, options: JsonRequestOptions = {}): Promise<unknown> {
  const response = await fetch(apiUrl(path), {
    method: options.method ?? 'GET',
    signal: options.signal,
    headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  if (response.ok) {
    return response.json()
  }

  let errorPayload: ApiErrorResponse | null = null
  try {
    errorPayload = (await response.json()) as ApiErrorResponse
  } catch {
    // A non-JSON error is still represented as a stable frontend error.
  }

  const code = typeof errorPayload?.code === 'string' ? errorPayload.code : 'request_failed'
  const message =
    typeof errorPayload?.message === 'string'
      ? errorPayload.message
      : `相机服务请求失败（HTTP ${response.status}）。`

  throw new CameraApiError(response.status, code, message)
}

/** Fetches all configured cameras before the page selects its only preview target. */
export async function listCameras(signal?: AbortSignal): Promise<CameraStatus[]> {
  const payload = await requestJson('/api/cameras', { signal })
  if (typeof payload !== 'object' || payload === null || !Array.isArray((payload as CameraListResponse).cameras)) {
    throw new CameraApiError(502, 'invalid_response', '相机列表响应无效。')
  }

  return (payload as CameraListResponse).cameras.map(parseCameraStatus)
}

/** Retrieves the current state for one configured camera. */
export async function getCameraStatus(cameraId: string, signal?: AbortSignal): Promise<CameraStatus> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/status`, { signal })
  return parseCameraStatus(payload)
}

/** Retrieves the actual parameter capabilities reported by the active camera. */
export async function getCameraParameters(cameraId: string, signal?: AbortSignal): Promise<CameraParameters> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/parameters`, { signal })
  return parseCameraParameters(payload, false) as CameraParameters
}

/** Writes a stream-safe parameter immediately after the user changes its control. */
export async function updateLiveCameraParameter(
  cameraId: string,
  parameterKey: string,
  value: CameraParameterValue,
): Promise<CameraParameterUpdateResult> {
  const payload = await requestJson(
    `/api/cameras/${encodeURIComponent(cameraId)}/parameters/${encodeURIComponent(parameterKey)}`,
    { method: 'PATCH', body: { value } },
  )
  return parseCameraParameters(payload, true) as CameraParameterUpdateResult
}

/** Applies staged acquisition-locked settings and lets the backend resume the stream. */
export async function applyRestartCameraParameters(
  cameraId: string,
  values: Record<string, CameraParameterValue>,
): Promise<CameraParameterUpdateResult> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/parameters/apply`, {
    method: 'POST',
    body: { values },
  })
  return parseCameraParameters(payload, true) as CameraParameterUpdateResult
}

/** Returns the MJPEG endpoint that a native image element can keep open. */
export function getCameraStreamUrl(cameraId: string): string {
  return apiUrl(`/api/cameras/${encodeURIComponent(cameraId)}/stream`)
}
