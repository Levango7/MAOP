// Tests for Licenses.vue — list rendering, filters, generate dialog, detail drawer, status tones.
//
// Licenses.onMounted calls load() which hits /api/licenses/list. We mock global.fetch,
// stub PageHeader, then assert on the rendered DataTable, StatCards, and interactions.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Licenses from '../views/Licenses.vue';
import { EmptyState, DataTable } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

// Helper: build a license object in the shape the API actually returns
// (LicenseRecord: customer / notes / edition / features). The view derives the
// display columns (customer_name / customer_email / version / max_agents) from
// these — mocks shaped like the display fields would hide that translation.
function makeLicense(overrides = {}) {
  return {
    license_id: 'maop_abc123_secret',
    customer: 'Acme Corp',
    edition: 'enterprise',
    status: 'active',
    expires_at: Math.floor(Date.now() / 1000) + 86400 * 365,
    features: ['max_agents:10'],
    max_users: 50,
    notes: 'email: admin@acme.com',
    // 故意不像真 key：高熵值会被 gitleaks 的 generic-api-key 判成泄漏。
    license_key: 'not-a-real-key',
    issued_at: Math.floor(Date.now() / 1000),
    created_at: Math.floor(Date.now() / 1000),
    ...overrides,
  };
}

function auditRoute(id = 'maop_abc123_secret') {
  return {
    [`/api/licenses/${id}/audit`]: {
      status: 'ok',
      count: 1,
      audit_logs: [
        { log_id: 'log-1', license_id: id, action: 'created', actor: 'admin', timestamp: 1.0, detail: '', ip_address: '', metadata: {} },
      ],
    },
  };
}

