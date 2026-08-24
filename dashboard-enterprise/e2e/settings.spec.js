import { test, expect } from '@playwright/test'

// Settings page (/settings, Settings.vue) e2e tests.
//
// Coverage:
//   - Page load: /settings renders the settings shell
//   - Core elements: tab switcher (3 tabs) / config grid / appearance card
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

test.describe('Settings page', () => {
  test.beforeEach(async ({ page }) => {
    await stubApi(page)
  })

  test('page loads with app root', async ({ page }) => {
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('settings root container renders', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('.settings-page')).toBeVisible()
  })

  test('tab switcher renders with three tabs', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('.settings-tabs')).toBeVisible()
    // Three tabs: Configuration | Config History | Hook Management.
    await expect(page.locator('.settings-tab')).toHaveCount(3)
  })

  test('config tab is default and renders settings grid', async ({ page }) => {
    await page.goto('/settings')
    // activeTab defaults to 'config' → .settings-grid visible (v-show).
    await expect(page.locator('.settings-grid')).toBeVisible()
  })

  test('switching to history tab updates active state', async ({ page }) => {
    await page.goto('/settings')
    // Click the second tab (Config History).
    await page.locator('.settings-tab').nth(1).click()
    await expect(page.locator('.settings-tab').nth(1)).toHaveAttribute('aria-selected', 'true')
  })

  test('switching to hooks tab updates active state', async ({ page }) => {
    await page.goto('/settings')
    // Click the third tab (Hook Management).
    await page.locator('.settings-tab').nth(2).click()
    await expect(page.locator('.settings-tab').nth(2)).toHaveAttribute('aria-selected', 'true')
  })
})