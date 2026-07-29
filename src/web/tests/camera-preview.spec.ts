import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Route } from '@playwright/test'

test('显示可解码的实时相机画面', async ({ page }) => {
  await page.setViewportSize({ width: 1_600, height: 960 })
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog())
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

  await expect(page.getByRole('heading', { name: '相机预览' })).toBeVisible()
  await expect(page.getByTestId('camera-device-selector')).toHaveValue('sim-device')
  await expect(page.getByTestId('camera-device-selector').locator('option')).toHaveCount(1)
  await expect(page.getByTestId('plugin-card-visual-pose-analysis')).toBeVisible()
  await expect(page.getByTestId('camera-adapter')).toBeVisible()
  await expect(page.getByTestId('gripper-adapter')).toBeVisible()
  await expect(page.getByTestId('robot-adapter')).toBeVisible()
  const pluginBounds = await page.getByTestId('plugin-workspace').boundingBox()
  const previewBounds = await page.locator('section[aria-label="相机实时画面"]').boundingBox()
  const adapterBounds = await page.locator('aside[aria-label="硬件适配器"]').boundingBox()
  if (pluginBounds === null || previewBounds === null || adapterBounds === null) {
    throw new Error('Desktop workbench must render all three columns.')
  }
  expect(pluginBounds.x).toBeLessThan(previewBounds.x)
  expect(previewBounds.x).toBeLessThan(adapterBounds.x)

  await page.getByTestId('plugin-details-visual-pose-analysis').locator('summary').click()
  await expect(page.getByRole('heading', { name: '人体姿态' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '成像与识别' })).toBeVisible()
  await expect(page.getByText('主人体框', { exact: true })).toBeVisible()
  await expect(page.getByLabel('锁定关节')).toBeVisible()

  await expect(page.getByRole('heading', { name: '相机参数' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('slider', { name: '曝光时间' })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存并重新采集' })).toBeVisible()

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible({ timeout: 15_000 })
  await expect
    .poll(async () => streamImage.evaluate((image: HTMLImageElement) => image.naturalWidth), {
      timeout: 15_000,
    })
    .toBeGreaterThan(0)

  const screenshotDirectory = join('..', '..', 'temp', 'gripper-ai-controller')
  mkdirSync(screenshotDirectory, { recursive: true })
  await page.screenshot({ path: join(screenshotDirectory, 'camera-preview-browser.png'), fullPage: true })
})

test('姿态刷新不会替换 MJPEG，过期时隐藏叠加且只在流错误后重连', async ({ page }) => {
  let poseRequests = 0
  let poseFrameRequested = false
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/pose/frame')) {
      poseFrameRequested = true
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
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
      await route.fulfill({ contentType: 'image/gif', body: transparentGif })
      return
    }
    if (path === '/api/cameras/sim-camera/pose') {
      poseRequests += 1
      await fulfillJson(route, posePayload(poseRequests === 1))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible()
  await expect(page.getByTestId('pose-overlay')).toBeVisible()
  const firstImage = await streamImage.elementHandle()
  const initialSource = await streamImage.getAttribute('src')
  expect(initialSource).toContain('/api/cameras/sim-camera/stream?attempt=1')

  await expect.poll(() => poseRequests, { timeout: 5_000 }).toBeGreaterThanOrEqual(2)
  await expect(page.getByTestId('pose-overlay-stale')).toBeVisible()
  await expect(page.getByTestId('pose-overlay')).toHaveCount(0)
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()
  expect(poseFrameRequested).toBeFalsy()

  await streamImage.evaluate((image) => image.dispatchEvent(new Event('error')))
  await expect.poll(() => streamImage.getAttribute('src'), { timeout: 5_000 }).not.toBe(initialSource)
  await expect(streamImage).toHaveAttribute('src', /attempt=2$/)
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeFalsy()
})

test('插件展开和重载不会替换连续 MJPEG，已注册的通用插件使用状态卡', async ({ page }) => {
  const reloadRequests: unknown[] = []
  const plugins = [
    visualPosePlugin(),
    {
      plugin_id: 'runtime-audit',
      name: '运行审计',
      version: '1.0.0',
      capabilities: ['audit'],
      ui_kind: 'generic',
      state: 'running',
      error: null,
      reloadable: false,
    },
  ]
  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/plugins') {
      await fulfillJson(route, { plugins })
      return
    }
    if (path === '/api/plugins/reload' && request.method() === 'POST') {
      reloadRequests.push(request.postDataJSON())
      await fulfillJson(route, { plugins })
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
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible()
  const firstImage = await streamImage.elementHandle()
  const initialSource = await streamImage.getAttribute('src')

  await expect(page.getByTestId('plugin-card-runtime-audit')).toContainText('该已注册模块暂未提供专用网页面板。')
  await page.getByTestId('plugin-details-visual-pose-analysis').locator('summary').click()
  await expect(page.getByRole('heading', { name: '人体姿态' })).toBeVisible()
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()

  await page.getByTestId('plugin-card-visual-pose-analysis').getByRole('button', { name: '重载 视觉与姿态分析' }).click()
  await expect.poll(() => reloadRequests.length).toBe(1)
  expect(reloadRequests[0]).toEqual({ plugin_ids: ['visual-pose-analysis'] })
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
  expect(await firstImage?.evaluate((image) => image.isConnected)).toBeTruthy()

  await page.getByRole('button', { name: '重载全部' }).click()
  await expect.poll(() => reloadRequests.length).toBe(2)
  expect(reloadRequests[1]).toEqual({ plugin_ids: ['visual-pose-analysis'] })
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
})

test('锁定关节暂时不可用时仍显示新鲜人体骨架', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog())
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
      await fulfillJson(route, posePayload(true, false))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByTestId('camera-stream')).toBeVisible()
  await expect(page.getByTestId('pose-overlay')).toBeVisible()
  await expect(page.getByTestId('pose-overlay-stale')).toHaveCount(0)
})

