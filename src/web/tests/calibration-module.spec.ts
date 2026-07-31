import { expect, test, type Route } from '@playwright/test'

test('标定模块卡片显示正确的初始状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload())
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证标定模块卡片存在
  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(moduleCard).toBeVisible()

  // 验证标题和描述
  await expect(moduleCard.getByText('手眼标定')).toBeVisible()
  await expect(moduleCard.getByText('标定机械臂工具端相机与基座的空间关系')).toBeVisible()

  // 验证状态显示为"未开始"
  await expect(moduleCard.getByText('未开始')).toBeVisible()

  // 验证开始按钮可见且可用
  const startButton = moduleCard.getByRole('button', { name: '开始标定' })
  await expect(startButton).toBeVisible()
  await expect(startButton).toBeEnabled()

  // 验证暂停和停止按钮不可见
  await expect(moduleCard.getByRole('button', { name: '暂停' })).not.toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '停止' })).not.toBeVisible()
})

test('标定模块卡片显示运行中状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('running', 3, 10))
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(moduleCard).toBeVisible()

  // 验证状态显示
  await expect(moduleCard.getByText('运行中')).toBeVisible()

  // 验证进度显示
  await expect(moduleCard.getByText('3 / 10')).toBeVisible()

  // 验证暂停和停止按钮可见
  await expect(moduleCard.getByRole('button', { name: '暂停' })).toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '停止' })).toBeVisible()

  // 验证开始按钮不可见
  await expect(moduleCard.getByRole('button', { name: '开始标定' })).not.toBeVisible()
})

test('标定模块卡片显示暂停状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('paused', 5, 10))
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(moduleCard).toBeVisible()

  // 验证状态显示
  await expect(moduleCard.getByText('已暂停')).toBeVisible()

  // 验证继续和停止按钮可见
  await expect(moduleCard.getByRole('button', { name: '继续' })).toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '停止' })).toBeVisible()

  // 验证开始和暂停按钮不可见
  await expect(moduleCard.getByRole('button', { name: '开始标定' })).not.toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '暂停' })).not.toBeVisible()
})

test('标定模块卡片显示完成状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('completed', 10, 10))
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(moduleCard).toBeVisible()

  // 验证状态显示
  await expect(moduleCard.getByText('已完成')).toBeVisible()

  // 验证进度显示
  await expect(moduleCard.getByText('10 / 10')).toBeVisible()

  // 验证只有重新开始按钮可见
  await expect(moduleCard.getByRole('button', { name: '重新开始' })).toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '暂停' })).not.toBeVisible()
  await expect(moduleCard.getByRole('button', { name: '停止' })).not.toBeVisible()
})

test('标定模块卡片显示错误状态', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('error', 2, 10, '相机连接失败'))
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(moduleCard).toBeVisible()

  // 验证错误状态显示
  await expect(moduleCard.getByText('错误')).toBeVisible()

  // 验证错误消息显示
  await expect(moduleCard.getByText('相机连接失败')).toBeVisible()

  // 验证重试按钮可见
  await expect(moduleCard.getByRole('button', { name: '重试' })).toBeVisible()
})

test('点击开始按钮启动标定', async ({ page }) => {
  const startRequests: string[] = []

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()

    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload())
      return
    }
    if (path === '/api/calibration/modules/hand-eye/start' && method === 'POST') {
      startRequests.push(path)
      await fulfillJson(route, { status: 'success' })
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  const startButton = moduleCard.getByRole('button', { name: '开始标定' })

  await startButton.click()

  await expect.poll(() => startRequests.length).toBe(1)
})

test('点击暂停按钮暂停标定', async ({ page }) => {
  const pauseRequests: string[] = []

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()

    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('running', 3, 10))
      return
    }
    if (path === '/api/calibration/modules/hand-eye/pause' && method === 'POST') {
      pauseRequests.push(path)
      await fulfillJson(route, { status: 'success' })
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  const pauseButton = moduleCard.getByRole('button', { name: '暂停' })

  await pauseButton.click()

  await expect.poll(() => pauseRequests.length).toBe(1)
})

test('点击停止按钮停止标定', async ({ page }) => {
  const stopRequests: string[] = []

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()

    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('running', 3, 10))
      return
    }
    if (path === '/api/calibration/modules/hand-eye/stop' && method === 'POST') {
      stopRequests.push(path)
      await fulfillJson(route, { status: 'success' })
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  const stopButton = moduleCard.getByRole('button', { name: '停止' })

  await stopButton.click()

  await expect.poll(() => stopRequests.length).toBe(1)
})

