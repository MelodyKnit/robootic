export type RobotMode = 'simulation' | 'physical'

export type JointVector = [number, number, number, number, number, number]

export interface RobotStatus {
  id: string
  mode: RobotMode
  controlsEnabled: boolean
  manualMotionEnabled: boolean
  enablePermitted: boolean
  connected: boolean
  powered: boolean
  enabled: boolean
  moving: boolean
  faulted: boolean
  emergencyStopped: boolean
  capturedAt: number
  jointPositionsRad: JointVector
  jointLowerLimitsRad: JointVector
  jointUpperLimitsRad: JointVector
  maximumJointSpeedRadPerSecond: number
  maximumJointStepRad: number
  armedUntil: number | null
  lastError: string | null
}

export interface ArmRobotRequest {
  workcellClear: boolean
  emergencyStopAvailable: boolean
}

export interface ArmRobotResult {
  armToken: string
  expiresAt: number
  status: RobotStatus
}

export interface JointMovePreviewRequest {
  jointPositionsRad: JointVector
  speedRadPerSecond: number
}

export interface JointMovePreview {
  id: string
  expiresAt: number
  sourceJointPositionsRad: JointVector
  targetJointPositionsRad: JointVector
  jointDeltasRad: JointVector
  estimatedDurationSeconds: number
  status: RobotStatus
}

export interface RobotOperationResult {
  idempotencyKey: string
  replayed: boolean
  status: RobotStatus
}

