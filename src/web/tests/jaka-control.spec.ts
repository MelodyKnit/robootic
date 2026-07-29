import { expect, test, type Route } from '@playwright/test'

test('默认只读显示 J1 至 J6，且不会发出使能或运动请求', async ({ page }) => {
  const operations: Array<{ path: string; method: string }> = []
  const readOnlyStatus = robotStatus({
    controls_enabled: false,
    manual_motion_enabled: false,
    joint_positions_rad: [-1.05, 0, 0, 0, 0, 0],
  })

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    operations.push({ path, method: request.method() })
    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [readOnlyStatus] })
      return
    }
    if (path === '/api/robots/jaka-primary/status') {
      await fulfillJson(route, readOnlyStatus)
      return
    }
    await notFound(route)
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const robotPanel = page.getByTestId('robot-adapter')

  await expect(robotPanel.getByRole('heading', { name: '机械臂控制' })).toBeVisible()
  await expect(robotPanel.getByText('J1', { exact: true })).toBeVisible()
  await expect(robotPanel.getByText('J6', { exact: true })).toBeVisible()
  await expect(robotPanel.getByText('当前本机配置仅允许读取机械臂状态。')).toBeVisible()
  await expect(robotPanel.getByRole('button', { name: '解锁控制' })).toBeDisabled()
  await expect(robotPanel.getByText('网页不提供软件急停。')).toHaveCount(0)
  expect(operations.filter((operation) => operation.method !== 'GET')).toEqual([])
})

