import { defineConfig } from '@playwright/test'

// T2.4 (2026-10-03): 全局关闭 CoachMarks 新手引导。
// 该引导的 .coach-marks__scrim 全屏遮罩会拦截任何点击——layout.spec.js 与
// knowledge-graph.spec.js 先后被它拦红（"等待元素可点"永远等不到，30s 超时）。
// 在配置层给所有 project 注入 localStorage 幂等标记（CoachMarks.vue:60
// STORAGE_KEY='maop_onboarding_done'，=1 即永不显示），比每个 spec 各自
// beforeEach 更可靠——新 spec 漏加就会重犯。
const coachMarksDone = {
  cookies: [],
  origins: [
    {
      origin: 'http://localhost:5174',
      localStorage: [{ name: 'maop_onboarding_done', value: '1' }],
    },
  ],
}

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
      use: { browserName: 'chromium', storageState: coachMarksDone },
    },
    // P1 fix (2026-09-17): added Firefox + WebKit for cross-browser coverage.
    // CI runs all 3; local dev can filter with --project=chromium.
    {
      name: 'firefox',
      use: { browserName: 'firefox', storageState: coachMarksDone },
    },
    {
      name: 'webkit',
      use: { browserName: 'webkit', storageState: coachMarksDone },
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