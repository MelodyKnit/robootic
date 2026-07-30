import { expect, test, type Route } from '@playwright/test'

test('设备尚未选定时仍建立逻辑相机流以兼容自动发现', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog(null))
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus())
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('camera-device-selector')).toHaveValue('')
  await expect(page.getByTestId('camera-stream')).toBeVisible()
})

test('旧版相机列表响应仍保持预览兼容', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, { cameras: [cameraStatus()] })
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus())
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('camera-device-selector')).toBeDisabled()
  await expect(page.getByTestId('camera-stream')).toBeVisible()
})

test('设备发现失败时显示降级错误并保留活动逻辑相机流', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, {
        cameras: [cameraStatus()],
        devices: [],
        selected_device_id: 'opaque-active-device',
        selection_enabled: true,
        discovery_error: { code: 'camera_discovery_failed', message: '相机列表暂时无法刷新，当前画面保持不变。' },
      })
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus())
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('alert').filter({ hasText: '相机列表暂时无法刷新' })).toBeVisible()
  await expect(page.getByTestId('camera-device-selector')).toBeDisabled()
  await expect(page.getByTestId('camera-stream')).toBeVisible()
})

test('选择新设备后重置相机来源状态但保持同一 MJPEG 连接', async ({ page }) => {
  let activeDeviceId = 'device-a'
  let catalogRequests = 0
  let streamRequests = 0
  let parameterRequests = 0
  let selectedDeviceRequest: unknown = null
  let selectedDeviceStatusRequests = 0
  let releaseSelection: (() => void) | undefined
  const selectionGate = new Promise<void>((resolve) => {
    releaseSelection = resolve
  })

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/cameras') {
      catalogRequests += 1
      await fulfillJson(route, multiCameraCatalog(activeDeviceId))
      return
    }
    if (path === '/api/cameras/sim-camera/selection' && request.method() === 'PUT') {
      selectedDeviceRequest = request.postDataJSON()
      await selectionGate
      activeDeviceId = 'device-b'
      await fulfillJson(route, multiCameraCatalog(activeDeviceId))
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      if (activeDeviceId === 'device-b') {
        selectedDeviceStatusRequests += 1
      }
      const latestFrameAt = activeDeviceId === 'device-b' && selectedDeviceStatusRequests > 1
        ? 1_000.4
        : 1_000.2
      await fulfillJson(route, cameraStatus(latestFrameAt))
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      streamRequests += 1
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      parameterRequests += 1
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const selector = page.getByTestId('camera-device-selector')
  const streamImage = page.getByTestId('camera-stream')
  await expect(selector).toHaveValue('device-a')
  await expect(streamImage).toBeVisible()
  const initialSource = await streamImage.getAttribute('src')
  const initialImage = await streamImage.elementHandle()

  await page.getByRole('button', { name: '刷新列表' }).click()
  await expect.poll(() => catalogRequests).toBe(2)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await initialImage?.evaluate((image) => image.isConnected)).toBeTruthy()

  await selector.selectOption('device-b')
  await expect.poll(() => selectedDeviceRequest).toEqual({ device_id: 'device-b' })
  await expect(page.getByText('正在切换采集设备...')).toBeVisible()
  expect(streamRequests).toBe(1)

  releaseSelection?.()
  await expect(selector).toHaveValue('device-b')
  await expect(page.getByText('正在等待所选相机画面...')).toBeVisible()
  await expect(page.getByText('正在等待所选相机画面...')).toHaveCount(0, { timeout: 5_000 })
  await expect.poll(() => parameterRequests, { timeout: 5_000 }).toBeGreaterThanOrEqual(2)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await initialImage?.evaluate((image) => image.isConnected)).toBeTruthy()
  expect(streamRequests).toBe(1)
})