test('草稿步进、预览和二次确认才提交一条绝对关节运动', async ({ page }) => {
  let currentStatus = robotStatus({ enabled: false })
  let armBody: unknown = null
  let enableHeaders: Record<string, string> | null = null
  let previewBody: unknown = null
  let nextPreviewLifetimeSeconds = 20
  let disarmToken: string | null = null
  const commands: Array<{ body: unknown; headers: Record<string, string> }> = []

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [currentStatus] })
      return
    }
    if (path === '/api/robots/jaka-primary/status') {
      await fulfillJson(route, currentStatus)
      return
    }
    if (path === '/api/robots/jaka-primary/arm' && request.method() === 'POST') {
      armBody = request.postDataJSON()
      currentStatus = { ...currentStatus, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, {
        token: 'jaka-browser-token',
        expires_at: currentStatus.armed_until,
        status: currentStatus,
      })
      return
    }
    if (path === '/api/robots/jaka-primary/arm' && request.method() === 'DELETE') {
      disarmToken = request.headers()['x-robot-control-token'] ?? null
      currentStatus = { ...currentStatus, armed_until: null }
      await route.fulfill({ status: 204 })
      return
    }
    if (path === '/api/robots/jaka-primary/enable') {
      enableHeaders = request.headers()
      currentStatus = { ...currentStatus, enabled: true, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, operationResult(request, currentStatus))
      return
    }
    if (path === '/api/robots/jaka-primary/joint-moves/preview') {
      previewBody = request.postDataJSON()
      const target = (previewBody as { joint_positions_rad: number[] }).joint_positions_rad
      await fulfillJson(route, {
        preview_id: 'joint-preview-1',
        expires_at: Date.now() / 1_000 + nextPreviewLifetimeSeconds,
        source_joint_positions_rad: currentStatus.joint_positions_rad,
        target_joint_positions_rad: target,
        joint_deltas_rad: target.map((value, index) => value - currentStatus.joint_positions_rad[index]),
        estimated_duration_seconds: 0.5,
        status: currentStatus,
      })
      return
    }
    if (path === '/api/robots/jaka-primary/commands') {
      commands.push({ body: request.postDataJSON(), headers: request.headers() })
      currentStatus = {
        ...currentStatus,
        joint_positions_rad: [Math.PI / 36, 0, 0, 0, 0, 0],
        armed_until: Date.now() / 1_000 + 60,
      }
      await fulfillJson(route, operationResult(request, currentStatus))
      return
    }
    await notFound(route)
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const robotPanel = page.getByTestId('robot-adapter')

  await robotPanel.getByRole('button', { name: '解锁控制' }).click()
  const armDialog = page.getByRole('dialog', { name: '确认解锁机械臂控制' })
  await armDialog.getByText('工作区已清空', { exact: false }).click()
  await armDialog.getByText('现场独立急停可用', { exact: false }).click()
  await armDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(robotPanel.getByText(/已解锁，剩余/)).toBeVisible()
  expect(armBody).toEqual({ work_area_clear: true, emergency_stop_ready: true })

  await robotPanel.getByRole('button', { name: '使能伺服' }).click()
  const enableDialog = page.getByRole('dialog', { name: '确认使能 JAKA 伺服' })
  await enableDialog.getByText('已确认机械臂已上电', { exact: false }).click()
  await enableDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(robotPanel.getByRole('button', { name: '伺服已使能' })).toBeVisible()
  expect(enableHeaders?.['x-robot-control-token']).toBe('jaka-browser-token')
  expect(enableHeaders?.['idempotency-key']).toBeTruthy()

  await robotPanel.getByLabel('启用草稿步进微调').check()
  await robotPanel.getByRole('button', { name: '增加 J1 草稿角度' }).click()
  await expect(robotPanel.getByLabel('J1 目标角度')).toHaveValue('2')
  await page.waitForTimeout(250)
  expect(previewBody).toBeNull()
  expect(commands).toHaveLength(0)

  await robotPanel.getByLabel('J1 目标角度').fill('5')
  await robotPanel.getByLabel('关节速度').fill('0.2')
  expect(previewBody).toBeNull()
  expect(commands).toHaveLength(0)

  await robotPanel.getByRole('button', { name: '生成动作预览' }).click()
  await expect.poll(() => previewBody).not.toBeNull()
  expect(previewBody).toEqual({
    joint_positions_rad: [Math.PI / 36, 0, 0, 0, 0, 0],
    speed_rad_per_second: 0.2,
  })
  expect(commands).toHaveLength(0)

  await robotPanel.getByRole('button', { name: '增加 J1 草稿角度' }).click()
  await expect(robotPanel.getByLabel('J1 目标角度')).toHaveValue('7')
  await expect(robotPanel.getByRole('button', { name: '确认并发送预览' })).toBeDisabled()
  expect(commands).toHaveLength(0)
  await robotPanel.getByLabel('J1 目标角度').fill('5')

  await robotPanel.getByRole('button', { name: '立即锁定' }).click()
  await expect.poll(() => disarmToken).toBe('jaka-browser-token')
  await expect(robotPanel.getByRole('button', { name: '确认并发送预览' })).toBeDisabled()
  expect(commands).toHaveLength(0)

  await robotPanel.getByRole('button', { name: '解锁控制' }).click()
  const rearmDialog = page.getByRole('dialog', { name: '确认解锁机械臂控制' })
  await rearmDialog.getByText('工作区已清空', { exact: false }).click()
  await rearmDialog.getByText('现场独立急停可用', { exact: false }).click()
  await rearmDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(robotPanel.getByText(/已解锁，剩余/)).toBeVisible()

  previewBody = null
  nextPreviewLifetimeSeconds = -1
  await robotPanel.getByRole('button', { name: '生成动作预览' }).click()
  await expect.poll(() => previewBody).not.toBeNull()
  await expect(robotPanel.getByRole('button', { name: '确认并发送预览' })).toBeDisabled()
  expect(commands).toHaveLength(0)

  previewBody = null
  nextPreviewLifetimeSeconds = 20
  await robotPanel.getByRole('button', { name: '生成动作预览' }).click()
  await expect.poll(() => previewBody).not.toBeNull()
  await robotPanel.getByRole('button', { name: '确认并发送预览' }).click()
  const moveDialog = page.getByRole('dialog', { name: '确认发送关节运动' })
  await expect(moveDialog.getByText('网页不提供软件急停。')).toBeVisible()
  await moveDialog.getByText('已核对六轴目标', { exact: false }).click()
  await moveDialog.getByRole('button', { name: '确认继续' }).click()
  await expect.poll(() => commands.length).toBe(1)
  expect(commands[0]?.body).toEqual({ preview_id: 'joint-preview-1' })
  expect(commands[0]?.headers['x-robot-control-token']).toBe('jaka-browser-token')
  expect(commands[0]?.headers['idempotency-key']).toBeTruthy()
})

