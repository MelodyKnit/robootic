import { expect, test, type Page, type Route } from '@playwright/test'

test('显示工件轮廓、中心和位姿详情，并按 object-contain 的实际像素区域绘制', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await installObjectPoseRoutes(page, {
    plugins: () => [visualPosePlugin(), objectPosePlugin()],
    objectPayload: () => objectPosePayload(true),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const overlay = page.getByTestId('object-pose-overlay')
  await expect(overlay).toBeVisible()
  await expect(page.getByTestId('plugin-card-visual-pose-analysis')).toBeVisible()
  await page.getByTestId('plugin-details-visual-pose-analysis').locator('summary').click()
  await expect(page.getByTestId('plugin-camera-selector-visual-pose-analysis')).toHaveValue('sim-device')
  await expect(page.getByTestId('plugin-card-object-pose-analysis')).toBeVisible()
  await page.getByTestId('plugin-details-object-pose-analysis').locator('summary').click()
  await expect(page.getByRole('heading', { name: '工件位姿', exact: true })).toBeVisible()
  await expect(page.getByText('known-workpiece-v1', { exact: true })).toBeVisible()
  await expect(page.getByText('X / Y / Z', { exact: true })).toBeVisible()
  await expect(page.getByText('X / Y / Yaw', { exact: true })).toBeVisible()

  await expect.poll(async () => {
    const placement = await overlay.evaluate(measureObjectOverlayPlacement)
    return placement.contentTargetHasGreen
  }).toBeTruthy()
  const placement = await overlay.evaluate(measureObjectOverlayPlacement)
  if (placement.contentBoundsDifferFromImageBox) {
    expect(placement.oldOuterTargetHasGreen).toBeFalsy()
  }
})

test('过期工件快照隐藏叠加层且保持同一条 MJPEG 连接', async ({ page }) => {
  let objectRequests = 0
  await installObjectPoseRoutes(page, {
    objectPayload: () => {
      objectRequests += 1
      return objectPosePayload(objectRequests === 1)
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible()
  await expect(page.getByTestId('object-pose-overlay')).toBeVisible()
  const firstImage = await streamImage.elementHandle()
  const initialSource = await streamImage.getAttribute('src')

  await expect.poll(() => objectRequests, { timeout: 5_000 }).toBeGreaterThanOrEqual(2)
  await expect(page.getByTestId('object-pose-overlay')).toHaveCount(0)
  await expect(page.getByTestId('object-pose-overlay-stale')).toBeVisible()
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()
})

test('窄屏下工件叠加保持在相机预览容器内', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await installObjectPoseRoutes(page, {
    objectPayload: () => objectPosePayload(true),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const overlay = page.getByTestId('object-pose-overlay')
  await expect(overlay).toBeVisible()
  const previewBounds = await page.locator('section[aria-label="相机实时画面"]').boundingBox()
  const overlayBounds = await overlay.boundingBox()
  if (previewBounds === null || overlayBounds === null) {
    throw new Error('The compact preview must retain both the camera surface and object overlay.')
  }
  expect(previewBounds.width).toBeGreaterThan(300)
  expect(overlayBounds.width).toBeGreaterThan(0)
  expect(overlayBounds.x).toBeGreaterThanOrEqual(previewBounds.x)
  expect(overlayBounds.x + overlayBounds.width).toBeLessThanOrEqual(previewBounds.x + previewBounds.width)
})

test('空对象响应安全降级为无叠加结果', async ({ page }) => {
  await installObjectPoseRoutes(page, {
    objectPayload: () => emptyObjectPosePayload(),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByTestId('camera-stream')).toBeVisible()
  await expect(page.getByTestId('object-pose-overlay')).toHaveCount(0)
  await page.getByTestId('plugin-details-object-pose-analysis').locator('summary').click()
  await expect(page.getByText('未检测到唯一工件', { exact: true })).toBeVisible()
})

test('尚未收到首个工件结果时不误报叠加层过期', async ({ page }) => {
  await installObjectPoseRoutes(page, {
    objectPayload: () => waitingObjectPosePayload(),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByTestId('camera-stream')).toBeVisible()
  await expect(page.getByTestId('object-pose-overlay')).toHaveCount(0)
  await expect(page.getByTestId('object-pose-overlay-stale')).toHaveCount(0)
})

test('关闭工件位姿插件会持久化状态、停止轮询并清空叠加', async ({ page }) => {
  let plugin = objectPosePlugin()
  let objectRequests = 0
  const activationRequests: boolean[] = []
  await installObjectPoseRoutes(page, {
    plugins: () => [plugin],
    objectPayload: () => objectPosePayload(true),
    onObjectRequest: () => {
      objectRequests += 1
    },
    onPluginActivation: (pluginId, enabled) => {
      if (pluginId !== plugin.plugin_id) {
        return undefined
      }
      activationRequests.push(enabled)
      plugin = {
        ...plugin,
        enabled,
        state: enabled ? 'running' : 'stopped',
      }
      return plugin
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const streamImage = page.getByTestId('camera-stream')
  const card = page.getByTestId('plugin-card-object-pose-analysis')
  await expect(page.getByTestId('object-pose-overlay')).toBeVisible()
  await expect.poll(() => objectRequests).toBeGreaterThan(0)
  const initialSource = await streamImage.getAttribute('src')
  const firstImage = await streamImage.elementHandle()

  await card.getByTitle('关闭功能模块').click()
  await expect.poll(() => activationRequests).toEqual([false])
  await expect(page.getByTestId('plugin-activation-object-pose-analysis')).not.toBeChecked()
  await expect(card).toContainText('已关闭')
  await expect(page.getByTestId('object-pose-overlay')).toHaveCount(0)
  await expect(page.getByTestId('plugin-details-object-pose-analysis')).toHaveCount(0)
  const objectRequestsAfterDisable = objectRequests

  await page.waitForTimeout(700)
  expect(objectRequests).toBe(objectRequestsAfterDisable)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()
})

test('插件卡可切换共享采集相机并清空旧工件叠加', async ({ page }) => {
  let selectedDeviceId = 'sim-device'
  await installObjectPoseRoutes(page, {
    catalog: () => cameraCatalog(selectedDeviceId, true),
    objectPayload: () => (
      selectedDeviceId === 'sim-device'
        ? objectPosePayload(true)
        : objectPosePayload(true, false, [])
    ),
    selectDevice: (deviceId) => {
      selectedDeviceId = deviceId
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible()
  await expect(page.getByTestId('object-pose-overlay')).toBeVisible()
  const initialSource = await streamImage.getAttribute('src')

  await page.getByTestId('plugin-details-object-pose-analysis').locator('summary').click()
  const pluginSelector = page.getByTestId('plugin-camera-selector-object-pose-analysis')
  await expect(page.getByTestId('plugin-camera-binding-object-pose-analysis')).toContainText('相机输入')
  await expect(page.getByTestId('plugin-camera-binding-object-pose-analysis')).toContainText('共享单源')
  await expect(pluginSelector).toHaveValue('sim-device')

  await pluginSelector.selectOption('other-device')
  await expect(pluginSelector).toHaveValue('other-device')
  await expect(page.getByTestId('camera-device-selector')).toHaveValue('other-device')
  await expect(page.getByTestId('object-pose-overlay')).toHaveCount(0)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
})

test('相机绑定面板由服务声明驱动，不依赖已知插件标识', async ({ page }) => {
  await installObjectPoseRoutes(page, {
    plugins: () => [genericCameraPlugin()],
    objectPayload: () => waitingObjectPosePayload(),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.getByTestId('plugin-details-generic-camera-analysis').locator('summary').click()

  await expect(page.getByTestId('plugin-camera-binding-generic-camera-analysis')).toContainText('共享单源')
  await expect(page.getByTestId('plugin-camera-selector-generic-camera-analysis')).toHaveValue('sim-device')
})

test('人体姿态插件可切换共享设备并同步其他视觉插件', async ({ page }) => {
  let selectedDeviceId = 'sim-device'
  await installObjectPoseRoutes(page, {
    catalog: () => cameraCatalog(selectedDeviceId, true),
    plugins: () => [visualPosePlugin(), objectPosePlugin()],
    objectPayload: () => waitingObjectPosePayload(),
    selectDevice: (deviceId) => {
      selectedDeviceId = deviceId
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.getByTestId('plugin-details-visual-pose-analysis').locator('summary').click()
  await page.getByTestId('plugin-camera-selector-visual-pose-analysis').selectOption('other-device')

  await expect(page.getByTestId('camera-device-selector')).toHaveValue('other-device')
  await page.getByTestId('plugin-details-object-pose-analysis').locator('summary').click()
  await expect(page.getByTestId('plugin-camera-selector-object-pose-analysis')).toHaveValue('other-device')
})

type ObjectPoseRouteOptions = {
  catalog?: () => unknown
  onObjectRequest?: () => void
  onPluginActivation?: (pluginId: string, enabled: boolean) => unknown | undefined
  plugins?: () => unknown[]
  objectPayload: () => unknown
  selectDevice?: (deviceId: string) => void
}

async function installObjectPoseRoutes(page: Page, options: ObjectPoseRouteOptions): Promise<void> {
  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/plugins') {
      await fulfillJson(route, { plugins: options.plugins?.() ?? [objectPosePlugin()] })
      return
    }
    if (path === '/api/plugins/object-pose-analysis/activation' && request.method() === 'PUT') {
      const payload = request.postDataJSON() as { enabled?: unknown }
      if (typeof payload.enabled !== 'boolean') {
        await route.fulfill({ status: 422, contentType: 'application/json', body: '{"code":"invalid_enabled"}' })
        return
      }
      const updatedPlugin = options.onPluginActivation?.('object-pose-analysis', payload.enabled)
      if (updatedPlugin === undefined) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
        return
      }
      await fulfillJson(route, updatedPlugin)
      return
    }
    if (path === '/api/plugins/reload' && request.method() === 'POST') {
      await fulfillJson(route, { plugins: options.plugins?.() ?? [objectPosePlugin()] })
      return
    }
    if (path === '/api/cameras') {
      await fulfillJson(route, options.catalog?.() ?? cameraCatalog())
      return
    }
    if (path === '/api/cameras/sim-camera/selection' && request.method() === 'PUT') {
      const payload = request.postDataJSON() as { device_id?: unknown }
      if (typeof payload.device_id !== 'string') {
        await route.fulfill({ status: 422, contentType: 'application/json', body: '{"code":"invalid_device"}' })
        return
      }
      options.selectDevice?.(payload.device_id)
      await fulfillJson(route, options.catalog?.() ?? cameraCatalog(payload.device_id, true))
      return
    }
    if (path === '/api/cameras/sim-camera/status') {
      await fulfillJson(route, cameraStatus())
      return
    }
    if (path === '/api/cameras/sim-camera/stream') {
      await route.fulfill({ contentType: 'image/svg+xml', body: squareCameraSvg })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      await fulfillJson(route, disabledPosePayload())
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/objects') {
      options.onObjectRequest?.()
      await fulfillJson(route, options.objectPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })
}

const squareCameraSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><rect width="1000" height="1000" fill="#111827"/></svg>'

function cameraStatus() {
  return {
    camera_id: 'sim-camera',
    state: 'streaming',
    latest_frame_at: 1_000.2,
    error: null,
  }
}

function cameraCatalog(selectedDeviceId = 'sim-device', includeSecondDevice = false) {
  const devices = [cameraDevice('sim-device', selectedDeviceId === 'sim-device', '模拟相机')]
  if (includeSecondDevice) {
    devices.push(cameraDevice('other-device', selectedDeviceId === 'other-device', '备用相机'))
  }
  return {
    cameras: [cameraStatus()],
    devices,
    selected_device_id: selectedDeviceId,
    selection_enabled: true,
    discovery_error: null,
  }
}

function cameraDevice(deviceId: string, selected: boolean, displayName: string) {
  return {
    device_id: deviceId,
    display_name: displayName,
    model_name: 'SIM-1000',
    transport: 'simulation',
    selected,
    calibrated: true,
  }
}

function objectPosePayload(
  overlayFresh: boolean,
  valid = true,
  objects = [knownWorkpiece()],
) {
  return {
    camera_id: 'sim-camera',
    enabled: true,
    captured_at: 1_000,
    latest_frame_at: overlayFresh ? 1_000.2 : 1_001,
    overlay_fresh: overlayFresh,
    valid,
    reason: valid ? 'ok' : '未检测到唯一工件',
    inference_latency_ms: 82,
    objects,
  }
}

function emptyObjectPosePayload() {
  return {
    ...objectPosePayload(true, false, []),
    objects: null,
  }
}

function waitingObjectPosePayload() {
  return {
    ...objectPosePayload(false, false, []),
    captured_at: null,
    latest_frame_at: null,
    reason: 'object_pose_waiting_for_frame',
  }
}

function knownWorkpiece() {
  return {
    profile_id: 'known-workpiece-v1',
    confidence: 0.94,
    bounding_box: { x: 0.18, y: 0.23, width: 0.34, height: 0.16 },
    contour: [
      { x: 0.18, y: 0.25 },
      { x: 0.46, y: 0.23 },
      { x: 0.52, y: 0.32 },
      { x: 0.22, y: 0.39 },
    ],
    pixel_center: { x: 860, y: 640 },
    normalized_center: { x: 0.35, y: 0.31 },
    coordinate_frame: 'jaka_base',
    translation_mm: { x: 124.8, y: -42.1, z: 8.0 },
    orientation_rpy_rad: { roll: 0, pitch: 0, yaw: 0.52 },
    observed_dof: ['X', 'Y', 'Yaw'],
    derived_dof: ['Z', 'Roll', 'Pitch'],
    yaw_period_rad: 6.283185307,
    orientation_defined: true,
    warning: null,
  }
}

function disabledPosePayload() {
  return {
    camera_id: 'sim-camera',
    enabled: false,
    captured_at: null,
    latest_frame_at: null,
    overlay_fresh: false,
    valid: false,
    reason: '姿态识别未启用',
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
    frame_captured_at: null,
    inference_captured_at: null,
    pose_enabled: false,
    valid: true,
    reason: 'ok',
    frame: null,
    person_count: 0,
    persons: [],
    selected_person: null,
    joint_visibility: [],
    visible_joint_names: [],
  }
}

function objectPosePlugin() {
  return {
    plugin_id: 'object-pose-analysis',
    name: '工件位姿分析',
    version: '1.0.0',
    capabilities: ['frame_consumer', 'object_pose'],
    ui_kind: 'object-pose-analysis',
    state: 'running',
    enabled: true,
    lifecycle_controllable: true,
    error: null,
    reloadable: true,
    camera_binding: sharedCameraBinding(),
  }
}

function visualPosePlugin() {
  return {
    plugin_id: 'visual-pose-analysis',
    name: '人体姿态分析',
    version: '1.0.0',
    capabilities: ['frame_consumer', 'pose_tracking'],
    ui_kind: 'visual-pose-analysis',
    state: 'running',
    enabled: true,
    lifecycle_controllable: true,
    error: null,
    reloadable: true,
    camera_binding: sharedCameraBinding(),
  }
}

function genericCameraPlugin() {
  return {
    plugin_id: 'generic-camera-analysis',
    name: '通用相机分析',
    version: '1.0.0',
    capabilities: ['frame_consumer'],
    ui_kind: 'generic',
    state: 'running',
    enabled: true,
    lifecycle_controllable: true,
    error: null,
    reloadable: false,
    camera_binding: sharedCameraBinding(),
  }
}

function sharedCameraBinding() {
  return {
    mode: 'shared_single_source',
    camera_ids: ['sim-camera'],
    minimum_sources: 1,
    maximum_sources: 1,
    state: 'satisfied',
  }
}

function measureObjectOverlayPlacement(canvas: HTMLCanvasElement) {
  const image = canvas.parentElement?.querySelector('img')
  if (!(image instanceof HTMLImageElement)) {
    throw new Error('The object-pose canvas must share a parent with the camera image.')
  }
  const canvasRect = canvas.getBoundingClientRect()
  const imageRect = image.getBoundingClientRect()
  const scale = Math.min(
    imageRect.width / image.naturalWidth,
    imageRect.height / image.naturalHeight,
  )
  const contentWidth = image.naturalWidth * scale
  const contentHeight = image.naturalHeight * scale
  const contentTargetX = imageRect.left + (imageRect.width - contentWidth) / 2 + contentWidth * 0.35
  const contentTargetY = imageRect.top + (imageRect.height - contentHeight) / 2 + contentHeight * 0.31
  const oldOuterTargetX = canvasRect.left + canvasRect.width * 0.35
  const oldOuterTargetY = canvasRect.top + canvasRect.height * 0.31
  const context = canvas.getContext('2d')
  if (context === null) {
    throw new Error('The object-pose canvas must expose a 2D context.')
  }
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
  const bitmapScaleX = canvas.width / canvasRect.width
  const bitmapScaleY = canvas.height / canvasRect.height

  function hasGreenNear(pageX: number, pageY: number): boolean {
    const centerX = Math.round((pageX - canvasRect.left) * bitmapScaleX)
    const centerY = Math.round((pageY - canvasRect.top) * bitmapScaleY)
    const radiusX = Math.max(1, Math.ceil(9 * bitmapScaleX))
    const radiusY = Math.max(1, Math.ceil(9 * bitmapScaleY))
    for (let y = Math.max(0, centerY - radiusY); y <= Math.min(canvas.height - 1, centerY + radiusY); y += 1) {
      for (let x = Math.max(0, centerX - radiusX); x <= Math.min(canvas.width - 1, centerX + radiusX); x += 1) {
        const offset = (y * canvas.width + x) * 4
        if (
          pixels[offset] >= 30 &&
          pixels[offset] <= 100 &&
          pixels[offset + 1] >= 160 &&
          pixels[offset + 2] >= 110 &&
          pixels[offset + 2] <= 210 &&
          pixels[offset + 3] >= 180
        ) {
          return true
        }
      }
    }
    return false
  }

  return {
    contentTargetHasGreen: hasGreenNear(contentTargetX, contentTargetY),
    oldOuterTargetHasGreen: hasGreenNear(oldOuterTargetX, oldOuterTargetY),
    contentBoundsDifferFromImageBox:
      Math.abs(contentWidth - imageRect.width) > 1 || Math.abs(contentHeight - imageRect.height) > 1,
  }
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}
