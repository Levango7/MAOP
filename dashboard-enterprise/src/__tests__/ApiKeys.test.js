// Tests for ApiKeys.vue — list rendering, field mapping, empty/error states,
// generate dialog flow and key-reveal-once behaviour.
//
// ApiKeys.onMounted calls load() which hits GET /api/auth/api-keys.
// Generate posts to /api/auth/api-keys and reveals the full key once.
// We mock global.fetch, stub PageHeader (it depends on vue-router), then
// assert on the rendered key rows and dialog interactions.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import ApiKeys from '../views/ApiKeys.vue';
import { EmptyState } from '../components/index.js';

// PageHeader calls useRoute() which needs a router context; stub it so we can
// mount the view without providing a full vue-router instance. ListPageLayout
// renders PageHeader internally, so the stub applies transitively.
const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('ApiKeys.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    // Mark vitest env so handleUnauthorized won't dispatch CustomEvent.
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url, opts) => {
      const u = String(url);
      const method = (opts && opts.method) || 'GET';
      // POST/PUT routes may be keyed by "METHOD URL"
      const keyed = method.toUpperCase() + ' ' + u;
      const body = routes[keyed] ?? routes[u] ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/auth/api-keys': {
        keys: [
          {
            key_id: 'k_001',
            name: 'CI Pipeline',
            key_prefix: 'maop_a1b2_****',
            scopes: ['agents:read', 'agents:execute'],
            status: 'active',
            rate_limit: 60,
            ip_whitelist: '',
            created_at: '2026-08-01T00:00:00Z',
            last_used_at: '2026-08-12T10:00:00Z',
            expires_at: null,
          },
          {
            key_id: 'k_002',
            name: 'Legacy Bot',
            key_prefix: 'maop_c3d4_****',
            scopes: ['agents:read'],
            status: 'revoked',
            rate_limit: 0,
            ip_whitelist: '10.0.0.0/8',
            created_at: '2026-07-01T00:00:00Z',
            last_used_at: null,
            expires_at: '2026-12-31T00:00:00Z',
          },
        ],
      },
      ...overrides,
    };
  }

  async function mountApiKeys() {
    const wrapper = mount(ApiKeys, mountOptions);
    // load() runs in onMounted; two flushes settle fetch + .finally().
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the apikeys root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountApiKeys();
    expect(wrapper.find('.apikeys-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders key rows from the loaded list', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountApiKeys();
    const rows = wrapper.findAll('.ak-row');
    // 1 head + 2 data rows
    expect(rows).toHaveLength(3);
    expect(wrapper.text()).toContain('CI Pipeline');
    expect(wrapper.text()).toContain('Legacy Bot');
    wrapper.unmount();
  });

  it('maps key_prefix, scopes and status fields onto rows', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountApiKeys();
    const dataRows = wrapper.findAll('.ak-row:not(.ak-row--head)');
    expect(dataRows).toHaveLength(2);

    // Row 0 — active key
    expect(dataRows[0].find('.ak-name').text()).toBe('CI Pipeline');
    expect(dataRows[0].find('.ak-mono').text()).toBe('maop_a1b2_****');
    expect(dataRows[0].text()).toContain('agents:read');
    expect(dataRows[0].text()).toContain('agents:execute');
    // status badge renders the localised "active" label
    expect(dataRows[0].find('.ak-cell--status .badge').text()).toMatch(/active|有效/i);

    // Row 1 — revoked key has no revoke button
    expect(dataRows[1].find('.ak-name').text()).toBe('Legacy Bot');
    const revokeBtns = dataRows[1].findAll('.btn-icon--danger');
    expect(revokeBtns).toHaveLength(0);
    wrapper.unmount();
  });

  it('shows empty state when no keys are returned', async () => {
    mockFetch({ '/api/auth/api-keys': { keys: [] } });
    const wrapper = await mountApiKeys();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).not.toContain('CI Pipeline');
    wrapper.unmount();
  });

  it('shows error state when the list API fails', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      })
    );
    const wrapper = await mountApiKeys();
    // ListPageLayout renders EmptyState with the error title/description.
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('API /api/auth/api-keys: 500');
    wrapper.unmount();
  });

  it('opens the generate dialog when the generate button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountApiKeys();
    // The generate button lives in the #actions slot (rendered via stubbed PageHeader).
    const genBtn = wrapper.find('button.btn--primary');
    expect(genBtn.exists()).toBe(true);
    await genBtn.trigger('click');
    expect(wrapper.find('.modal-overlay').exists()).toBe(true);
    expect(wrapper.text()).toContain('Generate API Key');
    // Scope groups are rendered as checkboxes
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('generates a key and reveals the full plaintext once', async () => {
    const fullKey = 'maop_a1b2_c3d4_e5f6_g7h8_fullsecret';
    mockFetch({
      ...defaultRoutes(),
      'POST /api/auth/api-keys': { key: fullKey, key_id: 'k_003', status: 'ok' },
    });
    const wrapper = await mountApiKeys();

    // Open generate dialog
    await wrapper.find('button.btn--primary').trigger('click');
    // Fill in the name
    const nameInput = wrapper.find('input[type="text"]');
    await nameInput.setValue('New CI Key');
    // Submit (the second .btn--primary is the dialog's generate button)
    const dialogBtn = wrapper.findAll('button.btn--primary')[1];
    await dialogBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    // The full key is shown in the result modal
    expect(wrapper.find('.key-full').exists()).toBe(true);
    expect(wrapper.find('.key-full').text()).toBe(fullKey);
    expect(wrapper.text()).toContain('Copy');
    wrapper.unmount();
  });

  it('warns when generating without a name', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountApiKeys();
    await wrapper.find('button.btn--primary').trigger('click');
    // Submit without filling the name
    const dialogBtn = wrapper.findAll('button.btn--primary')[1];
    await dialogBtn.trigger('click');
    await flushPromises();
    // Dialog stays open (no POST made, no key result)
    expect(wrapper.find('.key-full').exists()).toBe(false);
    // The generate dialog is still visible
    expect(wrapper.text()).toContain('Generate API Key');
    wrapper.unmount();
  });

  it('opens the detail drawer when the view-detail button is clicked', async () => {
    mockFetch({
      ...defaultRoutes(),
      '/api/auth/api-keys/k_001': {
        key: {
          key_id: 'k_001',
          name: 'CI Pipeline',
          key_prefix: 'maop_a1b2_****',
          scopes: ['agents:read', 'agents:execute'],
          status: 'active',
          rate_limit: 60,
          ip_whitelist: '',
          created_at: '2026-08-01T00:00:00Z',
          last_used_at: '2026-08-12T10:00:00Z',
          expires_at: null,
        },
        stats: {
          total_calls: 1200,
          success_rate: 98.5,
          avg_latency_ms: 142,
          call_trend: [{ date: '2026-08-12', count: 120 }],
          status_dist: { '2xx': 1180, '4xx': 15, '5xx': 5 },
        },
        recent_calls: [
          { ts: '2026-08-12T10:00:00Z', time: '2026-08-12T10:00:00Z', status: 200, latency_ms: 88, ip: '10.0.0.1' },
        ],
      },
    });
    const wrapper = await mountApiKeys();
    // First data row's view-detail button (file-text icon)
    const dataRows = wrapper.findAll('.ak-row:not(.ak-row--head)');
    const detailBtn = dataRows[0].findAll('.btn-icon')[0];
    await detailBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    // DetailDrawer is teleported to body; check its presence via the dialog role.
    const drawer = document.querySelector('[role="dialog"]');
    expect(drawer).not.toBeNull();
    expect(drawer.textContent).toContain('CI Pipeline');
    expect(drawer.textContent).toContain('agents:read');
    wrapper.unmount();
  });
});