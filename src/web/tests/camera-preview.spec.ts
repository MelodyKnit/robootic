import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test } from '@playwright/test'

test('显示可解码的实时相机画面', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('heading', { name: '相机预览' })).toBeVisible()

  const streamImage = page.getByTestId('camera-stream')
  await expect(streamImage).toBeVisible({ timeout: 15_000 })
  await expect
    .poll(async () => streamImage.evaluate((image: HTMLImageElement) => image.naturalWidth), {
      timeout: 15_000,
    })
    .toBeGreaterThan(0)

  const screenshotDirectory = join('..', '..', 'temp', 'gripper-ai-controller')
  mkdirSync(screenshotDirectory, { recursive: true })
  await page.screenshot({ path: join(screenshotDirectory, 'camera-preview-simulation.png'), fullPage: true })
})