test('相机适配器刷新不会卸载或自动锁定常驻机械臂控制', async ({ page }) => {
  let currentStatus = robotStatus()
  let disarmToken: string | null = null

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [currentStatus] })
      return
    }
    if (path === '/api/robots/jaka-primary/status') {
      await fulfillJson(route, currentStatus)
      return
    }
    if (path === '/api/robots/jaka-primary/arm' && request.method() === 'POST') {
      currentStatus = { ...currentStatus, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, {
        token: 'jaka-browser-token',
        expires_at: currentStatus.armed_until,
        status: currentStatus,
      })
      return
    }
    if (path === '/api/robots/jaka-primary/arm' && request.method() === 'DELETE') {
      disarmToken = request.headers()['x-robot-control-token'] ?? null
      currentStatus = { ...currentStatus, armed_until: null }
      await route.fulfill({ status: 204 })
      return
    }
    await notFound(route)
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const robotPanel = page.getByTestId('robot-adapter')
  await robotPanel.getByRole('button', { name: '解锁控制' }).click()
  const armDialog = page.getByRole('dialog', { name: '确认解锁机械臂控制' })
  await armDialog.getByText('工作区已清空', { exact: false }).click()
  await armDialog.getByText('现场独立急停可用', { exact: false }).click()
  await armDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(robotPanel.getByText(/已解锁，剩余/)).toBeVisible()

  await page.getByTestId('camera-adapter').getByRole('button', { name: '重新连接' }).click()
  await expect(robotPanel.getByRole('heading', { name: '机械臂控制' })).toBeVisible()
  expect(disarmToken).toBeNull()
})

test('硬件适配器默认展开，折叠时保持控制组件挂载且不发出写请求', async ({ page }) => {
  const operations: Array<{ path: string; method: string }> = []
  const currentStatus = robotStatus()

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    operations.push({ path, method: request.method() })
    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [currentStatus] })
      return
    }
    if (path === '/api/robots/jaka-primary/status') {
      await fulfillJson(route, currentStatus)
      return
    }
    await notFound(route)
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const cameraSection = page.getByTestId('camera-adapter-section')
  const gripperSection = page.getByTestId('gripper-adapter')
  const robotSection = page.getByTestId('robot-adapter')
  const robotContent = page.getByTestId('hardware-adapter-content-robot')
  const robotPanel = robotSection.getByRole('region', { name: '机械臂控制' })

  await expect(cameraSection.getByRole('button', { name: '收起相机适配器' })).toHaveAttribute('aria-expanded', 'true')
  await expect(gripperSection.getByRole('button', { name: '收起夹爪适配器' })).toHaveAttribute('aria-expanded', 'true')
  await expect(robotSection.getByRole('button', { name: '收起机械臂适配器' })).toHaveAttribute('aria-expanded', 'true')
  await expect(robotPanel).toBeVisible()

  await robotPanel.getByLabel('J1 目标角度').fill('5')
  await robotSection.getByRole('button', { name: '收起机械臂适配器' }).click()
  await expect(robotSection.getByRole('button', { name: '展开机械臂适配器' })).toHaveAttribute('aria-expanded', 'false')
  await expect(robotContent).toBeAttached()
  await expect(robotContent).not.toBeVisible()
  expect(operations.filter((operation) => operation.method !== 'GET')).toEqual([])

  await robotSection.getByRole('button', { name: '展开机械臂适配器' }).click()
  await expect(robotPanel).toBeVisible()
  await expect(robotPanel.getByLabel('J1 目标角度')).toHaveValue('5')

  await cameraSection.getByRole('button', { name: '收起相机适配器' }).click()
  await expect(page.getByTestId('camera-adapter')).not.toBeVisible()
  await gripperSection.getByRole('button', { name: '收起夹爪适配器' }).click()
  await expect(page.getByTestId('hardware-adapter-content-gripper')).not.toBeVisible()
  expect(operations.filter((operation) => operation.method !== 'GET')).toEqual([])
})

