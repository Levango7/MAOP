// Tests for Users.vue — user list, empty state, error handling, edit dialog.
//
// Users.onMounted calls fetchUsers() which hits /api/auth/users (only when
// isAdmin). We mock global.fetch, set localStorage admin role, stub PageHeader,
// then assert on the rendered user table and edit interaction.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Users from '../views/Users.vue';
import { PageHeader } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Users.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    localStorage.setItem('maop_roles', '["admin"]');
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    localStorage.clear();
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
      '/api/auth/users': { status: 'ok', users: [] },
      ...overrides,
    };
  }

  async function mountUsers() {
    const wrapper = mount(Users, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the users root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders user rows from loaded data', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: '2026-01-01', last_login: null }],
      },
    }));
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-table').exists()).toBe(true);
    expect(wrapper.text()).toContain('alice');
    wrapper.unmount();
  });

  it('shows empty state when no users', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-empty').exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/auth/users fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/auth/users') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('opens edit dialog when edit button is clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: '2026-01-01', last_login: null }],
      },
    }));
    const wrapper = await mountUsers();
    await wrapper.find('.btn-icon').trigger('click');
    await flushPromises();
    expect(wrapper.find('.users-dialog').exists()).toBe(true);
    wrapper.unmount();
  });
});