import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

// t22: 计算 ESM 模块下的 __dirname，读取 package.json 的 version 字段，
// 通过 vite define 注入为全局常量 __APP_VERSION__，供前端展示真实版本号，
// 避免在源码中硬编码版本号导致前后端版本不一致。
const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(resolve(__dirname, './package.json'), 'utf-8'));

export default defineConfig({
  root: '.',
  base: '/',
  plugins: [vue()],
  build: {
    outDir: '../dashboard/dist-enterprise',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // P1-4: vendor 分包优化，将第三方依赖按职责拆分为独立 chunk，
        // 减小入口 chunk 体积并提升缓存命中率（vendor chunk 命中后无需重复下载）。
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          // Vue 全家桶：核心框架 + 路由 + 状态管理
          if (/[\\/]node_modules[\\/](vue|vue-router|pinia)[\\/]/.test(id)) {
            return 'vendor-vue';
          }
          // 可视化：vis-network + vis-data（知识图谱、MCP 拓扑共用）
          if (/[\\/]node_modules[\\/](vis-network|vis-data)[\\/]/.test(id)) {
            return 'vendor-vis';
          }
          // 图表：chart.js + vue-chartjs
          if (/[\\/]node_modules[\\/](chart\.js|vue-chartjs)[\\/]/.test(id)) {
            return 'vendor-chart';
          }
          // 其余第三方依赖统一归入 vendor
          return 'vendor';
        },
      },
    },
    chunkSizeWarningLimit: 800,
  },
  server: {
    port: 5174,
    // 允许 import.meta.glob 访问项目根目录外的 docs/ 目录（文档页内联渲染）
    fs: {
      allow: ['..'],
    },
    proxy: {
      '/api': {
        target: 'http://localhost:9079',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: 'ws://localhost:9079',
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': '/src',
    },
  },

  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
});