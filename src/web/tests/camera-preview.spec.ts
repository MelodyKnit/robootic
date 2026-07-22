import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type APIRequestContext } from '@playwright/test'

test('显示可解码的实时相机画面', async ({ page, request }) => {
  const cameraId = await configuredCameraId(request)
  const initialExposure = await parameterValue(request, cameraId, 'exposure_time_us')

  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('heading', { name: '相机预览' })).toBeVisible()
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

  await expect.poll(() => parameterValue(request, cameraId, 'exposure_time_us')).toBe(initialExposure)

  const screenshotDirectory = join('..', '..', 'temp', 'gripper-ai-controller')
  mkdirSync(screenshotDirectory, { recursive: true })
  await page.screenshot({ path: join(screenshotDirectory, 'camera-preview-browser.png'), fullPage: true })
})

async function configuredCameraId(request: APIRequestContext): Promise<string> {
  const response = await request.get('/api/cameras')
  expect(response.ok()).toBeTruthy()
  const payload = (await response.json()) as { cameras?: Array<{ camera_id?: unknown }> }
  const cameraId = payload.cameras?.[0]?.camera_id
  expect(typeof cameraId).toBe('string')
  return cameraId as string
}

async function parameterValue(request: APIRequestContext, cameraId: string, key: string): Promise<unknown> {
  const response = await request.get(`/api/cameras/${encodeURIComponent(cameraId)}/parameters`)
  expect(response.ok()).toBeTruthy()
  const payload = (await response.json()) as {
    parameters?: Array<{ key?: unknown; value?: unknown }>
  }
  const parameter = payload.parameters?.find((candidate) => candidate.key === key)
  expect(parameter).toBeDefined()
  return parameter?.value
}
