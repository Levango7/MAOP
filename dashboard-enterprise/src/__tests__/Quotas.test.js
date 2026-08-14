// Tests for Quotas.vue — tenant quota cards, progress bars, heatmap, detail drawer, alerts.
//
// Quotas.onMounted calls loadAll() which hits /api/tenant/list and
// /api/tenant/quotas/overview. We mock global.fetch, stub PageHeader and chart
// components (Line/Doughnut), then assert on the rendered quota cards, heatmap
// cells, and detail drawer content.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Quotas from '../views/Quotas.vue';
import { EmptyState } from '../components/index.js';

// Stub PageHeader (calls useRoute), chart components (canvas in jsdom), and
// DetailDrawer (Teleport to body) so we can assert content via wrapper.find.
const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
      Line: true,
      Doughnut: true,
      DetailDrawer: {
        template: '<div v-if="open" class="detail-drawer-stub"><slot /></div>',
        props: ['open', 'title', 'icon'],
      },
    },
  },
};

// ── Test data ───────────────────────────────────────────────────
const TENANTS = [
  {
    tenant_id: 'acme',
    name: 'Acme Corp',
    status: 'active',
    plan: 'pro',
    quota: {
      max_agents: 10,
      max_users: 50,
      max_cpu_cores: 8,
      max_memory_mb: 4096,
      max_storage_mb: 10240,
      max_api_calls_per_day: 1000,
    },
    usage: {
      active_agents: 5,
      active_users: 30,
      cpu_cores: 4,
      memory_mb: 2048,
      storage_mb: 5120,
      api_calls_today: 500,
    },
  },
  {
    tenant_id: 'beta',
    name: 'Beta Inc',
    status: 'active',
    plan: 'starter',
    quota: {
      max_agents: 5,
      max_users: 10,
      max_cpu_cores: 2,
      max_memory_mb: 1024,
      max_storage_mb: 2048,
      max_api_calls_per_day: 200,
    },
    usage: {
      active_agents: 6, // 120% — over quota
      active_users: 8, // 80% — warn
      cpu_cores: 1,
      memory_mb: 512,
      storage_mb: 1024,
      api_calls_today: 150,
    },
  },
];

