// Smoke tests for Tenants.vue — tenant list, create modal, suspend/activate/delete actions.
//
// Tenants.onMounted calls load() → GET /api/tenant/list. We mock global.fetch,
// stub PageHeader (via ListPageLayout), then assert the root renders, tenant
// cards appear, and the create modal opens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Tenants from '../views/Tenants.vue';

// ListPageLayout renders PageHeader internally; stub PageHeader to avoid
// providing a full router context.
const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Tenants.vue', () => {
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
      '/api/tenant/list': { tenants: [] },
      ...overrides,
    };
  }

  async function mountTenants() {
    const wrapper = mount(Tenants, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the tenant-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTenants();
    expect(wrapper.find('.tenant-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders a tenant card per tenant returned by the API', async () => {
    mockFetch(defaultRoutes({
      '/api/tenant/list': {
        tenants: [
          { tenant_id: 'acme', name: 'Acme Corp', status: 'active', plan: 'pro' },
          { tenant_id: 'beta', name: 'Beta Inc', status: 'suspended', plan: 'starter' },
        ],
      },
    }));
    const wrapper = await mountTenants();
    const cards = wrapper.findAll('.tenant-card');
    expect(cards).toHaveLength(2);
    expect(wrapper.text()).toContain('Acme Corp');
    expect(wrapper.text()).toContain('Beta Inc');
    wrapper.unmount();
  });

  it('renders the create-tenant button', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTenants();
    // The create button is inside ListPageLayout #actions slot
    const btns = wrapper.findAll('button');
    const createBtn = btns.find((b) => b.text().includes('Create') || b.classes().some((c) => c.includes('primary')));
    expect(createBtn).toBeTruthy();
    wrapper.unmount();
  });

  it('opens the create modal when the create button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTenants();
    expect(wrapper.find('.modal-overlay').exists()).toBe(false);
    // Find the create button (has btn--primary class)
    const createBtn = wrapper.find('.btn--primary');
    expect(createBtn.exists()).toBe(true);
    await createBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.modal-overlay').exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/tenant/list fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountTenants();
    expect(wrapper.find('.tenant-page').exists()).toBe(true);
    wrapper.unmount();
  });
});