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
  test: {
    environment: 'jsdom',
    globals: true,
  },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
});