import { expect, test, type Route } from '@playwright/test'

test('模拟夹爪仅在确认解锁和明确提交后执行动作', async ({ page }) => {
  let currentStatus = gripperStatus({ initialized: false })
  let armRequestBody: unknown = null
  let disarmToken: string | null = null
  const commands: Array<{ body: unknown; armToken: string | null; idempotencyKey: string | null }> = []

  await page.route('**://*/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/grippers') {
      await fulfillJson(route, { grippers: [currentStatus] })
      return
    }
    if (path === '/api/grippers/sim-gripper/status') {
      await fulfillJson(route, currentStatus)
      return
    }
    if (path === '/api/grippers/sim-gripper/arm' && request.method() === 'POST') {
      armRequestBody = request.postDataJSON()
      currentStatus = { ...currentStatus, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, {
        token: 'browser-arm-token',
        expires_at: currentStatus.armed_until,
        status: currentStatus,
      })
      return
    }
    if (path === '/api/grippers/sim-gripper/arm' && request.method() === 'DELETE') {
      disarmToken = request.headers()['x-gripper-control-token'] ?? null
      currentStatus = { ...currentStatus, armed_until: null }
      await route.fulfill({ status: 204 })
      return
    }
    if (path === '/api/grippers/sim-gripper/initialization') {
      currentStatus = { ...currentStatus, initialized: true, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, { idempotency_key: request.headers()['idempotency-key'], replayed: false, status: currentStatus })
      return
    }
    if (path === '/api/grippers/sim-gripper/commands') {
      commands.push({
        body: request.postDataJSON(),
        armToken: request.headers()['x-gripper-control-token'] ?? null,
        idempotencyKey: request.headers()['idempotency-key'] ?? null,
      })
      currentStatus = { ...currentStatus, target_position: 650, armed_until: Date.now() / 1_000 + 60 }
      await fulfillJson(route, { idempotency_key: request.headers()['idempotency-key'], replayed: false, status: currentStatus })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const gripperPanel = page.getByTestId('gripper-adapter')
  await expect(gripperPanel.getByRole('heading', { name: '夹爪控制' })).toBeVisible()

  await gripperPanel.getByRole('button', { name: '解锁控制' }).click()
  const armDialog = page.getByRole('dialog', { name: '确认解锁夹爪控制' })
  await armDialog.getByText('工作区已清空', { exact: false }).click()
  await armDialog.getByText('现场独立急停可用', { exact: false }).click()
  await armDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(gripperPanel.getByText(/已解锁，剩余/)).toBeVisible()
  expect(armRequestBody).toEqual({ work_area_clear: true, emergency_stop_ready: true })

  await gripperPanel.getByRole('button', { name: '初始化', exact: true }).click()
  const initializationDialog = page.getByRole('dialog', { name: '确认执行夹爪初始化' })
  await initializationDialog.getByText('已再次检查夹爪行程', { exact: false }).click()
  await initializationDialog.getByRole('button', { name: '确认继续' }).click()
  await expect(gripperPanel.getByText('已完成', { exact: true })).toBeVisible()

  await gripperPanel.getByLabel('目标位置数值').fill('650')
  await gripperPanel.getByLabel('目标力数值').fill('25')
  await gripperPanel.getByLabel('速度数值').fill('10')
  expect(commands).toHaveLength(0)

  await gripperPanel.getByRole('button', { name: '执行目标位置' }).click()
  await expect.poll(() => commands.length).toBe(1)
  expect(commands[0]?.body).toEqual({
    action: 'move_to_position',
    target_position: 650,
    force_percent: 25,
    speed_percent: 10,
  })
  expect(commands[0]?.armToken).toBe('browser-arm-token')
  expect(commands[0]?.idempotencyKey).toBeTruthy()

  await gripperPanel.getByRole('button', { name: '立即锁定' }).click()
  await expect.poll(() => disarmToken).toBe('browser-arm-token')
  await expect(page.getByText('当前已锁定')).toBeVisible()
})

test('真机模式禁用未验证的速度和软件停止', async ({ page }) => {
  const physicalStatus = gripperStatus({
    gripper_id: 'pgi-tcp',
    mode: 'physical',
    initialized: true,
    supports_speed: false,
    supports_stop: false,
    position_is_feedback: false,
  })
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/grippers') {
      await fulfillJson(route, { grippers: [physicalStatus] })
      return
    }
    if (path === '/api/grippers/pgi-tcp/status') {
      await fulfillJson(route, physicalStatus)
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const gripperPanel = page.getByTestId('gripper-adapter')

  await expect(gripperPanel.getByText('真机 TCP')).toBeVisible()
  await expect(gripperPanel.getByText('已设定目标位置')).toBeVisible()
  await expect(gripperPanel.getByText('实时位置反馈未验证')).toBeVisible()
  await expect(gripperPanel.getByRole('slider', { name: '速度' })).toBeDisabled()
  await expect(gripperPanel.getByRole('button', { name: '停止仿真动作' })).toHaveCount(0)
  await expect(gripperPanel.getByText(/真机无软件停止接口/)).toBeVisible()
})

test('夹爪运动中禁用解锁与初始化入口', async ({ page }) => {
  const movingStatus = gripperStatus({
    initialized: false,
    moving: true,
    grip_state: 'moving',
  })
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/grippers') {
      await fulfillJson(route, { grippers: [movingStatus] })
      return
    }
    if (path === '/api/grippers/sim-gripper/status') {
      await fulfillJson(route, movingStatus)
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const gripperPanel = page.getByTestId('gripper-adapter')

  await expect(gripperPanel.getByText('正在运动', { exact: true })).toBeVisible()
  await expect(gripperPanel.getByRole('button', { name: '解锁控制' })).toBeDisabled()
  await expect(gripperPanel.getByRole('button', { name: '初始化', exact: true })).toBeDisabled()
})

test('旧版预览服务缺少夹爪接口时提示受控重启', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/grippers') {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: '{"detail":"Not Found"}',
      })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const gripperPanel = page.getByTestId('gripper-adapter')

  await expect(gripperPanel.getByText('夹爪控制接口不可用，请重启服务。')).toBeVisible()
  await expect(gripperPanel.getByText('未配置夹爪')).toHaveCount(0)
})