interface RobotListResponse {
  robots?: unknown
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

/** Represents one stable API failure returned by the manual JAKA service. */
export class RobotApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'RobotApiError'
    this.status = status
    this.code = code
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNullableTimestamp(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

/** Validates one six-axis radian vector before it reaches the UI state. */
function parseJointVector(payload: unknown, fieldName: string): JointVector {
  if (!Array.isArray(payload) || payload.length !== 6 || !payload.every(isFiniteNumber)) {
    throw invalidResponse(`${fieldName} 必须是六个有限数值。`)
  }
  return [payload[0], payload[1], payload[2], payload[3], payload[4], payload[5]]
}

/** Validates and converts one snake_case robot response into the UI model. */
function parseRobotStatus(payload: unknown): RobotStatus {
  if (!isRecord(payload)) {
    throw invalidResponse('机械臂状态响应不是对象。')
  }

  const mode = payload.mode
  if (
    typeof payload.robot_id !== 'string' ||
    payload.robot_id.length === 0 ||
    (mode !== 'simulation' && mode !== 'physical') ||
    typeof payload.controls_enabled !== 'boolean' ||
    typeof payload.manual_motion_enabled !== 'boolean' ||
    typeof payload.enable_permitted !== 'boolean' ||
    typeof payload.connected !== 'boolean' ||
    typeof payload.powered !== 'boolean' ||
    typeof payload.enabled !== 'boolean' ||
    typeof payload.moving !== 'boolean' ||
    typeof payload.faulted !== 'boolean' ||
    typeof payload.emergency_stopped !== 'boolean' ||
    !isFiniteNumber(payload.captured_at) ||
    !isFiniteNumber(payload.maximum_joint_speed_rad_per_second) ||
    !isFiniteNumber(payload.maximum_joint_step_rad) ||
    !isNullableTimestamp(payload.armed_until) ||
    !isNullableString(payload.last_error)
  ) {
    throw invalidResponse('机械臂状态字段缺失或类型无效。')
  }

  const jointPositionsRad = parseJointVector(payload.joint_positions_rad, 'joint_positions_rad')
  const jointLowerLimitsRad = parseJointVector(payload.joint_lower_limits_rad, 'joint_lower_limits_rad')
  const jointUpperLimitsRad = parseJointVector(payload.joint_upper_limits_rad, 'joint_upper_limits_rad')
  const maximumJointSpeedRadPerSecond = payload.maximum_joint_speed_rad_per_second
  const maximumJointStepRad = payload.maximum_joint_step_rad
  if (
    maximumJointSpeedRadPerSecond <= 0 ||
    maximumJointStepRad <= 0 ||
    jointLowerLimitsRad.some((limit, index) => limit >= jointUpperLimitsRad[index])
  ) {
    throw invalidResponse('机械臂状态范围无效。')
  }

  return {
    id: payload.robot_id,
    mode,
    controlsEnabled: payload.controls_enabled,
    manualMotionEnabled: payload.manual_motion_enabled,
    enablePermitted: payload.enable_permitted,
    connected: payload.connected,
    powered: payload.powered,
    enabled: payload.enabled,
    moving: payload.moving,
    faulted: payload.faulted,
    emergencyStopped: payload.emergency_stopped,
    capturedAt: payload.captured_at,
    jointPositionsRad,
    jointLowerLimitsRad,
    jointUpperLimitsRad,
    maximumJointSpeedRadPerSecond,
    maximumJointStepRad,
    armedUntil: payload.armed_until,
    lastError: payload.last_error,
  }
}

function parseArmResult(payload: unknown): ArmRobotResult {
  if (
    !isRecord(payload) ||
    typeof payload.token !== 'string' ||
    payload.token.length === 0 ||
    !isFiniteNumber(payload.expires_at)
  ) {
    throw invalidResponse('机械臂解锁响应无效。')
  }
  return {
    armToken: payload.token,
    expiresAt: payload.expires_at,
    status: parseRobotStatus(payload.status),
  }
}

function parsePreview(payload: unknown): JointMovePreview {
  if (
    !isRecord(payload) ||
    typeof payload.preview_id !== 'string' ||
    payload.preview_id.length === 0 ||
    !isFiniteNumber(payload.expires_at) ||
    !isFiniteNumber(payload.estimated_duration_seconds) ||
    payload.estimated_duration_seconds < 0
  ) {
    throw invalidResponse('机械臂运动预览响应无效。')
  }
  return {
    id: payload.preview_id,
    expiresAt: payload.expires_at,
    sourceJointPositionsRad: parseJointVector(payload.source_joint_positions_rad, 'source_joint_positions_rad'),
    targetJointPositionsRad: parseJointVector(payload.target_joint_positions_rad, 'target_joint_positions_rad'),
    jointDeltasRad: parseJointVector(payload.joint_deltas_rad, 'joint_deltas_rad'),
    estimatedDurationSeconds: payload.estimated_duration_seconds,
    status: parseRobotStatus(payload.status),
  }
}

function parseOperationResult(payload: unknown): RobotOperationResult {
  if (
    !isRecord(payload) ||
    typeof payload.idempotency_key !== 'string' ||
    payload.idempotency_key.length === 0 ||
    typeof payload.replayed !== 'boolean'
  ) {
    throw invalidResponse('机械臂操作响应无效。')
  }
  return {
    idempotencyKey: payload.idempotency_key,
    replayed: payload.replayed,
    status: parseRobotStatus(payload.status),
  }
}

function invalidResponse(message: string): RobotApiError {
  return new RobotApiError(502, 'invalid_response', message)
}

async function requestJson(path: string, options: JsonRequestOptions = {}): Promise<unknown> {
  const headers: Record<string, string> = { ...options.headers }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(path, {
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
      : `机械臂控制请求失败（HTTP ${response.status}）。`
  throw new RobotApiError(response.status, code, message)
}

/** Lists the JAKA targets explicitly exposed by the manual-control service. */
export async function listRobots(signal?: AbortSignal): Promise<RobotStatus[]> {
  const payload = await requestJson('/api/robots', { signal })
  if (!isRecord(payload) || !Array.isArray((payload as RobotListResponse).robots)) {
    throw invalidResponse('机械臂列表响应无效。')
  }
  return ((payload as RobotListResponse).robots as unknown[]).map(parseRobotStatus)
}

/** Reads one selected robot's latest status without enabling or moving it. */
export async function getRobotStatus(robotId: string, signal?: AbortSignal): Promise<RobotStatus> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/status`, { signal })
  return parseRobotStatus(payload)
}

/** Re-establishes the configured SDK session without powering, enabling, or moving JAKA. */
export async function reconnectRobot(robotId: string): Promise<RobotStatus> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/reconnect`, {
    method: 'POST',
  })
  return parseRobotStatus(payload)
}

/** Creates a short-lived browser authorization after explicit operator confirmation. */
export async function armRobot(robotId: string, request: ArmRobotRequest): Promise<ArmRobotResult> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/arm`, {
    method: 'POST',
    body: {
      work_area_clear: request.workcellClear,
      emergency_stop_ready: request.emergencyStopAvailable,
    },
  })
  return parseArmResult(payload)
}

/** Revokes the browser authorization without contacting JAKA motion or servo APIs. */
export async function disarmRobot(robotId: string, armToken: string, keepalive = false): Promise<void> {
  await requestJson(`/api/robots/${encodeURIComponent(robotId)}/arm`, {
    method: 'DELETE',
    headers: { 'X-Robot-Control-Token': armToken },
    allowEmptyResponse: true,
    keepalive,
  })
}

/** Explicitly enables already-powered servos after temporary authorization. */
export async function enableRobot(
  robotId: string,
  armToken: string,
  idempotencyKey: string,
): Promise<RobotOperationResult> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/enable`, {
    method: 'POST',
    headers: {
      'X-Robot-Control-Token': armToken,
      'Idempotency-Key': idempotencyKey,
    },
  })
  return parseOperationResult(payload)
}

/** Creates a server-side absolute joint-move preview without sending a movement command. */
export async function previewJointMove(
  robotId: string,
  armToken: string,
  request: JointMovePreviewRequest,
): Promise<JointMovePreview> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/joint-moves/preview`, {
    method: 'POST',
    headers: { 'X-Robot-Control-Token': armToken },
    body: {
      joint_positions_rad: request.jointPositionsRad,
      speed_rad_per_second: request.speedRadPerSecond,
    },
  })
  return parsePreview(payload)
}

/** Executes exactly one server-stored preview with an idempotency key. */
export async function executeJointMovePreview(
  robotId: string,
  armToken: string,
  idempotencyKey: string,
  previewId: string,
): Promise<RobotOperationResult> {
  const payload = await requestJson(`/api/robots/${encodeURIComponent(robotId)}/commands`, {
    method: 'POST',
    headers: {
      'X-Robot-Control-Token': armToken,
      'Idempotency-Key': idempotencyKey,
    },
    body: { preview_id: previewId },
  })
  return parseOperationResult(payload)
}
