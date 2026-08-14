import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:5174',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
  webServer: [
    // P2 fix (2026-08-14): 同时起后端(9079) + vite(5174)，使 e2e 可真实联调。
    // vite 已配置 /api → localhost:9079 代理，前端调用可直接打到后端。
    {
      command: 'MAOP_AUTH_ENABLED=0 MAOP_ENV=test python ../py/start_dashboard.py',
      port: 9079,
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: 'npm run dev',
      port: 5174,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
})