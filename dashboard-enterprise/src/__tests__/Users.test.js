// Tests for Users.vue — user list, empty state, error handling, edit dialog.
//
// Users.onMounted calls fetchUsers() which hits /api/auth/users (only when
// isAdmin). We mock global.fetch, set localStorage admin role, stub PageHeader,
// then assert on the rendered user table and edit interaction.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Users from '../views/Users.vue';


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
        users: [{ username: 'alice', roles: ['read'], created_at: 1750000000, enabled: true, tenant_id: '' }],
      },
    }));
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-table').exists()).toBe(true);
    expect(wrapper.text()).toContain('alice');
    wrapper.unmount();
  });

  it('renders created_at epoch seconds as the real date, not 1970', async () => {
    // auth_service 用 time.time() 写 created_at（epoch 秒）。当成毫秒会落到 1970。
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: 1750000000, enabled: true, tenant_id: '' }],
      },
    }));
    const wrapper = await mountUsers();
    const cell = wrapper.findAll('.users-row')[1].find('.users-cell--created');
    expect(cell.text()).toContain('2025');
    expect(cell.text()).not.toContain('1970');
    wrapper.unmount();
  });

  it('still parses an ISO created_at if a caller sends one', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: '2026-01-01', enabled: true, tenant_id: '' }],
      },
    }));
    const wrapper = await mountUsers();
    const cell = wrapper.findAll('.users-row')[1].find('.users-cell--created');
    expect(cell.text()).toContain('2026');
    wrapper.unmount();
  });

  it('does not render the last-login column the API never fills', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: 1750000000, enabled: true, tenant_id: '' }],
      },
    }));
    const wrapper = await mountUsers();
    expect(wrapper.find('.users-cell--login').exists()).toBe(false);
    expect(wrapper.findAll('.users-row')[0].findAll('.users-cell')).toHaveLength(5);
    wrapper.unmount();
  });

  it('shows empty state when no users', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountUsers();
    // 空态交给 ListPageLayout 内部的 EmptyState 渲染(根类名 .empty)
    expect(wrapper.find('.empty').exists()).toBe(true);
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
        users: [{ username: 'alice', roles: ['read'], created_at: 1750000000, enabled: true, tenant_id: '' }],
      },
    }));
    const wrapper = await mountUsers();
    await wrapper.find('.btn-icon').trigger('click');
    await flushPromises();
    expect(wrapper.find('.users-dialog').exists()).toBe(true);
    wrapper.unmount();
  });

  function sentBodies() {
    return global.fetch.mock.calls
      .filter(([, init]) => init && init.method && init.method !== 'GET')
      .map(([url, init]) => ({ url: String(url), method: init.method, body: JSON.parse(init.body) }));
  }

  it('registers with exactly the three fields the request model declares', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/register': { status: 'ok', username: 'bob', roles: ['read'], tenant_id: '' },
    }));
    const wrapper = await mountUsers();
    await wrapper.find('.btn-primary').trigger('click');
    await flushPromises();
    const inputs = wrapper.findAll('.users-form input');
    await inputs[0].setValue('bob');
    await inputs[1].setValue('secret123');
    await wrapper.find('.users-dialog-actions .btn-primary').trigger('click');
    await flushPromises();

    const sent = sentBodies();
    expect(sent).toHaveLength(1);
    expect(sent[0]).toEqual({
      url: '/api/auth/register',
      method: 'POST',
      body: { username: 'bob', password: 'secret123', roles: ['read'] },
    });
    wrapper.unmount();
  });

  it('edits with roles only — no username, no empty password', async () => {
    mockFetch(defaultRoutes({
      '/api/auth/users': {
        status: 'ok',
        users: [{ username: 'alice', roles: ['read'], created_at: 1750000000, enabled: true, tenant_id: '' }],
      },
      '/api/auth/users/alice': { status: 'ok', message: 'User alice updated' },
    }));
    const wrapper = await mountUsers();
    await wrapper.find('.btn-icon').trigger('click');
    await flushPromises();
    const boxes = wrapper.findAll('.users-role-chip input');
    // roleOptions: admin, superadmin, operator, write, read — 勾上 write
    await boxes[3].setValue(true);
    await wrapper.find('.users-dialog-actions .btn-primary').trigger('click');
    await flushPromises();

    const sent = sentBodies();
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe('/api/auth/users/alice');
    expect(sent[0].method).toBe('PUT');
    expect(sent[0].body).toEqual({ roles: ['read', 'write'] });
    wrapper.unmount();
  });
});