test('急停状态会立即撤销浏览器令牌，即使服务器时间窗尚未到期', async ({ page }) => {
  let currentStatus = robotStatus()

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [currentStatus] })
      return
    }
    if (path === '/api/robots/jaka-primary/status') {
      await fulfillJson(route, currentStatus)
      return
    }
    if (path === '/api/robots/jaka-primary/arm' && request.method() === 'POST') {
      currentStatus = { ...currentStatus, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, {
        token: 'jaka-browser-token',
        expires_at: currentStatus.armed_until,
        status: currentStatus,
      })
      return
    }
    await notFound(route)
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const robotPanel = page.getByTestId('robot-adapter')
  await robotPanel.getByRole('button', { name: '解锁控制' }).click()
  const armDialog = page.getByRole('dialog', { name: '确认解锁机械臂控制' })
  await armDialog.getByText('工作区已清空', { exact: false }).click()
  await armDialog.getByText('现场独立急停可用', { exact: false }).click()
  await armDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(robotPanel.getByText(/已解锁，剩余/)).toBeVisible()

  currentStatus = { ...currentStatus, emergency_stopped: true }
  await expect(robotPanel.getByText('急停状态')).toBeVisible({ timeout: 3_000 })
  await expect(robotPanel.getByText('当前已锁定')).toBeVisible()
  await expect(robotPanel.getByRole('button', { name: '解锁控制' })).toBeVisible()
  await expect(robotPanel.getByRole('button', { name: '立即锁定' })).toHaveCount(0)
})

function robotStatus(overrides: Record<string, unknown> = {}) {
  return {
    robot_id: 'jaka-primary',
    mode: 'physical',
    controls_enabled: true,
    manual_motion_enabled: true,
    enable_permitted: true,
    connected: true,
    powered: true,
    enabled: false,
    moving: false,
    faulted: false,
    emergency_stopped: false,
    captured_at: 1_000,
    joint_positions_rad: [0, 0, 0, 0, 0, 0],
    joint_lower_limits_rad: [-1, -1, -1, -1, -1, -1],
    joint_upper_limits_rad: [1, 1, 1, 1, 1, 1],
    maximum_joint_speed_rad_per_second: 0.5,
    maximum_joint_step_rad: Math.PI / 18,
    armed_until: null,
    last_error: null,
    ...overrides,
  }
}

function operationResult(request: { headers(): Record<string, string> }, status: Record<string, unknown>) {
  return {
    idempotency_key: request.headers()['idempotency-key'],
    replayed: false,
    status,
  }
}

const transparentGif = Buffer.from('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', 'base64')

async function fulfillCameraApi(route: Route, path: string): Promise<boolean> {
  if (path === '/api/plugins') {
    await fulfillJson(route, {
      plugins: [{
        plugin_id: 'visual-pose-analysis', name: '视觉与姿态分析', version: '1.0.0',
        capabilities: ['frame_consumer', 'pose_tracking'], ui_kind: 'visual-pose-analysis',
        state: 'running', error: null, reloadable: true,
      }],
    })
    return true
  }
  if (path === '/api/cameras') {
    await fulfillJson(route, {
      cameras: [{ camera_id: 'sim-camera', state: 'streaming', latest_frame_at: 1_000, error: null }],
      devices: [{
        device_id: 'sim-device', display_name: '模拟相机', model_name: 'SIM-1000',
        transport: 'simulation', selected: true, calibrated: true,
      }],
      selected_device_id: 'sim-device',
      selection_enabled: true,
      discovery_error: null,
    })
    return true
  }
  if (path === '/api/cameras/sim-camera/status') {
    await fulfillJson(route, { camera_id: 'sim-camera', state: 'streaming', latest_frame_at: 1_000, error: null })
    return true
  }
  if (path === '/api/cameras/sim-camera/stream') {
    await route.fulfill({ contentType: 'image/gif', body: transparentGif })
    return true
  }
  if (path === '/api/cameras/sim-camera/pose') {
    await fulfillJson(route, {
      camera_id: 'sim-camera',
      enabled: false,
      captured_at: null,
      latest_frame_at: 1_000,
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
    })
    return true
  }
  if (path === '/api/cameras/sim-camera/vision/analysis') {
    await fulfillJson(route, {
      camera_id: 'sim-camera',
      frame_captured_at: 1_000,
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
    })
    return true
  }
  if (path === '/api/cameras/sim-camera/parameters') {
    await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
    return true
  }
  return false
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) })
}

async function notFound(route: Route): Promise<void> {
  await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
}