test('切换前延迟返回的旧状态不能让新相机画面提前就绪', async ({ page }) => {
  await page.addInitScript(() => {
    const testWindow = window as typeof window & {
      __oldCameraStatusHeld: boolean
      __releaseOldCameraStatus?: () => void
    }
    const originalFetch = window.fetch.bind(window)
    let statusResponses = 0
    testWindow.__oldCameraStatusHeld = false
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const response = await originalFetch(input, init)
      const url = typeof input === 'string'
        ? input
        : input instanceof URL
          ? input.href
          : input.url
      if (new URL(url, window.location.href).pathname === '/api/cameras/sim-camera/status' && statusResponses++ === 0) {
        const detachedResponse = new Response(await response.text(), {
          status: response.status,
          headers: response.headers,
        })
        testWindow.__oldCameraStatusHeld = true
        await new Promise<void>((resolve) => {
          testWindow.__releaseOldCameraStatus = resolve
        })
        return detachedResponse
      }
      return response
    }
  })

  let activeDeviceId = 'device-a'
  let freshDeviceFrameAvailable = false
  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, multiCameraCatalog(activeDeviceId))
      return
    }
    if (path === '/api/cameras/sim-camera/selection' && request.method() === 'PUT') {
      activeDeviceId = 'device-b'
      await fulfillJson(route, multiCameraCatalog(activeDeviceId, 1_000.2))
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      const latestFrameAt = activeDeviceId === 'device-a'
        ? 1_000.3
        : freshDeviceFrameAvailable
          ? 1_000.4
          : 1_000.2
      await fulfillJson(route, cameraStatus(latestFrameAt))
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => (
    window as typeof window & { __oldCameraStatusHeld?: boolean }
  ).__oldCameraStatusHeld === true, undefined, { timeout: 5_000 })

  await page.getByTestId('camera-device-selector').selectOption('device-b')
  await expect(page.getByText('正在等待所选相机画面...')).toBeVisible()
  await page.evaluate(() => (
    window as typeof window & { __releaseOldCameraStatus?: () => void }
  ).__releaseOldCameraStatus?.())
  await page.waitForTimeout(250)
  await expect(page.getByText('正在等待所选相机画面...')).toBeVisible()

  freshDeviceFrameAvailable = true
  await expect(page.getByText('正在等待所选相机画面...')).toHaveCount(0, { timeout: 5_000 })
})

test('设备选择失败时保留原设备和连续画面', async ({ page }) => {
  let selectionRequests = 0
  let streamRequests = 0
  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, multiCameraCatalog('device-a'))
      return
    }
    if (path === '/api/cameras/sim-camera/selection' && request.method() === 'PUT') {
      selectionRequests += 1
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"code":"camera_selection_failed","message":"所选相机暂时无法启动采集。"}',
      })
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus())
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      streamRequests += 1
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, cameraParametersPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const selector = page.getByTestId('camera-device-selector')
  const streamImage = page.getByTestId('camera-stream')
  await expect(selector).toHaveValue('device-a')
  await expect(streamImage).toBeVisible()
  const initialSource = await streamImage.getAttribute('src')
  const initialImage = await streamImage.elementHandle()

  await selector.selectOption('device-b')
  await expect.poll(() => selectionRequests).toBe(1)
  await expect(page.getByRole('alert').filter({ hasText: '所选相机暂时无法启动采集。' })).toBeVisible()
  await page.waitForTimeout(2_300)
  await expect(page.getByRole('alert').filter({ hasText: '所选相机暂时无法启动采集。' })).toBeVisible()
  await expect(selector).toHaveValue('device-a')
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await initialImage?.evaluate((image) => image.isConnected)).toBeTruthy()
  expect(streamRequests).toBe(1)
})

test('旧设备延迟返回的参数写响应不会污染新设备参数', async ({ page }) => {
  let activeDeviceId = 'device-a'
  let delayedWriteReceived = false
  let delayedWriteCompleted = false
  let releaseDelayedWrite: (() => void) | undefined
  const delayedWriteGate = new Promise<void>((resolve) => {
    releaseDelayedWrite = resolve
  })

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, multiCameraCatalog(activeDeviceId))
      return
    }
    if (path === '/api/cameras/sim-camera/selection' && request.method() === 'PUT') {
      activeDeviceId = 'device-b'
      await fulfillJson(route, multiCameraCatalog(activeDeviceId))
      return
    }
    if (await fulfillPluginApi(route, path)) {
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus(activeDeviceId === 'device-b' ? 1_000.4 : 1_000.2))
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, posePayload(true))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters' && request.method() === 'GET') {
      await fulfillJson(route, liveCameraParametersPayload('Off'))
      return
    }
    if (path === '/api/cameras/sim-camera/parameters/exposure_auto' && request.method() === 'PATCH') {
      delayedWriteReceived = true
      await delayedWriteGate
      await fulfillJson(route, { ...liveCameraParametersPayload('Continuous'), restarted_acquisition: false })
      delayedWriteCompleted = true
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const exposureAuto = page.locator('#camera-parameter-exposure_auto')
  await expect(exposureAuto).toHaveValue('Off')
  await exposureAuto.focus()
  await exposureAuto.press('ArrowDown')
  await exposureAuto.press('Enter')
  await expect.poll(() => delayedWriteReceived).toBeTruthy()

  await page.getByTestId('camera-device-selector').selectOption('device-b')
  await expect(page.getByTestId('camera-device-selector')).toHaveValue('device-b')
  await expect(exposureAuto).toHaveValue('Off')

  releaseDelayedWrite?.()
  await expect.poll(() => delayedWriteCompleted).toBeTruthy()
  await expect(exposureAuto).toHaveValue('Off')
})

