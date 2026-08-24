import { test, expect } from '@playwright/test'

// Funnel Memory page (/funnel-memory, FunnelMemory.vue) e2e tests.
//
// Coverage:
//   - Page load: /funnel-memory renders the funnel memory panel
//   - Core elements: tab switcher / stats row / refresh button / error banner
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

test.describe('Funnel Memory page', () => {
  test.beforeEach(async ({ page }) => {
    await stubApi(page)
  })

  test('page loads with app root', async ({ page }) => {
    await page.goto('/funnel-memory')
    await expect(page).toHaveURL(/\/funnel-memory$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('funnel memory root container renders', async ({ page }) => {
    await page.goto('/funnel-memory')
    await expect(page.locator('.fm-page')).toBeVisible()
  })

  test('tab switcher renders', async ({ page }) => {
    await page.goto('/funnel-memory')
    await expect(page.locator('.fm-tabs')).toBeVisible()
  })

  test('refresh button is present in header', async ({ page }) => {
    await page.goto('/funnel-memory')
    await expect(page.locator('.btn-refresh')).toBeVisible()
  })

  test('evidence tab is default and renders stats row', async ({ page }) => {
    await page.goto('/funnel-memory')
    // activeTab defaults to 'evidence' → .fm-tab section renders.
    await expect(page.locator('.fm-tab')).toBeVisible({ timeout: 10_000 })
    // Stats row with 4 StatCards (L0 total / spilled / total chars / symbolic sessions).
    await expect(page.locator('.fm-tab .fm-stats-row')).toBeVisible()
  })

  test('error banner is absent on successful load', async ({ page }) => {
    await page.goto('/funnel-memory')
    // Stubbed API returns 200, so no error banner should appear.
    await expect(page.locator('.fm-error-banner')).not.toBeVisible()
  })
})