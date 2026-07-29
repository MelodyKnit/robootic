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

/** One physical device discovered behind a configured logical camera adapter. */
export interface CameraDevice {
  deviceId: string
  displayName: string
  modelName: string
  transport: string
  selected: boolean
  calibrated: boolean
}

/** Camera discovery and selection state returned as one consistent snapshot. */
export interface CameraCatalog {
  cameras: CameraStatus[]
  devices: CameraDevice[]
  selectedDeviceId: string | null
  selectionEnabled: boolean
  discoveryError: CameraError | null
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

export interface PoseJoint {
  name: string
  xPx: number
  yPx: number
  normalizedX: number
  normalizedY: number
  confidence: number
}

export interface PoseBoundingBox {
  x: number
  y: number
  width: number
  height: number
}

export interface HumanPose {
  cameraId: string
  capturedAt: number
  boundingBox: PoseBoundingBox
  confidence: number
  joints: PoseJoint[]
}

/** Passive motion metadata for the selected 2D target joint between two valid frames. */
export interface PoseMotion {
  previousCapturedAt: number
  capturedAt: number
  targetJoint: string
  deltaX: number
  deltaY: number
  displacement: number
  velocityX: number
  velocityY: number
  speed: number
  moving: boolean
}

export interface PoseTracking {
  cameraId: string
  enabled: boolean
  capturedAt: number | null
  latestFrameAt: number | null
  overlayFresh: boolean
  valid: boolean
  reason: string
  targetJoint: string
  target: PoseJoint | null
  person: HumanPose | null
  inferenceLatencyMs: number | null
  lostFrames: number
  motion: PoseMotion | null
  drawSkeleton: boolean
}

export type JointVisibilityState = 'detected' | 'low_confidence' | 'out_of_frame' | 'unavailable'

export interface FrameQuality {
  capturedAt: number
  valid: boolean
  width: number | null
  height: number | null
  pixelFormat: string | null
  brightnessMean: number | null
  contrast: number | null
  sharpness: number | null
  warnings: string[]
}

export interface PersonDetection {
  boundingBox: PoseBoundingBox
  confidence: number
  selected: boolean
}

export interface JointVisibility {
  name: string
  state: JointVisibilityState
  confidence: number
  normalizedX: number
  normalizedY: number
}

export interface VisionAnalysis {
  cameraId: string
  frameCapturedAt: number | null
  inferenceCapturedAt: number | null
  poseEnabled: boolean
  valid: boolean
  reason: string
  frame: FrameQuality | null
  personCount: number
  persons: PersonDetection[]
  selectedPerson: HumanPose | null
  jointVisibility: JointVisibility[]
  visibleJointNames: string[]
}

interface ApiErrorResponse {
  code?: unknown
  message?: unknown
}

interface JsonRequestOptions {
  method?: 'GET' | 'PATCH' | 'POST' | 'PUT'
  body?: unknown
  signal?: AbortSignal
}

const allowedStates: CameraState[] = ['starting', 'streaming', 'degraded', 'stopped']
const allowedParameterKinds: CameraParameterKind[] = ['float', 'enum', 'boolean']
const allowedApplyModes: CameraParameterApplyMode[] = ['live', 'restart']
const allowedJointVisibilityStates: JointVisibilityState[] = [
  'detected', 'low_confidence', 'out_of_frame', 'unavailable',
]

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

  const value = payload as unknown as Record<string, unknown>
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

/** Converts one backend-owned device descriptor without exposing SDK objects. */
function parseCameraDevice(payload: unknown): CameraDevice {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '相机设备条目无效。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.device_id !== 'string' ||
    typeof value.display_name !== 'string' ||
    typeof value.model_name !== 'string' ||
    typeof value.transport !== 'string' ||
    typeof value.selected !== 'boolean' ||
    typeof value.calibrated !== 'boolean'
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机设备条目不完整。')
  }

  return {
    deviceId: value.device_id,
    displayName: value.display_name,
    modelName: value.model_name,
    transport: value.transport,
    selected: value.selected,
    calibrated: value.calibrated,
  }
}

