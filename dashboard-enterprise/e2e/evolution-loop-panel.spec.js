import { test, expect } from '@playwright/test'

// T3.1-c 验收 #4：dashboard 可见闭环状态机与 A/B 结果。
// 此前前端零页面调用 /api/evolution/loop/* —— 六个 AC-07 端点上线却无人消费。
// 本 spec 钉住"闭环 tab 存在且接真端点"：切到该 tab → 触发按钮与状态机可见，
// 且点击触发会真的打到后端（拦截 /api/evolution/loop/trigger 验证请求体）。
//
// ⚠️ CoachMarks 引导遮罩已在 playwright.config.js 配置层全局预关
//    （localStorage 'maop_onboarding_done'=1），这里不再重复。

test.describe('T-UI evolution closed-loop panel', () => {
  test.use({ viewport: { width: 1280, height: 900 } })

  // EvolutionHistory 是 Evolve 页的嵌入组件（router 把 /evolution-history
  // 重定向到 /capability/evolve?tab=history），tab 切换器是 Segmented
  // （role="radiogroup"，项为 role="radio"，不是 button）。
  const openLoopTab = async (page) => {
    await page.goto('/capability/evolve?tab=history')
    const loopTab = page.getByRole('radio', { name: /闭环|Closed Loop/i })
    await expect(loopTab).toBeVisible()
    await loopTab.click()
  }

  test('closed-loop tab renders state machine and controls', async ({ page }) => {
    // 后端无认证时端点返回 403，面板应优雅降级（空态）而不是崩
    await openLoopTab(page)

    // 状态机标签 + 触发按钮 + 人工闸门卡片都在
    await expect(page.locator('.loop-state')).toBeVisible()
    await expect(page.locator('.loop-actions button').first()).toBeVisible()
    await expect(page.locator('.loop-toolbar')).toBeVisible()
  })

  test('trigger button posts to the real endpoint', async ({ page }) => {
    await openLoopTab(page)

    // 拦截真实请求：确认前端确实调 /api/evolution/loop/trigger 且带 dry_run 字段
    const [request] = await Promise.all([
      page.waitForRequest(
        (req) => req.url().includes('/api/evolution/loop/trigger'),
        { timeout: 15_000 },
      ),
      page.locator('.loop-actions button').last().click(),
    ])
    const body = request.postDataJSON()
    expect(body).toHaveProperty('dry_run')
    expect(typeof body.dry_run).toBe('boolean')
  })
})