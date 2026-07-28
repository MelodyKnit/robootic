export type GripperMode = 'simulation' | 'physical'

export type GripperCommandAction = 'open' | 'close' | 'move_to_position' | 'stop'

export interface GripperStatus {
  id: string
  mode: GripperMode
  controlsEnabled: boolean
  connected: boolean
  initialized: boolean
  initializing: boolean
  moving: boolean
  gripping: boolean
  targetPosition: number
  positionIsFeedback: boolean
  gripState: string
  supportsStop: boolean
  minimumPosition: number
  maximumPosition: number
  forceMin: number
  forceMax: number
  speedSupported: boolean
  speedMin: number
  speedMax: number
  openPosition: number
  closePosition: number
  armedUntil: number | null
  lastError: string | null
}

export interface ArmGripperRequest {
  workcellClear: boolean
  emergencyStopVerified: boolean
}

export interface ArmGripperResult {
  armToken: string
  expiresAt: number
  status: GripperStatus
}

export interface GripperCommandRequest {
  action: GripperCommandAction
  targetPosition?: number
  forcePercent?: number
  speedPercent?: number
}

interface GripperListResponse {
  grippers?: unknown
}

interface JsonRequestOptions {
  method?: 'GET' | 'POST' | 'DELETE'
  body?: Record<string, unknown>
  headers?: Record<string, string>
  signal?: AbortSignal
  allowEmptyResponse?: boolean
  keepalive?: boolean
}

interface ApiErrorResponse {
  code?: unknown
  message?: unknown
}

/** Represents one stable API failure returned by the gripper control service. */
export class GripperApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'GripperApiError'
    this.status = status
    this.code = code
  }
}

function apiUrl(path: string): string {
  return path
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isInteger(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value)
}

function isNullableTimestamp(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

/** Validates and converts one snake_case status response into the UI model. */
function parseGripperStatus(payload: unknown): GripperStatus {
  if (!isRecord(payload)) {
    throw invalidResponse('夹爪状态响应不是对象。')
  }

  const mode = payload.mode
  if (
    typeof payload.gripper_id !== 'string' ||
    payload.gripper_id.length === 0 ||
    (mode !== 'simulation' && mode !== 'physical') ||
    typeof payload.controls_enabled !== 'boolean' ||
    typeof payload.connected !== 'boolean' ||
    typeof payload.initialized !== 'boolean' ||
    typeof payload.initializing !== 'boolean' ||
    typeof payload.moving !== 'boolean' ||
    typeof payload.gripping !== 'boolean' ||
    !isInteger(payload.target_position) ||
    typeof payload.position_is_feedback !== 'boolean' ||
    typeof payload.grip_state !== 'string' ||
    typeof payload.supports_speed !== 'boolean' ||
    typeof payload.supports_stop !== 'boolean' ||
    !isInteger(payload.minimum_position) ||
    !isInteger(payload.maximum_position) ||
    !isInteger(payload.minimum_force_percent) ||
    !isInteger(payload.maximum_force_percent) ||
    !isInteger(payload.minimum_speed_percent) ||
    !isInteger(payload.maximum_speed_percent) ||
    !isInteger(payload.open_position) ||
    !isInteger(payload.close_position) ||
    !isNullableTimestamp(payload.armed_until) ||
    !isNullableString(payload.last_error)
  ) {
    throw invalidResponse('夹爪状态字段缺失或类型无效。')
  }

  if (
    payload.minimum_position >= payload.maximum_position ||
    payload.minimum_force_percent > payload.maximum_force_percent ||
    payload.minimum_speed_percent > payload.maximum_speed_percent ||
    payload.target_position < payload.minimum_position ||
    payload.target_position > payload.maximum_position ||
    payload.open_position < 0 ||
    payload.close_position < 0 ||
    payload.open_position < payload.minimum_position ||
    payload.open_position > payload.maximum_position ||
    payload.close_position < payload.minimum_position ||
    payload.close_position > payload.maximum_position
  ) {
    throw invalidResponse('夹爪状态范围无效。')
  }

  return {
    id: payload.gripper_id,
    mode,
    controlsEnabled: payload.controls_enabled,
    connected: payload.connected,
    initialized: payload.initialized,
    initializing: payload.initializing,
    moving: payload.moving,
    gripping: payload.gripping,
    targetPosition: payload.target_position,
    positionIsFeedback: payload.position_is_feedback,
    gripState: payload.grip_state,
    supportsStop: payload.supports_stop,
    minimumPosition: payload.minimum_position,
    maximumPosition: payload.maximum_position,
    forceMin: payload.minimum_force_percent,
    forceMax: payload.maximum_force_percent,
    speedSupported: payload.supports_speed,
    speedMin: payload.minimum_speed_percent,
    speedMax: payload.maximum_speed_percent,
    openPosition: payload.open_position,
    closePosition: payload.close_position,
    armedUntil: payload.armed_until,
    lastError: payload.last_error,
  }
}

function parseArmResult(payload: unknown): ArmGripperResult {
  if (
    !isRecord(payload) ||
    typeof payload.token !== 'string' ||
    payload.token.length === 0 ||
    !isFiniteNumber(payload.expires_at)
  ) {
    throw invalidResponse('夹爪解锁响应无效。')
  }

  return {
    armToken: payload.token,
    expiresAt: payload.expires_at,
    status: parseGripperStatus(payload.status),
  }
}

function parseOperationStatus(payload: unknown): GripperStatus {
  if (!isRecord(payload) || !isRecord(payload.status)) {
    throw invalidResponse('夹爪操作响应无效。')
  }
  return parseGripperStatus(payload.status)
}

function invalidResponse(message: string): GripperApiError {
  return new GripperApiError(502, 'invalid_response', message)
}

async function requestJson(path: string, options: JsonRequestOptions = {}): Promise<unknown> {
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(apiUrl(path), {
    method: options.method ?? 'GET',
    signal: options.signal,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    keepalive: options.keepalive,
  })
  if (response.ok) {
    if (options.allowEmptyResponse || response.status === 204) {
      return null
    }
    return response.json()
  }

  let errorPayload: ApiErrorResponse | null = null
  try {
    errorPayload = (await response.json()) as ApiErrorResponse
  } catch {
    // Non-JSON failures are normalized for the UI.
  }

  const code = typeof errorPayload?.code === 'string' ? errorPayload.code : 'request_failed'
  const message =
    typeof errorPayload?.message === 'string'
      ? errorPayload.message
      : `夹爪控制请求失败（HTTP ${response.status}）。`
  throw new GripperApiError(response.status, code, message)
}

/** Lists the grippers explicitly exposed by the manual-control service. */
export async function listGrippers(signal?: AbortSignal): Promise<GripperStatus[]> {
  const payload = await requestJson('/api/grippers', { signal })
  if (!isRecord(payload) || !Array.isArray((payload as GripperListResponse).grippers)) {
    throw invalidResponse('夹爪列表响应无效。')
  }
  return ((payload as GripperListResponse).grippers as unknown[]).map(parseGripperStatus)
}

/** Reads the latest cached and device-backed state for one gripper. */
export async function getGripperStatus(gripperId: string, signal?: AbortSignal): Promise<GripperStatus> {
  const payload = await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/status`, { signal })
  return parseGripperStatus(payload)
}

/** Re-establishes the configured transport without initializing or moving the device. */
export async function reconnectGripper(gripperId: string): Promise<GripperStatus> {
  const payload = await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/reconnect`, {
    method: 'POST',
  })
  return parseGripperStatus(payload)
}