const transparentGif = Buffer.from('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', 'base64')

function cameraStatus(latestFrameAt = 1_000.2) {
  return {
    camera_id: 'sim-camera',
    state: 'streaming',
    latest_frame_at: latestFrameAt,
    error: null,
  }
}

function cameraCatalog(selectedDeviceId: string | null = 'sim-device') {
  return {
    cameras: [cameraStatus()],
    devices: [cameraDevice('sim-device', selectedDeviceId === 'sim-device')],
    selected_device_id: selectedDeviceId,
    selection_enabled: true,
    discovery_error: null,
  }
}

function multiCameraCatalog(selectedDeviceId: string, latestFrameAt = 1_000.2) {
  return {
    cameras: [cameraStatus(latestFrameAt)],
    devices: [
      cameraDevice('device-a', selectedDeviceId === 'device-a', '工作台相机 A'),
      cameraDevice('device-b', selectedDeviceId === 'device-b', '工作台相机 B'),
    ],
    selected_device_id: selectedDeviceId,
    selection_enabled: true,
    discovery_error: null,
  }
}

function cameraDevice(deviceId: string, selected: boolean, displayName = '模拟相机') {
  return {
    device_id: deviceId,
    display_name: displayName,
    model_name: 'SIM-1000',
    transport: 'simulation',
    selected,
    calibrated: true,
  }
}

function posePayload() {
  return {
    camera_id: 'sim-camera',
    enabled: false,
    captured_at: null,
    latest_frame_at: 1_000.2,
    overlay_fresh: false,
    valid: false,
    reason: 'disabled',
    target_joint: 'right_wrist',
    target: null,
    person: null,
    inference_latency_ms: null,
    lost_frames: 0,
    motion: null,
    draw_skeleton: false,
  }
}

function visionAnalysisPayload() {
  return {
    camera_id: 'sim-camera',
    frame_captured_at: 1_000.2,
    inference_captured_at: null,
    pose_enabled: false,
    valid: false,
    reason: 'disabled',
    frame: null,
    person_count: 0,
    persons: [],
    selected_person: null,
    joint_visibility: [],
    visible_joint_names: [],
  }
}

function cameraParametersPayload() {
  return {
    camera_id: 'sim-camera',
    write_enabled: false,
    parameters: [],
  }
}

function liveCameraParametersPayload(value: string) {
  return {
    camera_id: 'sim-camera',
    write_enabled: true,
    parameters: [
      {
        key: 'exposure_auto',
        kind: 'enum',
        apply_mode: 'live',
        value,
        minimum: null,
        maximum: null,
        step: null,
        unit: null,
        options: ['Off', 'Continuous'],
      },
    ],
  }
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function fulfillPluginApi(route: Route, path: string): Promise<boolean> {
  if (path === '/api/plugins') {
    await fulfillJson(route, { plugins: [visualPosePlugin()] })
    return true
  }
  if (path === '/api/plugins/reload' && route.request().method() === 'POST') {
    await fulfillJson(route, { plugins: [visualPosePlugin()] })
    return true
  }
  return false
}

function visualPosePlugin() {
  return {
    plugin_id: 'visual-pose-analysis',
    name: '视觉与姿态分析',
    version: '1.0.0',
    capabilities: ['frame_consumer', 'pose_tracking', 'vision_analysis'],
    ui_kind: 'visual-pose-analysis',
    state: 'running',
    enabled: true,
    lifecycle_controllable: true,
    error: null,
    reloadable: true,
  }
}