/** Parses one atomic discovery snapshot shared by listing and selection responses. */
function parseCameraCatalog(payload: unknown): CameraCatalog {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '相机列表响应无效。')
  }

  const value = payload as Record<string, unknown>
  if (!Array.isArray(value.cameras)) {
    throw new CameraApiError(502, 'invalid_response', '相机列表响应不完整。')
  }

  const catalogFields = [
    'devices',
    'selected_device_id',
    'selection_enabled',
    'discovery_error',
  ]
  const catalogFieldCount = catalogFields.filter((field) => field in value).length
  if (catalogFieldCount === 0) {
    return {
      cameras: value.cameras.map(parseCameraStatus),
      devices: [],
      selectedDeviceId: null,
      selectionEnabled: false,
      discoveryError: null,
    }
  }

  if (
    !Array.isArray(value.devices) ||
    !(value.selected_device_id === null || typeof value.selected_device_id === 'string') ||
    typeof value.selection_enabled !== 'boolean' ||
    !isNullableCameraError(value.discovery_error)
  ) {
    throw new CameraApiError(502, 'invalid_response', '相机列表响应不完整。')
  }

  const cameras = value.cameras.map(parseCameraStatus)
  const devices = value.devices.map(parseCameraDevice)
  const selectedDeviceId = value.selected_device_id as string | null
  const discoveryError = value.discovery_error as CameraError | null
  const selectedDevices = devices.filter((device) => device.selected)
  const selectedDescriptor = devices.find((device) => device.deviceId === selectedDeviceId)
  if (selectedDevices.length > 1) {
    throw new CameraApiError(502, 'invalid_response', '相机列表包含多个已选设备。')
  }
  if (selectedDeviceId === null && selectedDevices.length !== 0) {
    throw new CameraApiError(502, 'invalid_response', '相机列表的选择状态不一致。')
  }
  if (selectedDeviceId !== null) {
    if (selectedDescriptor !== undefined && !selectedDescriptor.selected) {
      throw new CameraApiError(502, 'invalid_response', '相机列表的选择状态不一致。')
    }
    if (selectedDevices.some((device) => device.deviceId !== selectedDeviceId)) {
      throw new CameraApiError(502, 'invalid_response', '相机列表的选择状态不一致。')
    }
    if (selectedDescriptor === undefined && discoveryError === null) {
      throw new CameraApiError(502, 'invalid_response', '相机列表中的已选设备不存在。')
    }
  }

  return {
    cameras,
    devices,
    selectedDeviceId,
    selectionEnabled: value.selection_enabled,
    discoveryError,
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

/** Converts one pose API result into bounded values suitable for Canvas rendering. */
function parsePoseTracking(payload: unknown): PoseTracking {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '人体姿态响应无效。')
  }

  const value = payload as Record<string, unknown>
  const rawMotion = value.motion === undefined ? null : value.motion
  // These additive fields are optional while a browser is upgraded ahead of the
  // preview service. Missing values deliberately suppress an unsafe stale overlay.
  const latestFrameAt = value.latest_frame_at === undefined ? null : value.latest_frame_at
  const overlayFresh = value.overlay_fresh === undefined ? false : value.overlay_fresh
  if (
    typeof value.camera_id !== 'string' ||
    typeof value.enabled !== 'boolean' ||
    !isNullableNumber(value.captured_at) ||
    !isNullableNumber(latestFrameAt) ||
    typeof overlayFresh !== 'boolean' ||
    typeof value.valid !== 'boolean' ||
    typeof value.reason !== 'string' ||
    typeof value.target_joint !== 'string' ||
    !isNullablePoseJoint(value.target) ||
    !isNullableHumanPose(value.person) ||
    !isNullableNumber(value.inference_latency_ms) ||
    !isNonNegativeInteger(value.lost_frames) ||
    !isNullablePoseMotion(rawMotion) ||
    typeof value.draw_skeleton !== 'boolean'
  ) {
    throw new CameraApiError(502, 'invalid_response', '人体姿态响应不完整。')
  }

  return {
    cameraId: value.camera_id,
    enabled: value.enabled,
    capturedAt: value.captured_at as number | null,
    latestFrameAt: latestFrameAt as number | null,
    overlayFresh,
    valid: value.valid,
    reason: value.reason,
    targetJoint: value.target_joint,
    target: value.target === null ? null : parsePoseJoint(value.target),
    person: value.person === null ? null : parseHumanPose(value.person),
    inferenceLatencyMs: value.inference_latency_ms as number | null,
    lostFrames: value.lost_frames,
    motion: rawMotion === null ? null : parsePoseMotion(rawMotion),
    drawSkeleton: value.draw_skeleton,
  }
}

