// Tests for Search.vue — search box, result list, empty state, error handling.
//
// Search.onMounted calls loadStats() which hits /api/memory/stats,
// /api/vector/stats, /api/graph/stats. We mock global.fetch for those
// endpoints, stub PageHeader (uses useRoute), then assert on the rendered
// search input, result table, and empty/error states.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Search from '../views/Search.vue';
import { PageHeader, EmptyState, DataTable } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Search.vue', () => {
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
      '/api/memory/stats': { total_entries: 10 },
      '/api/vector/stats': { total_entries: 20 },
      '/api/graph/stats': { nodes: 5, edges: 8 },
      ...overrides,
    };
  }

  async function mountSearch() {
    const wrapper = mount(Search, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the search root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSearch();
    expect(wrapper.find('.search-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the search input box', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSearch();
    expect(wrapper.find('.search-input').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders search results after a successful search', async () => {
    mockFetch(defaultRoutes({
      '/api/memory/search?q=test&topk=10': {
        results: [{ id: 'm1', agent: 'claude', task: 'task1', tags: 'tag1', score: 0.95 }],
      },
    }));
    const wrapper = await mountSearch();
    await wrapper.find('.search-input').setValue('test');
    await wrapper.find('.btn-primary').trigger('click');
    await flushPromises();
    expect(wrapper.findComponent(DataTable).exists()).toBe(true);
    expect(wrapper.text()).toContain('claude');
    wrapper.unmount();
  });

  it('shows empty state when search returns no results', async () => {
    mockFetch(defaultRoutes({
      '/api/memory/search?q=test&topk=10': { results: [] },
    }));
    const wrapper = await mountSearch();
    await wrapper.find('.search-input').setValue('test');
    await wrapper.find('.btn-primary').trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('No matches for “test”.');
    wrapper.unmount();
  });

  it('shows error card when search API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.startsWith('/api/memory/search')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountSearch();
    await wrapper.find('.search-input').setValue('test');
    await wrapper.find('.btn-primary').trigger('click');
    await flushPromises();
    expect(wrapper.find('.err').exists()).toBe(true);
    wrapper.unmount();
  });
});