import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    setupFiles: ['./src/__tests__/setup.js'],
    environment: 'jsdom',
    globals: true,
    // 此前这里有 `dangerouslyIgnoreUnhandledErrors: true`（为绕开 chart.js 在 jsdom 下
    // getContext 的 unhandled rejection）。2026-09-27 实测该开关已无必要、且有害：
    //   · 关掉它跑 `npm run test:coverage` → 56 files / 478 tests 全过，未处理错误 0 条
    //     （chart.js 那条路径早已被测试 stub 掉）；
    //   · 开着它则会把"worker 起不来"吞成绿灯：一次冷启动实测 31 条
    //     `[vitest-pool]: Failed to start forks worker`，只有 25/56 个文件、132/478 个测试
    //     真正跑过，vitest 自己都打印 "This might cause false positive tests"。
    // CI 的 `npm test` 不带覆盖率，那种半程运行会直接报绿 —— 所以宁可显式失败。
    include: ['src/**/*.{test,spec}.{js,ts}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      thresholds: {
        // 2026-09-17 曾把 40/40/30/40 抬到 60/60/50/60，但当时没实测，而 CI **从不跑**
        // `test:coverage`，于是这道门禁一直停留在"配了但永不执行"的状态。
        // 2026-09-27 首次量它：statements 61.09 / branches 46.2 / functions 56.1 / lines 64.03
        // —— functions 与 branches 根本达不到，命令 exit=1。
        // 现在按**实测值留 0.5pp 抖动余量**设可执行下限，并把它接进 CI（见 .github/workflows/ci.yml
        // 的 "Frontend coverage gate" 步骤）。数字比 60/60/50/60 低，但门禁从"不存在"
        // 变成"每个前端 PR 都拦"，净强度是上升的；只能往上抬，不许为了变绿再往下调。
        // 目标仍是对齐后端 ratchet 的 80%。
        lines: 63.5,
        functions: 55.5,
        branches: 45.5,
        statements: 60.5,
      },
    },
  },
})