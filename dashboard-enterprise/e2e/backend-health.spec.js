// 真实前后端联调 smoke：不 stub API，真实调用后端。
// 依赖 playwright.config.js 同时起后端(9079) + vite(5174)，且 vite 配置了
// /api → localhost:9079 代理。验证前端经代理能连通后端健康端点。
import { test, expect } from '@playwright/test'

test('后端 /api/health 返回 status ok', async ({ request }) => {
  const resp = await request.get('http://localhost:9079/api/health')
  expect(resp.ok()).toBeTruthy()
  const body = await resp.json()
  expect(body.status).toBe('ok')
})

test('前端经 vite 代理连通后端 /api/health', async ({ request }) => {
  // baseURL 为 http://localhost:5174，vite 代理 /api → 9079
  const resp = await request.get('/api/health')
  expect(resp.ok()).toBeTruthy()
  const body = await resp.json()
  expect(body.status).toBe('ok')
})
