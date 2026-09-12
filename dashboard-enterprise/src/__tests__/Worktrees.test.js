// Smoke tests for Worktrees.vue — Git worktree management page.
//
// Worktrees.onMounted calls loadBranches() which hits /api/worktree/list.
// We mock global.fetch, stub PageHeader (via ListPageLayout), then assert
// the root renders, branch rows display, create-root modal opens, and
// error state shows on API failure.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Worktrees from '../views/Worktrees.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Worktrees.vue', () => {
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
      // Match exact URL or URL without query string
      const base = u.split('?')[0];
      const body = routes[u] ?? routes[base] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/worktree/list': { status: 'ok', branches: [], count: 0 },
      ...overrides,
    };
  }

  async function mountWorktrees() {
    const wrapper = mount(Worktrees, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the wt-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountWorktrees();
    expect(wrapper.find('.wt-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders empty state when no branches exist', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountWorktrees();
    expect(wrapper.text()).toContain('No worktree branches');
    wrapper.unmount();
  });

  it('renders branch rows when /api/worktree/list returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/worktree/list': {
        status: 'ok',
        branches: [
          { id: 'n1', name: 'main', status: 'active', parent_id: '', description: 'root' },
          { id: 'n2', name: 'feature/auth', status: 'active', parent_id: 'n1', description: 'auth feature' },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountWorktrees();
    const rows = wrapper.findAll('.wt-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('main');
    expect(wrapper.text()).toContain('feature/auth');
    wrapper.unmount();
  });

  it('opens create-root modal when Create Root button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountWorktrees();
    const buttons = wrapper.findAll('button');
    const createBtn = buttons.find((b) => b.text().includes('Create Root'));
    expect(createBtn).toBeTruthy();
    await createBtn.trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('opens branch modal when branch button on a row clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/worktree/list': {
        status: 'ok',
        branches: [
          { id: 'n1', name: 'main', status: 'active', parent_id: '', description: 'root' },
        ],
        count: 1,
      },
    }));
    const wrapper = await mountWorktrees();
    // Click the first icon-btn (branch button) in the actions cell
    const branchBtns = wrapper.findAll('.wt-table__td--act .icon-btn');
    expect(branchBtns.length).toBeGreaterThan(0);
    await branchBtns[0].trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('shows error state when API fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountWorktrees();
    expect(wrapper.find('.wt-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Failed to load branches');
    wrapper.unmount();
  });

  it('displays active badge for active branches', async () => {
    mockFetch(defaultRoutes({
      '/api/worktree/list': {
        status: 'ok',
        branches: [
          { id: 'n1', name: 'main', status: 'active', parent_id: '', description: '' },
          { id: 'n2', name: 'abandoned', status: 'abandoned', parent_id: 'n1', description: '' },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountWorktrees();
    const badges = wrapper.findAll('.badge');
    expect(badges.length).toBeGreaterThanOrEqual(2);
    expect(badges[0].text()).toContain('Active');
    expect(badges[1].text()).toContain('Abandoned');
    wrapper.unmount();
  });

  it('passes active_only query param when toggle is checked', async () => {
    const fetchSpy = vi.fn((url) => {
      const u = String(url);
      const body = u.includes('active_only=true')
        ? { status: 'ok', branches: [], count: 0 }
        : { status: 'ok', branches: [], count: 0 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    global.fetch = fetchSpy;
    const wrapper = await mountWorktrees();
    // Find the active-only toggle checkbox
    const toggle = wrapper.find('.wt-toggle input[type="checkbox"]');
    expect(toggle.exists()).toBe(true);
    await toggle.trigger('change');
    await flushPromises();
    // Verify a fetch was made with active_only=true
    const calls = fetchSpy.mock.calls.map((c) => String(c[0]));
    expect(calls.some((u) => u.includes('active_only=true'))).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when list endpoint returns malformed data', async () => {
    mockFetch({
      '/api/worktree/list': { status: 'ok' },
    });
    const wrapper = await mountWorktrees();
    expect(wrapper.find('.wt-view').exists()).toBe(true);
    // Should show empty state (no branches array)
    expect(wrapper.text()).toContain('No worktree branches');
    wrapper.unmount();
  });
});