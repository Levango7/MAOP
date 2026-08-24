import { test, expect } from '@playwright/test'

// Chat page e2e tests.
//
// /chat is a legacy deep-link that 301-redirects to /run?tab=chat (RFC-001
// iteration A). The Run.vue view hosts the Chat component in embedded mode
// when tab === 'chat'.
//
// Coverage:
//   - Redirect: /chat → /run?tab=chat
//   - Page load: /run?tab=chat renders the run shell + embedded chat
//   - Core elements: run tabs / chat page / chat body / chat input area
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

test.describe('Chat page (legacy redirect to /run?tab=chat)', () => {
  test.beforeEach(async ({ page }) => {
    await stubApi(page)
  })

  test('/chat redirects to /run?tab=chat', async ({ page }) => {
    await page.goto('/chat')
    await expect(page).toHaveURL(/\/run\?tab=chat/)
  })

  test('run shell renders with tab switcher', async ({ page }) => {
    await page.goto('/run?tab=chat')
    await expect(page).toHaveURL(/\/run\?tab=chat/)
    await expect(page.locator('.run-view')).toBeVisible()
    await expect(page.locator('.run-tabs')).toBeVisible()
  })

  test('embedded chat page renders', async ({ page }) => {
    await page.goto('/run?tab=chat')
    // Chat.vue root container (.chat-page) renders in embedded mode.
    await expect(page.locator('.chat-page')).toBeVisible({ timeout: 10_000 })
  })

  test('chat body and input area render', async ({ page }) => {
    await page.goto('/run?tab=chat')
    await expect(page.locator('.chat-body')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.chat-input-area')).toBeVisible()
  })

  test('AI split button is present in run header', async ({ page }) => {
    await page.goto('/run?tab=chat')
    await expect(page.locator('.ai-split-btn')).toBeVisible()
  })
})