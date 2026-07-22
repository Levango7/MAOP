import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

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
});