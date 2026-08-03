// Tests for RBAC.vue — role list, permission matrix, empty state, error handling.
//
// RBAC.onMounted calls loadRoles(), loadGrants(), loadPerms() which hit
// /api/rbac/roles, /api/rbac/grants, /api/rbac/permissions. We mock global.fetch,
// stub PageHeader, then assert on the rendered role cards and permission table.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import RBAC from '../views/RBAC.vue';
import { PageHeader, EmptyState, DataTable } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('RBAC.vue', () => {
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
      '/api/rbac/roles': { roles: [{ role: 'admin', permission_count: 3, permissions: ['read', 'write', 'admin'] }] },
      '/api/rbac/grants': { grants: [] },
      '/api/rbac/permissions': { permissions: [{ value: 'read', name: 'READ' }, { value: 'write', name: 'WRITE' }] },
      ...overrides,
    };
  }

  async function mountRBAC() {
    const wrapper = mount(RBAC, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the rbac root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRBAC();
    expect(wrapper.find('.rbac-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders role cards from loaded roles', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRBAC();
    expect(wrapper.find('.role-card').exists()).toBe(true);
    expect(wrapper.text()).toContain('read');
    wrapper.unmount();
  });

  it('renders the permission matrix table', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRBAC();
    expect(wrapper.findComponent(DataTable).exists()).toBe(true);
    expect(wrapper.text()).toContain('READ');
    wrapper.unmount();
  });

  it('shows empty state when no grants and no permissions', async () => {
    mockFetch(defaultRoutes({
      '/api/rbac/permissions': { permissions: [] },
    }));
    const wrapper = await mountRBAC();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows inline error when roles API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/rbac/roles') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountRBAC();
    expect(wrapper.find('.inline-error').exists()).toBe(true);
    wrapper.unmount();
  });
});