/** Converts one cached analysis payload without accepting raw pixels or model tensors. */
function parseVisionAnalysis(payload: unknown): VisionAnalysis {
  if (typeof payload !== 'object' || payload === null) {
    throw new CameraApiError(502, 'invalid_response', '视觉分析响应无效。')
  }

  const value = payload as Record<string, unknown>
  if (
    typeof value.camera_id !== 'string' ||
    !isNullableNumber(value.frame_captured_at) ||
    !isNullableNumber(value.inference_captured_at) ||
    typeof value.pose_enabled !== 'boolean' ||
    typeof value.valid !== 'boolean' ||
    typeof value.reason !== 'string' ||
    !isNullableFrameQuality(value.frame) ||
    !isNonNegativeInteger(value.person_count) ||
    !Array.isArray(value.persons) ||
    !value.persons.every(isPersonDetection) ||
    !isNullableHumanPose(value.selected_person) ||
    !Array.isArray(value.joint_visibility) ||
    !value.joint_visibility.every(isJointVisibility) ||
    !Array.isArray(value.visible_joint_names) ||
    !value.visible_joint_names.every((name) => typeof name === 'string')
  ) {
    throw new CameraApiError(502, 'invalid_response', '视觉分析响应不完整。')
  }
  if (value.person_count !== value.persons.length) {
    throw new CameraApiError(502, 'invalid_response', '视觉分析人数统计不一致。')
  }

  return {
    cameraId: value.camera_id,
    frameCapturedAt: value.frame_captured_at as number | null,
    inferenceCapturedAt: value.inference_captured_at as number | null,
    poseEnabled: value.pose_enabled,
    valid: value.valid,
    reason: value.reason,
    frame: value.frame === null ? null : parseFrameQuality(value.frame),
    personCount: value.person_count as number,
    persons: (value.persons as unknown[]).map(parsePersonDetection),
    selectedPerson: value.selected_person === null ? null : parseHumanPose(value.selected_person),
    jointVisibility: (value.joint_visibility as unknown[]).map(parseJointVisibility),
    visibleJointNames: value.visible_joint_names as string[],
  }
}

/** Maps a validated normalized box to the frontend data contract. */
function parseBoundingBox(payload: unknown): PoseBoundingBox {
  const box = payload as Record<string, unknown>
  return {
    x: box.x as number,
    y: box.y as number,
    width: box.width as number,
    height: box.height as number,
  }
}

/** Converts passive image-quality diagnostics from the latest cached frame. */
function parseFrameQuality(payload: unknown): FrameQuality {
  const value = payload as Record<string, unknown>
  return {
    capturedAt: value.captured_at as number,
    valid: value.valid as boolean,
    width: value.width as number | null,
    height: value.height as number | null,
    pixelFormat: value.pixel_format as string | null,
    brightnessMean: value.brightness_mean as number | null,
    contrast: value.contrast as number | null,
    sharpness: value.sharpness as number | null,
    warnings: value.warnings as string[],
  }
}

/** Converts one person box derived from the existing Keypoint R-CNN inference output. */
function parsePersonDetection(payload: unknown): PersonDetection {
  const value = payload as Record<string, unknown>
  return {
    boundingBox: parseBoundingBox(value.bounding_box),
    confidence: value.confidence as number,
    selected: value.selected as boolean,
  }
}

