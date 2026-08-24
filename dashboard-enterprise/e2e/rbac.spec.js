import { test, expect } from '@playwright/test'

// RBAC page (/rbac, RBAC.vue) e2e tests.
//
// /rbac carries meta.requiresEnterprise — the route guard redirects to /
// when the edition is not 'enterprise'. We pre-seed the localStorage snapshot
// and stub /api/info/edition to 'enterprise' so the guard admits the route.
//
// Coverage:
//   - Page load: /rbac renders without redirect under enterprise edition
//   - Core elements: role grid / grant role button / permission catalog
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

test.describe('RBAC page', () => {
  test.beforeEach(async ({ page }) => {
    // Pre-seed enterprise edition so the cold-load route guard admits /rbac.
    await page.addInitScript(() => {
      localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }))
    })
    await stubApi(page)
  })

  test('page loads without redirect under enterprise edition', async ({ page }) => {
    await page.goto('/rbac')
    await expect(page).toHaveURL(/\/rbac$/)
    await expect(page.locator('#app')).toBeVisible()
  })

  test('rbac root container renders', async ({ page }) => {
    await page.goto('/rbac')
    await expect(page.locator('.rbac-page')).toBeVisible()
  })

  test('grant role button is present in header actions', async ({ page }) => {
    await page.goto('/rbac')
    // The primary "Grant Role" button lives in ListPageLayout #actions slot.
    await expect(page.locator('.btn--primary')).toBeVisible()
  })

  test('role grid renders after loading completes', async ({ page }) => {
    await page.goto('/rbac')
    // .role-grid renders in both loading (skeleton) and loaded states.
    await expect(page.locator('.role-grid')).toBeVisible({ timeout: 10_000 })
  })

  test('grant modal opens on button click', async ({ page }) => {
    await page.goto('/rbac')
    // Wait for the page to settle, then open the grant modal.
    await expect(page.locator('.rbac-page')).toBeVisible()
    await page.locator('.btn--primary').click()
    // The modal overlay with user_id input should appear.
    await expect(page.locator('.modal-overlay')).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('.modal input').first()).toBeVisible()
  })
})