test('姿态读取失败会隐藏已有骨架而不重连实时画面', async ({ page }) => {
  let poseRequests = 0
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog())
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
      poseRequests += 1
      if (poseRequests === 1) {
        await fulfillJson(route, posePayload(true))
      } else {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: '{"code":"pose_unavailable","message":"Pose metadata is unavailable."}',
        })
      }
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload())
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible()
  const initialSource = await streamImage.getAttribute('src')
  await expect(page.getByTestId('pose-overlay')).toBeVisible()
  await expect.poll(() => poseRequests, { timeout: 5_000 }).toBeGreaterThanOrEqual(2)
  await expect(page.getByTestId('pose-overlay')).toHaveCount(0)
  await expect(page.getByTestId('pose-overlay-stale')).toBeVisible()
  await expect(streamImage).toHaveAttribute('src', initialSource ?? '')
})

test('窄屏保持完整的预览宽度并将侧栏移到下方', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cameras') {
      await fulfillJson(route, cameraCatalog())
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
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('camera-stream')).toBeVisible()
  const previewBounds = await page.locator('section[aria-label="相机实时画面"]').boundingBox()
  const adapterBounds = await page.locator('aside[aria-label="硬件适配器"]').boundingBox()
  if (previewBounds === null || adapterBounds === null) {
    throw new Error('The compact layout must retain both the preview and the adapter column.')
  }
  expect(previewBounds.width).toBeGreaterThan(300)
  expect(adapterBounds.width).toBeGreaterThan(300)
  expect(adapterBounds.y).toBeGreaterThan(previewBounds.y)
})

test('骨架按 object-contain 的实际相机像素区域绘制', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
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
      await fulfillJson(route, posePayload(true, true, { x: 0.1, y: 0.2 }))
      return
    }
    if (path === '/api/cameras/sim-camera/vision/analysis') {
      await fulfillJson(route, visionAnalysisPayload(false))
      return
    }
    if (path === '/api/cameras/sim-camera/parameters') {
      await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByTestId('camera-stream')).toBeVisible()
  await expect(page.getByTestId('pose-overlay')).toBeVisible()

  const overlay = page.getByTestId('pose-overlay')
  await expect.poll(async () => {
    const placement = await overlay.evaluate(measureOverlayPlacement)
    return placement.contentTargetHasOrange
  }).toBeTruthy()
  const placement = await overlay.evaluate(measureOverlayPlacement)
  if (placement.contentBoundsDifferFromImageBox) {
    expect(placement.oldOuterTargetHasOrange).toBeFalsy()
  }
})

const transparentGif = Buffer.from('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', 'base64')
const squareCameraSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><rect width="1000" height="1000" fill="#111827"/></svg>'

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