/** Creates a short-lived browser-session authorization after operator confirmation. */
export async function armGripper(gripperId: string, request: ArmGripperRequest): Promise<ArmGripperResult> {
  const payload = await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/arm`, {
    method: 'POST',
    body: {
      work_area_clear: request.workcellClear,
      emergency_stop_ready: request.emergencyStopVerified,
    },
  })
  return parseArmResult(payload)
}

/** Revokes the current browser authorization. Local state must still be cleared on failure. */
export async function disarmGripper(
  gripperId: string,
  armToken: string,
  keepalive = false,
): Promise<void> {
  await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/arm`, {
    method: 'DELETE',
    headers: { 'X-Gripper-Control-Token': armToken },
    allowEmptyResponse: true,
    keepalive,
  })
}

/** Starts the normal PGI initialization sequence after explicit operator approval. */
export async function initializeGripper(
  gripperId: string,
  armToken: string,
  idempotencyKey: string,
): Promise<GripperStatus> {
  const payload = await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/initialization`, {
    method: 'POST',
    headers: {
      'X-Gripper-Control-Token': armToken,
      'Idempotency-Key': idempotencyKey,
    },
  })
  return parseOperationStatus(payload)
}

/** Submits one deliberate motion command; callers must never retry with a new key automatically. */
export async function submitGripperCommand(
  gripperId: string,
  armToken: string,
  idempotencyKey: string,
  command: GripperCommandRequest,
): Promise<GripperStatus> {
  const body: Record<string, unknown> = { action: command.action }
  if (command.targetPosition !== undefined) {
    body.target_position = command.targetPosition
  }
  if (command.forcePercent !== undefined) {
    body.force_percent = command.forcePercent
  }
  if (command.speedPercent !== undefined) {
    body.speed_percent = command.speedPercent
  }

  const payload = await requestJson(`/api/grippers/${encodeURIComponent(gripperId)}/commands`, {
    method: 'POST',
    headers: {
      'X-Gripper-Control-Token': armToken,
      'Idempotency-Key': idempotencyKey,
    },
    body,
  })
  return parseOperationStatus(payload)
}
