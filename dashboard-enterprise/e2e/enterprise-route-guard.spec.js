import { test, expect } from '@playwright/test'

// Enterprise edition route guard e2e tests.
//
// Guard under test (src/router/index.js):
//   - Routes /audit, /rbac, /tenants carry meta.requiresEnterprise.
//   - beforeEach reads useEditionStore().edition; when it is not 'enterprise'
//     the navigation is redirected to '/'.
//
// Cold-load fix (src/stores/edition.js):
//   - The pinia store initial state now reads the persisted localStorage
//     snapshot via loadInitialEdition() at store construction time, instead of
//     hardcoding edition='enterprise'. fetchEdition() still runs in App.vue
//     onMounted to refresh from the backend, but the very first
//     router.beforeEach now observes the real persisted edition.
//   - Consequence: a cold page.goto with localStorage maop_edition.edition
//     === 'personal' is redirected to '/' on the FIRST guard pass. The
//     localStorage snapshot branch in the guard is no longer dead code.
//
// P1-H1 security fix: cold-load default changed from 'enterprise' to
//   'personal' (fail-safe). The guard also fire-and-forget hydrates edition
//   from /api/info/config for subsequent SPA navigation, but the cold first
//   pass never waits for the backend — no snapshot → default personal →
//   enterprise-only routes blocked.
//
// Test coverage:
//   - personal edition: direct goto /audit|/rbac|/tenants → redirected to the
//     home page (/home) (exercises the cold-load fix). SPA navigation variants
//     retained to verify the post-fetchEdition path still redirects.
//     2026-09-04 IA redesign follow-up: the guard still redirects to '/', but
//     '/' is now itself a redirect record to '/home' (router/index.js), so
//     the final observed URL is /home. Assertions updated accordingly.
//   - enterprise edition: direct goto loads without redirect.
//   - cold load boundary: no localStorage snapshot → default personal blocks
//     /audit on the cold first pass; no localStorage + personal API → cold
//     goto /audit blocked (fail-safe), SPA nav after hydration still blocked.


// Stub every /api call so the suite does not depend on a live backend.
// The edition endpoint returns the requested edition; auth/status reports
// auth disabled; everything else returns a benign 200 JSON body so views
// that fetch on mount do not throw.
async function stubApi(page, edition) {
  await page.route('**/api/**', (route) => {
    const url = route.request().url()
    if (url.includes('/api/info/edition')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ edition, features: {}, backends: {}, degradations: [] }),
      })
    }
    // P1-H1: guard hydrates edition from /api/info/config
    if (url.includes('/api/info/config')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ project_name: 'MAOP', edition, debug: false }),
      })
    }
    if (url.includes('/api/auth/status')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ auth_enabled: false, has_token: false }),
      })
    }
    if (url.includes('/api/health')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ version: 'test' }),
      })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

// Trigger a client-side (SPA) navigation so vue-router re-runs beforeEach
// against the already-hydrated pinia store. Useful for verifying behaviour
// after fetchEdition() has refreshed the edition from the backend.
async function spaNavigate(page, path) {
  await page.evaluate((p) => {
    window.history.pushState({}, '', p)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, path)
}

// Register a promise that resolves when /api/info/edition responds. MUST be
// registered BEFORE the navigation that triggers the request — Playwright
// waitForResponse only captures responses that arrive after registration, so
// registering after `await page.goto(...)` can miss an already-completed
// response and time out.
function editionResponsePromise(page) {
  return page.waitForResponse((resp) => resp.url().includes('/api/info/edition'))
}

test.describe('Enterprise route guard', () => {
  test.describe('personal edition', () => {
    test.beforeEach(async ({ page }) => {
      // Pre-seed the localStorage snapshot so the cold-load fix reads
      // edition='personal' at store construction time, making the very first
      // router.beforeEach observe personal and redirect enterprise-only routes.
      await page.addInitScript(() => {
        localStorage.setItem('maop_edition', JSON.stringify({ edition: 'personal' }))
      })
      await stubApi(page, 'personal')
    })

    test('direct goto /audit redirects to /home (cold-load fix)', async ({ page }) => {
      await page.goto('/audit')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('direct goto /rbac redirects to /home (cold-load fix)', async ({ page }) => {
      await page.goto('/rbac')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('direct goto /tenants redirects to /home (cold-load fix)', async ({ page }) => {
      await page.goto('/tenants')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('SPA navigation to /audit redirects to /home', async ({ page }) => {
      const editionResp = editionResponsePromise(page)
      await page.goto('/')
      await editionResp
      await spaNavigate(page, '/audit')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('SPA navigation to /rbac redirects to /home', async ({ page }) => {
      const editionResp = editionResponsePromise(page)
      await page.goto('/')
      await editionResp
      await spaNavigate(page, '/rbac')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('SPA navigation to /tenants redirects to /home', async ({ page }) => {
      const editionResp = editionResponsePromise(page)
      await page.goto('/')
      await editionResp
      await spaNavigate(page, '/tenants')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('overview / redirects to /home (new IA)', async ({ page }) => {
      await page.goto('/')
      await expect(page).toHaveTitle(/MAOP/)
      await expect(page).toHaveURL(/\/home$/)
    })
  })

  test.describe('enterprise edition', () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(() => {
        localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }))
      })
      await stubApi(page, 'enterprise')
    })

    test('/audit loads without redirect', async ({ page }) => {
      await page.goto('/audit')
      await expect(page).toHaveURL(/\/audit$/)
      await expect(page.locator('body')).not.toBeEmpty()
    })

    test('/rbac loads without redirect', async ({ page }) => {
      await page.goto('/rbac')
      await expect(page).toHaveURL(/\/rbac$/)
      await expect(page.locator('body')).not.toBeEmpty()
    })

    test('/tenants loads without redirect', async ({ page }) => {
      await page.goto('/tenants')
      await expect(page).toHaveURL(/\/tenants$/)
      await expect(page.locator('body')).not.toBeEmpty()
    })
  })

  test.describe('cold load boundary', () => {
    test('no localStorage snapshot → /audit blocked (default personal)', async ({ page }) => {
      // P1-H1: No maop_edition in localStorage. loadInitialEdition() falls back
      // to 'personal' (fail-safe), so the guard blocks the enterprise-only route
      // on the first cold pass. This documents the secure default-when-no-snapshot
      // behaviour — personal users cannot bypass the enterprise route guard.
      await page.addInitScript(() => {
        localStorage.removeItem('maop_edition')
      })
      await stubApi(page, 'enterprise')
      await page.goto('/audit')
      await expect(page).toHaveURL(/\/home$/)
    })

    test('no localStorage + personal API → cold goto blocked, SPA nav still blocked', async ({ page }) => {
      // P1-H1: No localStorage snapshot → store initial edition='personal' (fail-safe
      // default), so the cold goto /audit is blocked on the first guard pass.
      // After hydrateEditionFromConfig() hydrates personal from /api/info/config,
      // a subsequent SPA navigation to /audit is still blocked. This pins the
      // fail-safe cold-pass-blocked / second-pass-blocked behaviour when there
      // is no persisted snapshot to pre-hydrate from.
      await page.addInitScript(() => {
        localStorage.removeItem('maop_edition')
      })
      await stubApi(page, 'personal')
      const configResp = page.waitForResponse((resp) => resp.url().includes('/api/info/config'))
      await page.goto('/audit')
      await expect(page).toHaveURL(/\/home$/)
      await configResp
      await spaNavigate(page, '/audit')
      await expect(page).toHaveURL(/\/home$/)
    })
  })
})