/** Converts one named joint visibility state. */
function parseJointVisibility(payload: unknown): JointVisibility {
  const value = payload as Record<string, unknown>
  return {
    name: value.name as string,
    state: value.state as JointVisibilityState,
    confidence: value.confidence as number,
    normalizedX: value.normalized_x as number,
    normalizedY: value.normalized_y as number,
  }
}

/** Validates one person pose while keeping model-specific tensors out of the UI. */
function parseHumanPose(payload: unknown): HumanPose {
  if (!isHumanPose(payload)) {
    throw new CameraApiError(502, 'invalid_response', '人体姿态数据无效。')
  }

  const value = payload as unknown as Record<string, unknown>
  const boundingBox = value.bounding_box as Record<string, unknown>
  return {
    cameraId: value.camera_id as string,
    capturedAt: value.captured_at as number,
    boundingBox: {
      x: boundingBox.x as number,
      y: boundingBox.y as number,
      width: boundingBox.width as number,
      height: boundingBox.height as number,
    },
    confidence: value.confidence as number,
    joints: (value.joints as unknown[]).map(parsePoseJoint),
  }
}

/** Converts an API joint object after type and coordinate range validation. */
function parsePoseJoint(payload: unknown): PoseJoint {
  if (!isPoseJoint(payload)) {
    throw new CameraApiError(502, 'invalid_response', '人体关节点数据无效。')
  }

  const value = payload as unknown as Record<string, unknown>
  return {
    name: value.name as string,
    xPx: value.x_px as number,
    yPx: value.y_px as number,
    normalizedX: value.normalized_x as number,
    normalizedY: value.normalized_y as number,
    confidence: value.confidence as number,
  }
}

/** Maps bounded normalized image-space motion without assigning physical units. */
function parsePoseMotion(payload: unknown): PoseMotion {
  if (!isPoseMotion(payload)) {
    throw new CameraApiError(502, 'invalid_response', '人体运动追踪数据无效。')
  }

  const value = payload as unknown as Record<string, unknown>
  return {
    previousCapturedAt: value.previous_captured_at as number,
    capturedAt: value.captured_at as number,
    targetJoint: value.target_joint as string,
    deltaX: value.delta_x as number,
    deltaY: value.delta_y as number,
    displacement: value.displacement as number,
    velocityX: value.velocity_x as number,
    velocityY: value.velocity_y as number,
    speed: value.speed as number,
    moving: value.moving as boolean,
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

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isInteger(value) && value > 0)
}

function isNullablePoseJoint(value: unknown): value is PoseJoint | null {
  return value === null || isPoseJoint(value)
}

function isNullablePoseMotion(value: unknown): value is PoseMotion | null {
  return value === null || isPoseMotion(value)
}

function isPoseMotion(value: unknown): value is PoseMotion {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const motion = value as Record<string, unknown>
  return (
    isFiniteNumber(motion.previous_captured_at) &&
    isFiniteNumber(motion.captured_at) &&
    motion.captured_at > motion.previous_captured_at &&
    typeof motion.target_joint === 'string' &&
    isFiniteNumber(motion.delta_x) &&
    isFiniteNumber(motion.delta_y) &&
    isNonNegativeFiniteNumber(motion.displacement) &&
    isFiniteNumber(motion.velocity_x) &&
    isFiniteNumber(motion.velocity_y) &&
    isNonNegativeFiniteNumber(motion.speed) &&
    typeof motion.moving === 'boolean'
  )
}

function isPoseJoint(value: unknown): value is PoseJoint {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const joint = value as Record<string, unknown>
  return (
    typeof joint.name === 'string' &&
    isFiniteNumber(joint.x_px) &&
    isFiniteNumber(joint.y_px) &&
    isNormalizedCoordinate(joint.normalized_x) &&
    isNormalizedCoordinate(joint.normalized_y) &&
    isNormalizedCoordinate(joint.confidence)
  )
}

function isNullableHumanPose(value: unknown): value is HumanPose | null {
  return value === null || isHumanPose(value)
}

function isNullableFrameQuality(value: unknown): value is FrameQuality | null {
  return value === null || isFrameQuality(value)
}