describe('Licenses.vue', () => {
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
      '/api/licenses/list': { status: 'ok', licenses: [] },
      ...overrides,
    };
  }

  async function mountLicenses() {
    const wrapper = mount(Licenses, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── Rendering ──────────────────────────────────────────────
  it('renders the licenses root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLicenses();
    expect(wrapper.find('.licenses-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders DataTable with loaded licenses', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': {
        status: 'ok',
        licenses: [makeLicense()],
      },
    }));
    const wrapper = await mountLicenses();
    expect(wrapper.findComponent(DataTable).exists()).toBe(true);
    // customer -> customer_name, features['max_agents:10'] -> 10
    expect(wrapper.text()).toContain('Acme Corp');
    expect(wrapper.text()).toContain('10');
    wrapper.unmount();
  });

  it('derives the version column from edition', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [makeLicense()] },
    }));
    const wrapper = await mountLicenses();
    expect(wrapper.text()).toContain('Enterprise');
    wrapper.unmount();
  });

  it('leaves derived fields empty instead of showing undefined', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': {
        status: 'ok',
        licenses: [makeLicense({ notes: '', features: [], edition: '' })],
      },
    }));
    const wrapper = await mountLicenses();
    expect(wrapper.text()).not.toContain('undefined');
    wrapper.unmount();
  });

  it('masks the license key in the table (maop_xxxx_****)', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': {
        status: 'ok',
        licenses: [makeLicense({ license_id: 'maop_seg01_secret' })],
      },
    }));
    const wrapper = await mountLicenses();
    expect(wrapper.text()).toContain('maop_seg01_****');
    expect(wrapper.text()).not.toContain('secret');
    wrapper.unmount();
  });

  it('shows empty state when no licenses', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLicenses();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows inline error when list API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/licenses/list') {
        return Promise.resolve({
          ok: false, status: 500,
          json: () => Promise.resolve({}), text: () => Promise.resolve(''),
        });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = await mountLicenses();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  // ── Stats ──────────────────────────────────────────────────
  it('renders three StatCards for totals', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': {
        status: 'ok',
        licenses: [makeLicense(), makeLicense({ license_id: 'maop_2_y' })],
      },
    }));
    const wrapper = await mountLicenses();
    const stats = wrapper.findAllComponents({ name: 'StatCard' });
    expect(stats.length).toBe(3);
    wrapper.unmount();
  });

  // ── Generate dialog ────────────────────────────────────────
  it('opens generate dialog when Generate button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLicenses();
    const btn = wrapper.find('.lic-btn--primary');
    expect(btn.exists()).toBe(true);
    await btn.trigger('click');
    expect(wrapper.find('.lic-dialog').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows a fixed Enterprise edition instead of a version selector', async () => {
    // 后端 create 请求没有 edition 字段（签发恒为 enterprise），下拉框选什么都
    // 不会被发送——不能保留一个"看起来能选"的控件。
    mockFetch(defaultRoutes());
    const wrapper = await mountLicenses();
    await wrapper.find('.lic-btn--primary').trigger('click');
    expect(wrapper.find('.lic-dialog select').exists()).toBe(false);
    expect(wrapper.find('.lic-dialog .lic-static').text()).toContain('Enterprise');
    wrapper.unmount();
  });

  it('shows form validation error when customer name is empty', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLicenses();
    await wrapper.find('.lic-btn--primary').trigger('click');
    // The dialog has two primary buttons (generate + save); find the save one
    const actions = wrapper.find('.lic-dialog-actions');
    const saveBtn = actions.findAll('button').find((b) => b.text().includes('Save'));
    await saveBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.lic-form-error').exists()).toBe(true);
    wrapper.unmount();
  });

  it('posts to /api/licenses/create on valid submit', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/create': { status: 'ok', license: { license_id: 'maop_new_x' } },
    }));
    const wrapper = await mountLicenses();
    await wrapper.find('.lic-btn--primary').trigger('click');
    // Fill form
    const inputs = wrapper.findAll('.lic-input');
    await inputs[0].setValue('Test Co');
    await inputs[1].setValue('test@test.com');
    // Submit
    const actions = wrapper.find('.lic-dialog-actions');
    const saveBtn = actions.findAll('button').find((b) => b.text().includes('Save'));
    await saveBtn.trigger('click');
    await flushPromises();
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/licenses/create',
      expect.objectContaining({ method: 'POST' }),
    );
    wrapper.unmount();
  });

  // ── Detail drawer ──────────────────────────────────────────
  it('opens detail drawer when a table row is clicked', async () => {
    const lic = makeLicense();
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [lic] },
      '/api/licenses/maop_abc123_secret': { status: 'ok', license: lic },
      ...auditRoute(),
    }));
    const wrapper = await mountLicenses();
    // Click first data row in DataTable
    const row = wrapper.find('.dt tbody tr');
    expect(row.exists()).toBe(true);
    await row.trigger('click');
    await flushPromises();
    await flushPromises();
    // DetailDrawer uses Teleport to body, so check document.body
    expect(document.querySelector('.detail-drawer')).not.toBeNull();
    wrapper.unmount();
  });

  it('fills customer email, version, max_agents and audit history in the drawer', async () => {
    const lic = makeLicense();
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [lic] },
      '/api/licenses/maop_abc123_secret': { status: 'ok', license: lic },
      ...auditRoute(),
    }));
    const wrapper = await mountLicenses();
    await wrapper.find('.dt tbody tr').trigger('click');
    await flushPromises();
    await flushPromises();
    await flushPromises();
    const text = document.body.textContent || '';
    expect(text).toContain('admin@acme.com');   // notes -> customer_email
    expect(text).toContain('Enterprise');       // edition -> version
    expect(text).toContain('10');               // features -> max_agents
    expect(text).toContain('created');          // /audit -> history
    expect(text).toContain('admin');
    wrapper.unmount();
  });

  // ── Status tones ───────────────────────────────────────────
  it('applies success tone (green) for trial status', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [makeLicense({ status: 'trial' })] },
    }));
    const wrapper = await mountLicenses();
    const badge = wrapper.find('.badge--success');
    expect(badge.exists()).toBe(true);
    wrapper.unmount();
  });

  it('applies info tone (blue) for active status', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [makeLicense({ status: 'active' })] },
    }));
    const wrapper = await mountLicenses();
    const badge = wrapper.find('.badge--info');
    expect(badge.exists()).toBe(true);
    wrapper.unmount();
  });

  it('applies fail tone (red) for expired status', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [makeLicense({ status: 'expired' })] },
    }));
    const wrapper = await mountLicenses();
    const badge = wrapper.find('.badge--fail');
    expect(badge.exists()).toBe(true);
    wrapper.unmount();
  });

  it('applies warn tone (orange) for revoked status', async () => {
    mockFetch(defaultRoutes({
      '/api/licenses/list': { status: 'ok', licenses: [makeLicense({ status: 'revoked' })] },
    }));
    const wrapper = await mountLicenses();
    const badge = wrapper.find('.badge--warn');
    expect(badge.exists()).toBe(true);
    wrapper.unmount();
  });
});