test('状态轮询机制正常工作', async ({ page }) => {
  let pollCount = 0

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/calibration/modules') {
      pollCount += 1
      // 第一次返回未开始，第二次返回运行中
      const payload = pollCount === 1
        ? calibrationModulesPayload()
        : calibrationModulesPayload('running', 1, 10)
      await fulfillJson(route, payload)
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 等待至少两次轮询
  await expect.poll(() => pollCount, { timeout: 10_000 }).toBeGreaterThanOrEqual(2)
})

test('WebSocket连接失败时回退到轮询', async ({ page }) => {
  let pollCount = 0

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/calibration/modules') {
      pollCount += 1
      await fulfillJson(route, calibrationModulesPayload())
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    // WebSocket路径返回404
    if (path.includes('/ws/')) {
      await route.fulfill({ status: 404 })
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证即使WebSocket失败，轮询仍然工作
  await expect.poll(() => pollCount, { timeout: 10_000 }).toBeGreaterThanOrEqual(2)
})

test('多个标定模块正确显示', async ({ page }) => {
  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    if (path === '/api/calibration/modules') {
      await fulfillJson(route, {
        modules: [
          {
            module_id: 'hand-eye',
            name: '手眼标定',
            description: '标定机械臂工具端相机与基座的空间关系',
            state: 'idle',
            progress: { current: 0, total: 10 },
            error: null,
          },
          {
            module_id: 'camera-intrinsic',
            name: '相机内参标定',
            description: '标定相机的内部参数',
            state: 'running',
            progress: { current: 5, total: 20 },
            error: null,
          },
        ],
      })
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  // 验证两个模块都存在
  await expect(page.getByTestId('calibration-module-card-hand-eye')).toBeVisible()
  await expect(page.getByTestId('calibration-module-card-camera-intrinsic')).toBeVisible()

  // 验证第一个模块的状态
  const handEyeCard = page.getByTestId('calibration-module-card-hand-eye')
  await expect(handEyeCard.getByText('未开始')).toBeVisible()

  // 验证第二个模块的状态
  const intrinsicCard = page.getByTestId('calibration-module-card-camera-intrinsic')
  await expect(intrinsicCard.getByText('运行中')).toBeVisible()
  await expect(intrinsicCard.getByText('5 / 20')).toBeVisible()
})

test('错误状态下点击重试按钮', async ({ page }) => {
  const retryRequests: string[] = []

  await page.route('**://*/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()

    if (path === '/api/calibration/modules') {
      await fulfillJson(route, calibrationModulesPayload('error', 0, 10, '相机连接失败'))
      return
    }
    if (path === '/api/calibration/modules/hand-eye/start' && method === 'POST') {
      retryRequests.push(path)
      await fulfillJson(route, { status: 'success' })
      return
    }
    if (await fulfillCameraApi(route, path)) {
      return
    }
    await route.fulfill({ status: 404, contentType: 'application/json', body: '{"code":"not_found"}' })
  })

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  const moduleCard = page.getByTestId('calibration-module-card-hand-eye')
  const retryButton = moduleCard.getByRole('button', { name: '重试' })

  await retryButton.click()

  await expect.poll(() => retryRequests.length).toBe(1)
})

async function fulfillJson(route: Route, payload: unknown): Promise<void> {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(payload),
  })
}

async function fulfillCameraApi(route: Route, path: string): Promise<boolean> {
  if (path === '/api/cameras') {
    await fulfillJson(route, {
      cameras: [],
      devices: [],
      selected_device_id: null,
      selection_enabled: false,
      discovery_error: null,
    })
    return true
  }
  if (path === '/api/plugins') {
    await fulfillJson(route, { plugins: [] })
    return true
  }
  return false
}

function calibrationModulesPayload(
  state: 'idle' | 'running' | 'paused' | 'completed' | 'error' = 'idle',
  current = 0,
  total = 10,
  errorMessage: string | null = null,
) {
  return {
    modules: [
      {
        module_id: 'hand-eye',
        name: '手眼标定',
        description: '标定机械臂工具端相机与基座的空间关系',
        state,
        progress: { current, total },
        error: errorMessage,
      },
    ],
  }
}
