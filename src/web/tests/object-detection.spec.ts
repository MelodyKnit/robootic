import { expect, test, type Page, type Route } from '@playwright/test'

test('通用检测插件显示多个框、类别和置信度', async ({ page }) => {
  await installDetectionRoutes(page, {
    detectionPayload: () => detectionPayload(true),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const overlay = page.getByTestId('object-detection-overlay')
  await expect(overlay).toBeVisible()
  await expect(page.getByTestId('plugin-card-object-detection-analysis')).toBeVisible()
  await page.getByTestId('plugin-details-object-detection-analysis').locator('summary').click()
  await expect(page.getByRole('heading', { name: '物品检测', exact: true })).toBeVisible()
  await expect(page.getByTestId('detection-model-selector-object-detection-analysis')).toHaveValue('yolo-world-s')
  await expect(page.getByText('2 个目标', { exact: true })).toBeVisible()

  const canvasBox = await overlay.boundingBox()
  const previewBox = await page.locator('section[aria-label="相机实时画面"]').boundingBox()
  if (canvasBox === null || previewBox === null) {
    throw new Error('The generic detection overlay must be mounted in the camera preview.')
  }
  expect(canvasBox.x).toBeGreaterThanOrEqual(previewBox.x)
  expect(canvasBox.y).toBeGreaterThanOrEqual(previewBox.y)
  expect(canvasBox.x + canvasBox.width).toBeLessThanOrEqual(previewBox.x + previewBox.width)
  expect(canvasBox.y + canvasBox.height).toBeLessThanOrEqual(previewBox.y + previewBox.height)
})

test('选择通用检测模型调用模型选择接口并隐藏过期框', async ({ page }) => {
  let selectedModelId = 'yolo-world-s'
  let modelSelectionRequests = 0
  await installDetectionRoutes(page, {
    detectionPayload: () => detectionPayload(selectedModelId === 'yolo-world-s'),
    onModelSelection: (modelId) => {
      selectedModelId = modelId
      modelSelectionRequests += 1
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('object-detection-overlay')).toBeVisible()
  await page.getByTestId('plugin-details-object-detection-analysis').locator('summary').click()

  const modelSelector = page.getByTestId('detection-model-selector-object-detection-analysis')
  await modelSelector.selectOption('yolo-world-m')
  await expect.poll(() => modelSelectionRequests).toBe(1)
  await expect(modelSelector).toHaveValue('yolo-world-m')
  await expect(page.getByTestId('object-detection-overlay')).toHaveCount(0)
  await expect(page.getByTestId('object-detection-overlay-stale')).toBeVisible()
})

test('尚未收到首个检测结果时不误报叠加层过期', async ({ page }) => {
  await installDetectionRoutes(page, {
    detectionPayload: () => waitingDetectionPayload(),
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByTestId('camera-stream')).toBeVisible()
  await expect(page.getByTestId('object-detection-overlay')).toHaveCount(0)
  await expect(page.getByTestId('object-detection-overlay-stale')).toHaveCount(0)
})

test('关闭检测插件会持久化状态、停止轮询并清空叠加', async ({ page }) => {
  const activationRequests: boolean[] = []
  let detectionRequests = 0
  await installDetectionRoutes(page, {
    detectionPayload: () => detectionPayload(true),
    onDetectionRequest: () => {
      detectionRequests += 1
    },
    onPluginActivation: (enabled) => {
      activationRequests.push(enabled)
    },
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const card = page.getByTestId('plugin-card-object-detection-analysis')
  const streamImage = page.getByTestId('camera-stream')
  await expect(page.getByTestId('object-detection-overlay')).toBeVisible()
  await expect.poll(() => detectionRequests).toBeGreaterThan(0)
  const initialSource = await streamImage.getAttribute('src')
  const firstImage = await streamImage.elementHandle()

  await card.getByTitle('关闭功能模块').click()
  await expect.poll(() => activationRequests).toEqual([false])
  await expect(page.getByTestId('plugin-activation-object-detection-analysis')).not.toBeChecked()
  await expect(card).toContainText('已关闭')
  await expect(page.getByTestId('object-detection-overlay')).toHaveCount(0)
  await expect(page.getByTestId('plugin-details-object-detection-analysis')).toHaveCount(0)
  const detectionRequestsAfterDisable = detectionRequests

  await page.waitForTimeout(700)
  expect(detectionRequests).toBe(detectionRequestsAfterDisable)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()
})

type DetectionRouteOptions = {
  detectionPayload: () => unknown
  onDetectionRequest?: () => void
  onModelSelection?: (modelId: string) => void
  onPluginActivation?: (enabled: boolean) => void
}

async function installDetectionRoutes(page: Page, options: DetectionRouteOptions): Promise<void> {
  let plugin = detectionPlugin()
  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/plugins') {
      await fulfillJson(route, {
        plugins: [plugin],
      })
      return
    }
    if (path === '/api/plugins/object-detection-analysis/activation' && request.method() === 'PUT') {
      const body = request.postDataJSON() as { enabled?: unknown }
      if (typeof body.enabled !== 'boolean') {
        await route.fulfill({ status: 422, contentType: 'application/json', body: '{"code":"invalid_enabled"}' })
        return
      }
      plugin = {
        ...plugin,
        enabled: body.enabled,
        state: body.enabled ? 'running' : 'stopped',
      }
      options.onPluginActivation?.(body.enabled)
      await fulfillJson(route, plugin)
      return
    }
    if (path === '/api/plugins/reload' && request.method() === 'POST') {
      await fulfillJson(route, { plugins: [] })
      return
    }
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog())
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
      await fulfillJson(route, emptyObjectPosePayload())
      return
    }
    if (path === '/api/cameras/sim-camera/detections/model-selection' && request.method() === 'PUT') {
      const body = request.postDataJSON() as { model_id?: unknown }
      if (typeof body.model_id !== 'string') {
        await route.fulfill({ status: 422, contentType: 'application/json', body: '{"code":"invalid_model"}' })
        return
      }
      options.onModelSelection?.(body.model_id)
      await fulfillJson(route, detectionPayload(false, body.model_id))
      return
    }
    if (path === '/api/cameras/sim-camera/detections') {
      options.onDetectionRequest?.()
      await fulfillJson(route, options.detectionPayload())
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })
}

function detectionPlugin() {
  return {
    plugin_id: 'object-detection-analysis',
    name: '通用物品检测',
    version: '1.0.0',
    capabilities: ['frame_consumer', 'object_detection'],
    ui_kind: 'object-detection-analysis',
    state: 'running',
    enabled: true,
    lifecycle_controllable: true,
    error: null,
    reloadable: true,
    camera_binding: {
      mode: 'shared_single_source',
      camera_ids: ['sim-camera'],
      minimum_sources: 1,
      maximum_sources: 1,
      state: 'satisfied',
    },
  }
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

function cameraCatalog() {
  return {
    cameras: [cameraStatus()],
    devices: [{
      device_id: 'sim-device',
      display_name: '模拟相机',
      model_name: 'SIM-1000',
      transport: 'simulation',
      selected: true,
      calibrated: true,
    }],
    selected_device_id: 'sim-device',
    selection_enabled: true,
    discovery_error: null,
  }
}

function detectionPayload(overlayFresh: boolean, selectedModelId = 'yolo-world-s') {
  return {
    camera_id: 'sim-camera',
    enabled: true,
    selected_model_id: selectedModelId,
    models: [
      { model_id: 'yolo-world-s', display_name: 'YOLO-World Small', provider: 'ultralytics', available: true, selected: selectedModelId === 'yolo-world-s' },
      { model_id: 'yolo-world-m', display_name: 'YOLO-World Medium', provider: 'ultralytics', available: true, selected: selectedModelId === 'yolo-world-m' },
    ],
    captured_at: 1_000,
    latest_frame_at: overlayFresh ? 1_000.2 : 1_001,
    overlay_fresh: overlayFresh,
    valid: overlayFresh,
    reason: overlayFresh ? 'ok' : 'stale_result',
    inference_latency_ms: 65,
    detections: overlayFresh ? [
      { detection_id: 'det-1', label: 'wrench', class_id: 0, confidence: 0.94, bounding_box: { x: 0.18, y: 0.23, width: 0.34, height: 0.16 } },
      { detection_id: 'det-2', label: 'rod', class_id: null, confidence: 0.88, bounding_box: { x: 0.61, y: 0.42, width: 0.12, height: 0.31 } },
    ] : [],
  }
}

function waitingDetectionPayload() {
  return {
    ...detectionPayload(false),
    captured_at: null,
    latest_frame_at: null,
    reason: 'object_detection_waiting_for_frame',
  }
}

function disabledPosePayload() {
  return {
    camera_id: 'sim-camera', enabled: false, captured_at: null, latest_frame_at: null,
    overlay_fresh: false, valid: false, reason: '姿态识别未启用', target_joint: 'right_wrist',
    target: null, person: null, inference_latency_ms: null, lost_frames: 0, motion: null, draw_skeleton: false,
  }
}

function visionAnalysisPayload() {
  return {
    camera_id: 'sim-camera', frame_captured_at: null, inference_captured_at: null, pose_enabled: false,
    valid: true, reason: 'ok', frame: null, person_count: 0, persons: [], selected_person: null,
    joint_visibility: [], visible_joint_names: [],
  }
}

function emptyObjectPosePayload() {
  return {
    camera_id: 'sim-camera', enabled: false, captured_at: null, latest_frame_at: null,
    overlay_fresh: false, valid: false, reason: 'object_pose_disabled', inference_latency_ms: null, objects: [],
  }
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) })
}
