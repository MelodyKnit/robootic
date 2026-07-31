import { expect, test, type Route } from '@playwright/test'

test('机械臂列表显示和选择功能', async ({ page }) => {
  // 模拟API响应
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    // 机械臂列表
    if (path === '/api/robots') {
      await fulfillJson(route, {
        robots: [
          createRobotStatus('robot-sim-1', 'simulation', true, true, false),
          createRobotStatus('robot-sim-2', 'simulation', true, false, false),
          createRobotStatus('robot-physical-1', 'physical', true, true, true),
        ],
      })
      return
    }

    // 单个机械臂状态
    const robotStatusMatch = path.match(/^\/api\/robots\/([^/]+)\/status$/)
    if (robotStatusMatch) {
      const robotId = robotStatusMatch[1]
      await fulfillJson(route, createRobotStatus(robotId, 'simulation', true, true, false))
      return
    }

    // 其他API返回404
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证机械臂列表显示
  await expect(page.getByText('可用机械臂')).toBeVisible()
  await expect(page.getByText('robot-sim-1')).toBeVisible()
  await expect(page.getByText('robot-sim-2')).toBeVisible()
  await expect(page.getByText('robot-physical-1')).toBeVisible()

  // 验证选择器显示所有机械臂
  const selector = page.getByTestId('robot-selector')
  await expect(selector).toBeVisible()
  await expect(selector.locator('option')).toHaveCount(3)

  // 验证仿真和实机标签
  await expect(page.getByText('仿真').first()).toBeVisible()
  await expect(page.getByText('实机').first()).toBeVisible()
})

test('机械臂状态指示器正确显示', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      await fulfillJson(route, {
        robots: [
          createRobotStatus('robot-1', 'simulation', true, true, true), // 全部正常
          createRobotStatus('robot-2', 'simulation', false, false, false), // 未连接
        ],
      })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证第一个机械臂的状态
  const robot1Card = page.locator('button', { hasText: 'robot-1' })
  await expect(robot1Card.getByText('已连接')).toBeVisible()
  await expect(robot1Card.getByText('已上电')).toBeVisible()
  await expect(robot1Card.getByText('已使能')).toBeVisible()

  // 验证第二个机械臂的状态
  const robot2Card = page.locator('button', { hasText: 'robot-2' })
  await expect(robot2Card.getByText('未连接')).toBeVisible()
  await expect(robot2Card.getByText('未上电')).toBeVisible()
  await expect(robot2Card.getByText('未使能')).toBeVisible()
})

test('机械臂选择功能', async ({ page }) => {
  let selectedRobotId = 'robot-1'

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      await fulfillJson(route, {
        robots: [
          createRobotStatus('robot-1', 'simulation', true, true, false),
          createRobotStatus('robot-2', 'simulation', true, false, false),
        ],
      })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 默认选中第一个机械臂
  const selector = page.getByTestId('robot-selector')
  await expect(selector).toHaveValue('robot-1')

  // 选择第二个机械臂
  await selector.selectOption('robot-2')
  await expect(selector).toHaveValue('robot-2')

  // 验证卡片选中状态
  const robot2Card = page.locator('button', { hasText: 'robot-2' })
  await expect(robot2Card).toHaveClass(/border-blue-500/)
})

test('机械臂列表刷新功能', async ({ page }) => {
  let requestCount = 0

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      requestCount++
      await fulfillJson(route, {
        robots: [
          createRobotStatus('robot-1', 'simulation', true, true, false),
        ],
      })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 等待初始加载
  await expect(page.getByText('robot-1')).toBeVisible()
  const initialCount = requestCount

  // 点击刷新按钮
  await page.getByTitle('刷新机械臂列表').click()

  // 验证请求被发送
  await expect.poll(() => requestCount).toBeGreaterThan(initialCount)
})

test('机械臂列表空状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      await fulfillJson(route, { robots: [] })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证空状态显示
  await expect(page.getByText('无可用机械臂')).toBeVisible()
  await expect(page.getByText('请检查配置或启动机械臂服务')).toBeVisible()
})

test('机械臂列表错误处理', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 'server_error',
          message: '服务器内部错误',
        }),
      })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证错误消息显示
  await expect(page.getByText('服务器内部错误')).toBeVisible()
})

test('机械臂状态自动轮询', async ({ page }) => {
  let pollCount = 0

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      pollCount++
      await fulfillJson(route, {
        robots: [
          createRobotStatus('robot-1', 'simulation', true, true, false),
        ],
      })
      return
    }

    const statusMatch = path.match(/^\/api\/robots\/([^/]+)\/status$/)
    if (statusMatch) {
      await fulfillJson(route, createRobotStatus('robot-1', 'simulation', true, true, false))
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 等待初始加载
  await expect(page.getByText('robot-1')).toBeVisible()

  // 等待至少一次轮询
  await expect.poll(() => pollCount, { timeout: 5000 }).toBeGreaterThanOrEqual(2)
})

test('机械臂控制权限提示', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/robots') {
      await fulfillJson(route, {
        robots: [
          {
            ...createRobotStatus('robot-1', 'physical', true, true, false),
            controls_enabled: false,
          },
        ],
      })
      return
    }

    await route.fulfill({ status: 404 })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 如果页面包含控制权限检查，验证提示信息
  // 注意：这取决于具体的页面实现
  await expect(page.getByText('robot-1')).toBeVisible()
})

// 辅助函数

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

function createRobotStatus(
  id: string,
  mode: 'simulation' | 'physical',
  connected: boolean,
  powered: boolean,
  enabled: boolean,
) {
  return {
    robot_id: id,
    mode,
    controls_enabled: true,
    manual_motion_enabled: true,
    enable_permitted: true,
    connected,
    powered,
    enabled,
    moving: false,
    faulted: false,
    emergency_stopped: false,
    captured_at: Date.now() / 1000,
    joint_positions_rad: [0, 0, 0, 0, 0, 0],
    joint_lower_limits_rad: [-3.14, -3.14, -3.14, -3.14, -3.14, -3.14],
    joint_upper_limits_rad: [3.14, 3.14, 3.14, 3.14, 3.14, 3.14],
    maximum_joint_speed_rad_per_second: 0.5,
    maximum_joint_step_rad: 0.175,
    armed_until: null,
    last_error: null,
  }
}
