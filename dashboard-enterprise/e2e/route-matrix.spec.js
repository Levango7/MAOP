import { test, expect } from '@playwright/test'

// 全路由冒烟矩阵（2026-10-10 补）：51 个视图此前仅 6 个 spec 覆盖约 12%
// 的页面。本 spec 给**每条主路由**（个人版可用、非企业专属——那 8 条由
// enterprise-route-guard.spec.js 覆盖）一张回归网：
//
//   1. 页面挂载（#app 可见）
//   2. 主内容非空
//   3. 控制台零错误 + 零未捕获异常（页面级运行时崩溃）
//   4. 无意外重定向（路由守卫只应动企业路由）
//   5. 可见文本不含原始 i18n key —— PR #73（Chat 页原始 key 直显）那类
//      bug 的**运行时**防线：静态守卫（src/__tests__/i18n-key-completeness）
//      看不见动态拼接的 key，这里补上
//
// 真实后端联调（不桩 API）：依赖 playwright.config.js 的 webServer 同时起
// 后端(9079) + vite(5174)，与 backend-health.spec.js 同款。
//
// 仅 chromium：矩阵是浏览器无关的冒烟网（跨浏览器覆盖由 layout /
// knowledge-graph spec 承担），全量跑 3 浏览器会把 Playwright 作业时长
// 翻三倍。三条断言里有任一浏览器相关回归，layout/knowledge-graph 腿会先红。
test.skip(({ browserName }) => browserName !== 'chromium', '冒烟矩阵仅 chromium')

// /docs 渲染仓库 markdown（CHANGELOG 等正文里正常出现 `view.chat.title`
// 这类 key 名），排除原始 key 断言。
const RAW_KEY_EXEMPT = new Set(['/docs'])

const ROUTES = [
  '/home',
  '/home/tasks',
  '/run',
  '/run/agents',
  '/run/workflow',
  '/memory',
  '/memory/search',
  '/memory/graph',
  '/capability/agents',
  '/capability/skills',
  '/capability/market',
  '/capability/models',
  '/capability/evolve',
  '/capability/mcp',
  '/capability/plugins',
  '/capability/subagents',
  '/capability/routing',
  '/capability/scheduling',
  '/capability/protocols',
  '/capability/worktrees',
  '/capability/agent-proxy',
  '/capability/agent-registry',
  '/capability/agent-billing',
  '/capability/agent-gateway',
  '/operate',
  '/operate/logs',
  '/operate/tracing',
  '/operate/cost',
  '/operate/debate',
  '/operate/blackboard',
  '/operate/alerts',
  '/operate/webhooks',
  '/operate/integrations',
  '/analysis',
  '/feedback',
  '/notifications',
  '/settings',
  '/docs',
]

const RAW_KEY_RE = /\b(?:view|nav|common|a11y|status|auth|footer|settings)\.[a-z][\w.]*\b/

// 浏览器级网络噪声过滤（no-auth 个人版 E2E 环境的**设计内**行为，非缺陷）：
//   - 403：admin-only 信息端点（/api/info/*）。无鉴权模式匿名用户只获得
//     read 角色（P0-4 安全修复明确禁止授予 admin），端点锁死是既定安全姿态；
//     页面按 P1-H1 fail-closed 降级（edition 默认 personal）。
//   - 404：enterprise-only 路由（n8n 等，_register_routes.py 按 FeatureFlag
//     注册），个人版不注册 → 页面显示未连接态，本就如此。
// 这两类都是 Chrome 对失败请求的通用网络日志（不带 URL、应用层无法屏蔽），
// 与应用自己的 console.error / pageerror / 5xx 是两回事——后者继续全量抓捕。
const NETWORK_NOISE_RE =
  /^Failed to load resource: the server responded with a status of (403|404)\b/

test.describe('route matrix smoke (real backend)', () => {
  test('the matrix itself is non-vacuous', () => {
    // 防"路由表被清空/写错 → 所有用例消失 → 绿"。
    expect(ROUTES.length).toBeGreaterThan(30)
    expect(new Set(ROUTES).size).toBe(ROUTES.length)
  })

  for (const route of ROUTES) {
    test(`renders without errors: ${route}`, async ({ page }) => {
      const problems = []
      page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`))
      page.on('console', (msg) => {
        if (msg.type() === 'error') {
          const text = msg.text()
          if (!NETWORK_NOISE_RE.test(text)) problems.push(`console: ${text}`)
        }
      })

      await page.goto(route)
      await expect(page.locator('#app')).toBeVisible()

      // 主内容非空（等渲染稳定后取文本）
      const text = await page.locator('body').textContent()
      expect((text ?? '').trim().length, `empty body on ${route}`).toBeGreaterThan(20)

      // 无意外重定向（企业路由守卫除外——本矩阵不含它们）
      expect(new URL(page.url()).pathname, `unexpected redirect on ${route}`).toBe(route)

      // 可见文本不含原始 i18n key（/docs 豁免：正文正常引用 key 名）
      if (!RAW_KEY_EXEMPT.has(route)) {
        expect(text, `raw i18n key visible on ${route}`).not.toMatch(RAW_KEY_RE)
      }

      expect(problems, `runtime problems on ${route}`).toEqual([])
    })
  }
})
