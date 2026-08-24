import { test, expect } from '@playwright/test'

// Audit page (/audit, Audit.vue) e2e tests.
//
// /audit carries meta.requiresEnterprise — the route guard redirects to /
// when the edition is not 'enterprise'. We pre-seed the localStorage snapshot
// and stub /api/info/edition to 'enterprise' so the guard admits the route.
//
// Coverage:
//   - Page load: /audit renders without redirect under enterprise edition
//   - Core elements: tab switcher / events stat row / filter bar / body
//
// All /api/** calls are stubbed so the suite runs without a live backend.

async function stubApi(page) {
  await page.route('**/api/**', (route) => {
    const url = route.request().url()
    if (url.includes('/api/info/edition')) {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ edition: 'enterprise', features: {}, backends: {}, degradations: [] }),
      })
    }
    if (url.includes('/api/info/config')) {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ project_name: 'MAOP', edition: 'enterprise', debug: false }),
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

test.describe('Audit page', () => {
  test.beforeEach(async ({ page }) => {
    // Pre-seed enterprise edition so the cold-load route guard admits /audit.
    await page.addInitScript(() => {
      localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }))
    })
    await stubApi(page)
  })

  test('page loads without redirect under enterprise edition', async ({ page }) => {
    await page.goto('/audit')
    await expect(page).toHaveURL(/\/audit$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('audit root container renders', async ({ page }) => {
    await page.goto('/audit')
    await expect(page.locator('.audit-view')).toBeVisible()
  })

  test('tab switcher renders', async ({ page }) => {
    await page.goto('/audit')
    await expect(page.locator('.audit-tabs')).toBeVisible()
  })

  test('events tab is default and renders stat row', async ({ page }) => {
    await page.goto('/audit')
    // tab defaults to 'events' → .audit-events section renders.
    await expect(page.locator('.audit-events')).toBeVisible()
    // Top stat row with 4 StatCards (today ops / high risk / active users / anomalies).
    await expect(page.locator('.audit-events .stat-row')).toBeVisible()
  })

  test('filter bar renders with export controls', async ({ page }) => {
    await page.goto('/audit')
    await expect(page.locator('.audit-filterbar')).toBeVisible()
    // CSV and JSON export buttons.
    expect(await page.locator('.export-group .act-btn').count()).toBeGreaterThanOrEqual(1)
  })

  test('audit body renders after loading completes', async ({ page }) => {
    await page.goto('/audit')
    // Three-state body: error → loading → content. Stubbed empty data resolves
    // without error, so .audit-body should appear once loading clears.
    await expect(page.locator('.audit-body')).toBeVisible({ timeout: 10_000 })
  })
})