import { defineConfig, devices } from '@playwright/test'

const externalBaseUrl = process.env.CAMERA_PREVIEW_BASE_URL
const localBaseUrl = 'http://127.0.0.1:5174'

/**
 * Browser tests use a local Vite server and mock every API response. An
 * explicit external URL remains available for manual browser diagnostics.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: externalBaseUrl || localBaseUrl,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174 --strictPort',
        cwd: process.cwd(),
        url: localBaseUrl,
        timeout: 30_000,
        reuseExistingServer: !process.env.CI,
      },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