function isFrameQuality(value: unknown): value is FrameQuality {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const quality = value as Record<string, unknown>
  return (
    isFiniteNumber(quality.captured_at) &&
    typeof quality.valid === 'boolean' &&
    isNullablePositiveInteger(quality.width) &&
    isNullablePositiveInteger(quality.height) &&
    (quality.pixel_format === null || typeof quality.pixel_format === 'string') &&
    isNullableNumber(quality.brightness_mean) &&
    isNullableNumber(quality.contrast) &&
    isNullableNumber(quality.sharpness) &&
    Array.isArray(quality.warnings) &&
    quality.warnings.every((warning) => typeof warning === 'string')
  )
}

function isPersonDetection(value: unknown): value is PersonDetection {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const person = value as Record<string, unknown>
  return (
    isPoseBoundingBox(person.bounding_box) &&
    isNormalizedCoordinate(person.confidence) &&
    typeof person.selected === 'boolean'
  )
}

function isJointVisibility(value: unknown): value is JointVisibility {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const joint = value as Record<string, unknown>
  return (
    typeof joint.name === 'string' &&
    allowedJointVisibilityStates.includes(joint.state as JointVisibilityState) &&
    isNormalizedCoordinate(joint.confidence) &&
    isNormalizedCoordinate(joint.normalized_x) &&
    isNormalizedCoordinate(joint.normalized_y)
  )
}

function isHumanPose(value: unknown): value is HumanPose {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const person = value as Record<string, unknown>
  if (
    typeof person.camera_id !== 'string' ||
    !isFiniteNumber(person.captured_at) ||
    !isNormalizedCoordinate(person.confidence) ||
    !Array.isArray(person.joints) ||
    !person.joints.every(isPoseJoint) ||
    typeof person.bounding_box !== 'object' ||
    person.bounding_box === null
  ) {
    return false
  }

  return isPoseBoundingBox(person.bounding_box)
}

function isPoseBoundingBox(value: unknown): value is PoseBoundingBox {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const box = value as Record<string, unknown>
  return (
    isNormalizedCoordinate(box.x) &&
    isNormalizedCoordinate(box.y) &&
    isNormalizedCoordinate(box.width) &&
    isNormalizedCoordinate(box.height)
  )
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isNonNegativeFiniteNumber(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0
}

function isNormalizedCoordinate(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0 && value <= 1
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

/** Fetches configured logical cameras and their backend-discovered physical devices. */
export async function listCameras(signal?: AbortSignal): Promise<CameraCatalog> {
  const payload = await requestJson('/api/cameras', { signal })
  return parseCameraCatalog(payload)
}

/** Selects one discovered physical source for a configured logical camera. */
export async function selectCameraDevice(
  cameraId: string,
  deviceId: string,
  signal?: AbortSignal,
): Promise<CameraCatalog> {
  const payload = await requestJson(
    `/api/cameras/${encodeURIComponent(cameraId)}/selection`,
    { method: 'PUT', body: { device_id: deviceId }, signal },
  )
  return parseCameraCatalog(payload)
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

/** Retrieves the latest cached single-person pose without initiating GPU inference. */
export async function getCameraPose(cameraId: string, signal?: AbortSignal): Promise<PoseTracking> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/pose`, { signal })
  return parsePoseTracking(payload)
}

/** Persists and selects one supported COCO target joint for the active pose tracker. */
export async function updatePoseTarget(cameraId: string, targetJoint: string): Promise<PoseTracking> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/pose/target`, {
    method: 'PUT',
    body: { target_joint: targetJoint },
  })
  return parsePoseTracking(payload)
}

/** Retrieves cached image quality and human analysis without starting a new inference. */
export async function getCameraVisionAnalysis(cameraId: string, signal?: AbortSignal): Promise<VisionAnalysis> {
  const payload = await requestJson(`/api/cameras/${encodeURIComponent(cameraId)}/vision/analysis`, { signal })
  return parseVisionAnalysis(payload)
}

/** Returns the MJPEG endpoint that a native image element can keep open. */
export function getCameraStreamUrl(cameraId: string): string {
  return apiUrl(`/api/cameras/${encodeURIComponent(cameraId)}/stream`)
}