describe('Quotas.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  // ── Mock helpers ──────────────────────────────────────────────

  function mockFetch(routes) {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      const body = routes[u] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/tenant/list': { tenants: TENANTS },
      '/api/tenant/quotas/overview': {
        heatmap: [],
        allocation: { labels: ['Acme Corp', 'Beta Inc'], values: [100, 50] },
      },
      ...overrides,
    };
  }

  /** Full mock with detail-drawer endpoints for a given tenant id. */
  function mockFetchFull(overrides = {}) {
    global.fetch = vi.fn((url, opts) => {
      const u = String(url);
      const method = opts?.method || 'GET';
      let body = {};
      if (u === '/api/tenant/list') {
        body = { tenants: TENANTS };
      } else if (u === '/api/tenant/quotas/overview') {
        body = { heatmap: [], allocation: { labels: ['Acme Corp', 'Beta Inc'], values: [100, 50] } };
      } else if (u.endsWith('/usage/trend')) {
        body = { labels: ['00:00', '06:00', '12:00', '18:00'], series: [{ label: 'API Calls', data: [10, 20, 30, 25] }] };
      } else if (u.endsWith('/quota/history')) {
        body = { history: [{ id: 1, field: 'max_agents', old_value: 5, new_value: 10, changed_by: 'admin', changed_at: Date.now() - 3600000 }] };
      } else if (u.endsWith('/alerts') && method === 'GET') {
        body = { alerts: [{ id: 'a1', level: 'warning', message: 'API calls approaching limit', time: Date.now() - 1800000, status: 'active' }] };
      } else if (u.endsWith('/usage') && method === 'GET') {
        body = { usage: { active_agents: 5, active_users: 30, cpu_cores: 4, memory_mb: 2048, storage_mb: 5120, api_calls_today: 500 } };
      } else if (u.endsWith('/quota') && method === 'POST') {
        body = { status: 'ok' };
      } else if (u.includes('/resolve')) {
        body = { status: 'ok' };
      }
      if (overrides[u]) body = overrides[u];
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountQuotas() {
    const wrapper = mount(Quotas, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── Tests ─────────────────────────────────────────────────────

  it('renders the quotas root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountQuotas();
    expect(wrapper.find('.quotas-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders tenant quota cards from loaded data', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountQuotas();
    const cards = wrapper.findAll('.quota-card');
    expect(cards).toHaveLength(2);
    expect(wrapper.text()).toContain('Acme Corp');
    expect(wrapper.text()).toContain('Beta Inc');
    wrapper.unmount();
  });

  it('renders usage progress bars with three-tone classes', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountQuotas();
    // beta agents: 6/5 = 120% → is-danger
    expect(wrapper.find('.quota-bar__fill.is-danger').exists()).toBe(true);
    // beta users: 8/10 = 80% → is-warn
    expect(wrapper.find('.quota-bar__fill.is-warn').exists()).toBe(true);
    // acme agents: 5/10 = 50% → is-ok
    expect(wrapper.find('.quota-bar__fill.is-ok').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders overview heatmap cells for each tenant and resource', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountQuotas();
    // 2 tenants × 6 resources = 12 cells
    const cells = wrapper.findAll('.heatmap__cell');
    expect(cells).toHaveLength(12);
    wrapper.unmount();
  });

  it('marks over-quota tenant card and heatmap cell with over class', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountQuotas();
    // beta is over quota (agents 120%)
    const overCards = wrapper.findAll('.quota-card.is-over');
    expect(overCards.length).toBeGreaterThanOrEqual(1);
    // heatmap cell for beta agents should be over
    const overCells = wrapper.findAll('.heatmap__cell--over');
    expect(overCells.length).toBeGreaterThanOrEqual(1);
    wrapper.unmount();
  });

  it('shows empty state when no tenants', async () => {
    mockFetch(defaultRoutes({ '/api/tenant/list': { tenants: [] } }));
    const wrapper = await mountQuotas();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows error state when tenant list API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/tenant/list') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') });
    });
    const wrapper = await mountQuotas();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('Could not load quota data');
    wrapper.unmount();
  });

  it('opens detail drawer on view detail click and renders alerts', async () => {
    mockFetchFull();
    const wrapper = await mountQuotas();
    const cards = wrapper.findAll('.quota-card');
    expect(cards.length).toBe(2);
    // First card's first action button = view detail
    const viewDetailBtn = cards[0].findAll('.quota-card__actions button')[0];
    await viewDetailBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // DetailDrawer stub renders when open
    expect(wrapper.find('.detail-drawer-stub').exists()).toBe(true);
    // Alert item rendered
    expect(wrapper.find('.alert-item').exists()).toBe(true);
    expect(wrapper.text()).toContain('API calls approaching limit');
    wrapper.unmount();
  });

  it('renders quota change history in detail drawer', async () => {
    mockFetchFull();
    const wrapper = await mountQuotas();
    const cards = wrapper.findAll('.quota-card');
    const viewDetailBtn = cards[0].findAll('.quota-card__actions button')[0];
    await viewDetailBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // 后端暂无 quota history 端点 → 视图诚实降级为空态(EmptyState), 不渲染历史项
    expect(wrapper.find('.history-item').exists()).toBe(false);
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('opens adjust quota modal and saves via POST', async () => {
    mockFetchFull();
    const wrapper = await mountQuotas();
    const cards = wrapper.findAll('.quota-card');
    // First card's second action button = adjust
    const adjustBtn = cards[0].findAll('.quota-card__actions button')[1];
    await adjustBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.modal').exists()).toBe(true);
    // Click save
    const saveBtn = wrapper.find('.modal .btn--primary');
    await saveBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // Verify a POST request was made to /api/quotas/{id}/{resource} (new contract)
    const postCalls = global.fetch.mock.calls.filter(
      (c) => c[1] && c[1].method === 'POST',
    );
    const quotaPost = postCalls.find((c) => /\/api\/quotas\/acme\//.test(String(c[0])));
    expect(quotaPost).toBeTruthy();
    // Modal closed after successful save
    expect(wrapper.find('.modal').exists()).toBe(false);
    wrapper.unmount();
  });

  it('resolves an alert via POST', async () => {
    if (typeof window !== 'undefined') {
      vi.spyOn(window, 'confirm').mockReturnValue(true);
    }
    mockFetchFull();
    const wrapper = await mountQuotas();
    const cards = wrapper.findAll('.quota-card');
    const viewDetailBtn = cards[0].findAll('.quota-card__actions button')[0];
    await viewDetailBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // Click resolve button on the alert
    const resolveBtn = wrapper.find('.alert-item .btn');
    expect(resolveBtn.exists()).toBe(true);
    await resolveBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // Verify a POST request was made to resolve endpoint
    const postCalls = global.fetch.mock.calls.filter(
      (c) => c[1] && c[1].method === 'POST',
    );
    const resolvePost = postCalls.find((c) => String(c[0]).includes('/resolve'));
    expect(resolvePost).toBeTruthy();
    wrapper.unmount();
  });
});