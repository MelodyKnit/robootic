/**
 * The browser-facing contract for the read-only camera preview API.
 * The API client is intentionally limited to status and image endpoints so the
 * web application cannot become a path for robot or gripper control.
 */
export type CameraState = 'starting' | 'streaming' | 'degraded' | 'stopped'

export interface CameraError {
  code: string
  message: string
}

export interface CameraStatus {
  cameraId: string
  state: CameraState
  latestFrameAt: number | null
  frameSequence: number | null
  error: CameraError | null
}

interface CameraListResponse {
  cameras: CameraStatus[]
}

interface ApiErrorResponse {
  code?: unknown
  message?: unknown
}

const allowedStates: CameraState[] = ['starting', 'streaming', 'degraded', 'stopped']

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
    !isNullableNumber(value.frame_sequence) ||
    !isNullableCameraError(value.error)
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机服务返回了不完整状态。')
  }

  return {
    cameraId: value.camera_id,
    state: value.state as CameraState,
    latestFrameAt: value.latest_frame_at as number | null,
    frameSequence: value.frame_sequence as number | null,
    error: value.error as CameraError | null,
  }
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

async function requestJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(apiUrl(path), { signal })
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
  const payload = await requestJson('/api/cameras', signal)
  if (typeof payload !== 'object' || payload === null || !Array.isArray((payload as CameraListResponse).cameras)) {
    throw new CameraApiError(502, 'invalid_response', '相机列表响应无效。')
  }

  return (payload as CameraListResponse).cameras.map(parseCameraStatus)
}

/** Retrieves the current state for one configured camera. */
export async function getCameraStatus(cameraId: string, signal?: AbortSignal): Promise<CameraStatus> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/status`, signal)
  return parseCameraStatus(payload)
}

/** Returns the MJPEG endpoint that a native image element can keep open. */
export function getCameraStreamUrl(cameraId: string): string {
  return apiUrl(`/api/cameras/${encodeURIComponent(cameraId)}/stream`)
}