function posePayload(overlayFresh: boolean, valid = true, targetPosition = { x: 0.5, y: 0.4 }) {
  const person = {
    camera_id: 'sim-camera',
    captured_at: 1_000,
    bounding_box: { x: 0.2, y: 0.15, width: 0.45, height: 0.7 },
    confidence: 0.95,
    joints: [
      {
        name: 'right_wrist',
        x_px: targetPosition.x * 2_448,
        y_px: targetPosition.y * 2_048,
        normalized_x: targetPosition.x,
        normalized_y: targetPosition.y,
        confidence: 0.9,
      },
    ],
  }
  return {
    camera_id: 'sim-camera',
    enabled: true,
    captured_at: 1_000,
    latest_frame_at: overlayFresh ? 1_000.2 : 1_001,
    overlay_fresh: overlayFresh,
    valid,
    reason: valid ? 'ok' : 'target is temporarily unavailable',
    target_joint: 'right_wrist',
    target: valid ? person.joints[0] : null,
    person,
    inference_latency_ms: 100,
    lost_frames: valid ? 0 : 1,
    motion: null,
    draw_skeleton: true,
  }
}

function visionAnalysisPayload(includePerson = true) {
  const selectedPerson = posePayload(true).person
  return {
    camera_id: 'sim-camera',
    frame_captured_at: 1_000.2,
    inference_captured_at: 1_000,
    pose_enabled: true,
    valid: true,
    reason: 'ok',
    frame: null,
    person_count: includePerson ? 1 : 0,
    persons: includePerson
      ? [
          {
            bounding_box: { x: 0.2, y: 0.15, width: 0.45, height: 0.7 },
            confidence: 0.95,
            selected: true,
          },
        ]
      : [],
    selected_person: includePerson ? selectedPerson : null,
    joint_visibility: [],
    visible_joint_names: [],
  }
}

function cameraParametersPayload() {
  return {
    camera_id: 'sim-camera',
    write_enabled: false,
    parameters: [
      {
        key: 'exposure_time_us',
        kind: 'float',
        apply_mode: 'restart',
        value: 8_000,
        minimum: 10,
        maximum: 50_000,
        step: 1,
        unit: 'us',
        options: [],
      },
    ],
  }
}

function measureOverlayPlacement(canvas: HTMLCanvasElement) {
  const image = canvas.parentElement?.querySelector('img')
  if (!(image instanceof HTMLImageElement)) {
    throw new Error('The pose canvas must share a parent with the camera image.')
  }
  const canvasRect = canvas.getBoundingClientRect()
  const imageRect = image.getBoundingClientRect()
  const scale = Math.min(
    imageRect.width / image.naturalWidth,
    imageRect.height / image.naturalHeight,
  )
  const contentWidth = image.naturalWidth * scale
  const contentHeight = image.naturalHeight * scale
  const contentTargetX = imageRect.left + (imageRect.width - contentWidth) / 2 + contentWidth * 0.1
  const contentTargetY = imageRect.top + (imageRect.height - contentHeight) / 2 + contentHeight * 0.2
  const oldOuterTargetX = canvasRect.left + canvasRect.width * 0.1
  const oldOuterTargetY = canvasRect.top + canvasRect.height * 0.2
  const context = canvas.getContext('2d')
  if (context === null) {
    throw new Error('The pose canvas must expose a 2D context.')
  }
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
  const bitmapScaleX = canvas.width / canvasRect.width
  const bitmapScaleY = canvas.height / canvasRect.height

  function hasOrangeNear(pageX: number, pageY: number): boolean {
    const centerX = Math.round((pageX - canvasRect.left) * bitmapScaleX)
    const centerY = Math.round((pageY - canvasRect.top) * bitmapScaleY)
    const radiusX = Math.max(1, Math.ceil(7 * bitmapScaleX))
    const radiusY = Math.max(1, Math.ceil(7 * bitmapScaleY))
    for (let y = Math.max(0, centerY - radiusY); y <= Math.min(canvas.height - 1, centerY + radiusY); y += 1) {
      for (let x = Math.max(0, centerX - radiusX); x <= Math.min(canvas.width - 1, centerX + radiusX); x += 1) {
        const offset = (y * canvas.width + x) * 4
        if (
          pixels[offset] >= 230 &&
          pixels[offset + 1] >= 130 &&
          pixels[offset + 1] <= 180 &&
          pixels[offset + 2] <= 40 &&
          pixels[offset + 3] >= 200
        ) {
          return true
        }
      }
    }
    return false
  }

  return {
    contentTargetHasOrange: hasOrangeNear(contentTargetX, contentTargetY),
    oldOuterTargetHasOrange: hasOrangeNear(oldOuterTargetX, oldOuterTargetY),
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
    error: null,
    reloadable: true,
  }
}
