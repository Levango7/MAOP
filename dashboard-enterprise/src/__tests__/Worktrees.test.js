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
    // Modals are teleported to <body>. A test that fails before its
    // unmount() would leave one behind, and the next test's
    // document.querySelector('.modal-overlay') would then find that stale
    // modal and click a component that is no longer the one under test.
    document.querySelectorAll('.modal-overlay').forEach((el) => el.remove());
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

  // ── Merge ──────────────────────────────────────────────────────
  //
  // The merge button used to send the branch *name* while the API looked
  // branches up by id, so a merge started from this page could never find
  // its branch. It now sends the id and shows the name.

  function branchRoutes(mergeBody, mergeStatus = 200) {
    return {
      '/api/worktree/list': {
        status: 'ok',
        branches: [
          { id: 'n1', name: 'main', status: 'active', parent_id: '', description: 'root' },
          { id: 'n2', name: 'feature/auth', status: 'active', parent_id: 'n1', description: 'auth' },
        ],
        count: 2,
      },
      '/api/worktree/merge': mergeBody,
    };
  }

  async function openMergeModal(wrapper, branchName = 'feature/auth') {
    const row = wrapper.findAll('.wt-table__row').find((r) => r.text().includes(branchName));
    expect(row).toBeTruthy();
    const btn = row.findAll('.icon-btn')
      .find((b) => b.attributes('aria-label') === 'Merge Branch');
    expect(btn).toBeTruthy();
    await btn.trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    return modal;
  }

  function mergeSaveButton(modal) {
    return Array.from(modal.querySelectorAll('button'))
      .find((b) => b.textContent.trim() === 'Save');
  }

  it('sends the branch id — not the name — when merging', async () => {
    const calls = [];
    const routes = branchRoutes();
    global.fetch = vi.fn((url, init) => {
      calls.push({ url: String(url), init });
      const u = String(url);
      const body = u.includes('/api/worktree/merge')
        ? { status: 'ok', merge: { success: true, source_branch: 'n2', target_branch: 'n1', strategy: 'auto', conflicts: [], merged_result: '' } }
        : routes[u] || { status: 'ok', branches: [], count: 0 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = await mountWorktrees();
    const modal = await openMergeModal(wrapper);
    // The disabled input shows the name the user recognises…
    const sourceInput = modal.querySelector('input[disabled]');
    expect(sourceInput.value).toBe('feature/auth');

    await mergeSaveButton(modal).click();
    await flushPromises();
    await flushPromises();

    const mergeCall = calls.find((c) => c.url.includes('/api/worktree/merge'));
    expect(mergeCall).toBeTruthy();
    const sent = JSON.parse(mergeCall.init.body);
    expect(sent.source_branch).toBe('n2');
    expect(sent.source_branch).not.toBe('feature/auth');
    wrapper.unmount();
  });

  it('reports merge conflicts instead of looking like a clean merge', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      const body = u.includes('/api/worktree/merge')
        ? {
            status: 'ok',
            merge: {
              success: true, source_branch: 'n2', target_branch: 'n1',
              strategy: 'manual', conflicts: ["Result conflict: target='a', source='b'"],
              merged_result: 'b',
            },
          }
        : branchRoutes()[u] || { status: 'ok', branches: [], count: 0 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = await mountWorktrees();
    const modal = await openMergeModal(wrapper);
    await mergeSaveButton(modal).click();
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('.wt-notice').exists()).toBe(true);
    expect(wrapper.text()).toContain('Merge completed with conflicts');
    expect(wrapper.text()).toContain("Result conflict");
    wrapper.unmount();
  });

  it('keeps the merge modal open and shows the error when the merge fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/worktree/merge')) {
        return Promise.resolve({
          ok: false, status: 404,
          json: () => Promise.resolve({ status: 'error', error: 'Source branch not found' }),
          text: () => Promise.resolve('{"status":"error"}'),
        });
      }
      const body = branchRoutes()[u] || { status: 'ok', branches: [], count: 0 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = await mountWorktrees();
    const modal = await openMergeModal(wrapper);
    await mergeSaveButton(modal).click();
    await flushPromises();
    await flushPromises();

    // The modal stays open so the user can correct the reference…
    expect(document.querySelector('.modal-overlay')).toBeTruthy();
    // …and the failure is reported instead of a silent "ok".
    expect(wrapper.text()).toContain('Failed to load branches');
    expect(wrapper.text()).toContain('/api/worktree/merge: 404');
    wrapper.unmount();
  });
});