test('当前服务成功返回空夹爪列表时显示未配置状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (await fulfillCameraApi(route, path)) {
      return
    }
    if (path === '/api/grippers') {
      await fulfillJson(route, { grippers: [] })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })
  const gripperPanel = page.getByTestId('gripper-adapter')

  await expect(gripperPanel.getByText('未配置夹爪')).toBeVisible()
  await expect(gripperPanel.getByRole('alert')).toHaveCount(0)
})

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
      camera_id: 'sim-camera', enabled: false, captured_at: null, latest_frame_at: 1_000,
      overlay_fresh: false, valid: false, reason: 'disabled', target_joint: 'right_wrist',
      target: null, person: null, inference_latency_ms: null, lost_frames: 0, motion: null, draw_skeleton: false,
    })
    return true
  }
  if (path === '/api/cameras/sim-camera/vision/analysis') {
    await fulfillJson(route, {
      camera_id: 'sim-camera', frame_captured_at: 1_000, inference_captured_at: null,
      pose_enabled: false, valid: false, reason: 'disabled', frame: null, person_count: 0,
      persons: [], selected_person: null, joint_visibility: [], visible_joint_names: [],
    })
    return true
  }
  if (path === '/api/cameras/sim-camera/parameters') {
    await fulfillJson(route, { camera_id: 'sim-camera', write_enabled: false, parameters: [] })
    return true
  }
  return false
}

function gripperStatus(overrides: Record<string, unknown> = {}) {
  return {
    gripper_id: 'sim-gripper',
    mode: 'simulation',
    controls_enabled: true,
    connected: true,
    initialized: false,
    initializing: false,
    moving: false,
    gripping: false,
    target_position: 500,
    position_is_feedback: true,
    grip_state: 'idle',
    supports_speed: true,
    supports_stop: true,
    minimum_position: 0,
    maximum_position: 1000,
    minimum_force_percent: 20,
    maximum_force_percent: 40,
    minimum_speed_percent: 1,
    maximum_speed_percent: 30,
    open_position: 900,
    close_position: 100,
    armed_until: null,
    last_error: null,
    ...overrides,
  }
}

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) })
}
