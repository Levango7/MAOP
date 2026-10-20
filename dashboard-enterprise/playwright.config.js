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
      // env 走 webServer.env 而不是 POSIX 内联写法：Windows 的 cmd.exe 把
      // `MAOP_AUTH_ENABLED=0 python …` 当成命令名，整条 e2e 在 Windows 上
      // 起不来（本机实测 "'MAOP_AUTH_ENABLED' is not recognized"）。
      // 显式带 process.env 打底，避免覆盖掉 PATH 等基础变量。
      //
      // MAOP_MULTIMODAL_PROVIDER=hash：否则后端启动会去 huggingface 拉 CLIP
      // 权重（本机实测因此花 87 秒，且网络被重置时靠 5 次重试耗满超时）。
      // e2e 不测多模态嵌入，哈希回退是确定性且足够快的选择——与
      // py/scripts/first_run_smoke.py 里同一开关的理由一致。
      // timeout 60s→180s：给慢机器留余量，CI 上不改变行为。
      command: 'python ../py/start_dashboard.py',
      env: {
        ...process.env,
        MAOP_AUTH_ENABLED: '0',
        MAOP_ENV: 'test',
        MAOP_MULTIMODAL_PROVIDER: 'hash',
      },
      port: 9079,
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command: 'npm run dev',
      port: 5174,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
})