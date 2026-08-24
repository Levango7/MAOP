import { test, expect } from '@playwright/test'

// Evolve page (/evolve, Evolve.vue) e2e tests.
//
// Coverage:
//   - Page load: /evolve renders the evolve console
//   - Core elements: tab switcher / stats row / status badge / trigger button
//   - Enterprise edition: evolution milestones timeline renders
//
// All /api/** calls are stubbed so the suite runs without a live backend.

async function stubApi(page, edition = 'enterprise') {
  await page.route('**/api/**', (route) => {
    const url = route.request().url()
    if (url.includes('/api/info/edition')) {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ edition, features: {}, backends: {}, degradations: [] }),
      })
    }
    if (url.includes('/api/info/config')) {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ project_name: 'MAOP', edition, debug: false }),
      })
    }
    if (url.includes('/api/auth/status')) {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ auth_enabled: false, has_token: false }),
      })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

test.describe('Evolve page', () => {
  test('page loads with app root', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    await expect(page).toHaveURL(/\/evolve$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('evolve root container renders', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    await expect(page.locator('.evolve-page')).toBeVisible()
  })

  test('tab switcher renders', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    await expect(page.locator('.evolve-tabs')).toBeVisible()
  })

  test('main tab is default and renders stats row', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    // tab defaults to 'main' → .evolve-main section renders.
    await expect(page.locator('.evolve-main')).toBeVisible()
    // Stats row with 4 StatCards.
    await expect(page.locator('.evolve-main .stats-row')).toBeVisible()
  })

  test('status badge renders in header', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    await expect(page.locator('.status-badge')).toBeVisible()
  })

  test('trigger evolution button is present', async ({ page }) => {
    await stubApi(page)
    await page.goto('/evolve')
    await expect(page.locator('.btn-action')).toBeVisible()
  })

  test('enterprise edition shows evolution milestones timeline', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }))
    })
    await stubApi(page, 'enterprise')
    await page.goto('/evolve')
    // .evolve-milestones only renders when edition.isEnterprise is true.
    await expect(page.locator('.evolve-milestones')).toBeVisible({ timeout: 10_000 })
    // The timeline should have milestone nodes.
    expect(await page.locator('.evolve-milestones__node').count()).toBeGreaterThan(0)
  })
})