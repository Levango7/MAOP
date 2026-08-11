import { test, expect } from '@playwright/test'

test.describe('MAOP Dashboard Core Flows', () => {
  test('overview page loads and shows stat cards', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/MAOP/)
    await expect(page.locator('.stat-card, [class*="StatCard"]')).first().toBeVisible()
  })

  test('navigation to all major routes works', async ({ page }) => {
    const routes = [
      '/',
      '/run',
      '/agents',
      '/models',
      '/memory',
      '/search',
      '/audit',
      '/settings',
    ]
    for (const route of routes) {
      await page.goto(route)
      await expect(page.locator('body')).not.toBeEmpty()
    }
  })

  test('legacy routes redirect to merged pages', async ({ page }) => {
    // RFC-001 迭代 A: /control /chat /evolution-history → /run 或 /evolve
    await page.goto('/control')
    await expect(page).toHaveURL(/\/run\?tab=structured/)
    await page.goto('/chat')
    await expect(page).toHaveURL(/\/run\?tab=chat/)
    await page.goto('/evolution-history')
    await expect(page).toHaveURL(/\/evolve\?tab=history/)
  })

  test('login page renders when auth enabled', async ({ page }) => {
    await page.goto('/')
    // If auth is enabled, we should see a login form or redirect
    // If not, we should see the dashboard
    const hasLogin = await page.locator('input[type="password"]').count()
    const hasDashboard = await page.locator('.memory-page, .overview-page, [class*="page"]').count()
    expect(hasLogin + hasDashboard).toBeGreaterThan(0)
  })
})