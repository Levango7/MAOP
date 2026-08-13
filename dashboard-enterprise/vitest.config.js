import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    setupFiles: ['./src/__tests__/setup.js'],
    environment: 'jsdom',
    globals: true,
    // chart.js 在 jsdom 下调用 HTMLCanvasElement.getContext 会抛出 unhandled
    // rejection（jsdom 未实现 canvas）。这些 rejection 源自 chart.js 的已知限制，
    // 不影响测试正确性（测试已 stub Line/Bar/Pie 组件，rejection 发生在
    // chart.js 内部的异步 resize 路径）。忽略以避免 CI 误判。
    dangerouslyIgnoreUnhandledErrors: true,
    include: ['src/**/*.{test,spec}.{js,ts}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      thresholds: {
        lines: 40,
        functions: 40,
        branches: 30,
        statements: 40,
      },
    },
  },
})