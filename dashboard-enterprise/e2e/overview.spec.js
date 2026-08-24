import { test, expect } from '@playwright/test'

// Overview page (/, Overview.vue) e2e tests.
//
// Coverage:
//   - Page load: / renders the overview skeleton without error
//   - Core elements: hero strip / action tiles / KPI grid / refresh button
//   - Enterprise edition: Plan-Execute-Verify workflow section renders
//
// All /api/** calls are stubbed so the suite runs without a live backend.
// Overview degrades gracefully on empty data (optional chaining on stats),
// so stubbing {} yields a rendered skeleton rather than an error state.

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

test.describe('Overview page', () => {
  test('page loads with title and app root', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    await expect(page).toHaveTitle(/MAOP/)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('overview root container renders', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    await expect(page.locator('.overview')).toBeVisible()
  })

  test('hero strip renders with status and kpi', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    // .ov-hero renders when !error; stubbed empty data does not set error.
    await expect(page.locator('.ov-hero')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.ov-hero__status')).toBeVisible()
  })

  test('quick action tiles render', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    await expect(page.locator('.ov-actions')).toBeVisible()
    // Each action is a router-link with .ov-action class.
    expect(await page.locator('.ov-action').count()).toBeGreaterThan(0)
  })

  test('KPI stats grid renders', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    await expect(page.locator('.stats-grid')).toBeVisible()
  })

  test('refresh button is present in header', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    await expect(page.locator('.refresh-btn')).toBeVisible()
  })

  test('enterprise edition shows Plan-Execute-Verify workflow section', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }))
    })
    await stubApi(page, 'enterprise')
    await page.goto('/')
    // .ov-pev only renders when edition.isEnterprise is true.
    await expect(page.locator('.ov-pev')).toBeVisible({ timeout: 10_000 })
    // The PEV phases list should have entries.
    expect(await page.locator('.ov-pev__phase').count()).toBeGreaterThan(0)
  })
})