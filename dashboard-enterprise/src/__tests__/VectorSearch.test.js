// Tests for VectorSearch.vue — query input, stats cards, results, empty/error.
//
// VectorSearch.onMounted hits /api/vector/stats and /api/vector/list. We mock
// global.fetch, stub PageHeader, then assert on the rendered stat grid, query
// input, result cards, and empty/error states.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import VectorSearch from '../views/VectorSearch.vue';
import { EmptyState } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('VectorSearch.vue', () => {
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
      '/api/vector/stats': { total_entries: 100, total_traces: 50, total_trajectory_steps: 200, by_agent: { claude: 1 } },
      '/api/vector/list': { vectors: [] },
      ...overrides,
    };
  }

  async function mountVectorSearch() {
    const wrapper = mount(VectorSearch, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the vector search root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountVectorSearch();
    expect(wrapper.find('.vs-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders stats cards from loaded stats', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountVectorSearch();
    expect(wrapper.find('.stat-grid').exists()).toBe(true);
    expect(wrapper.text()).toContain('100');
    wrapper.unmount();
  });

  it('renders result cards after a successful search', async () => {
    mockFetch(defaultRoutes({
      '/api/vector/search?q=test&topk=10': {
        results: [{ id: 'v1', score: 0.95, content: 'hello world', agent: 'claude' }],
      },
    }));
    const wrapper = await mountVectorSearch();
    await wrapper.find('.query-input').setValue('test');
    await wrapper.find('.btn--primary').trigger('click');
    await flushPromises();
    expect(wrapper.find('.result-card').exists()).toBe(true);
    expect(wrapper.text()).toContain('hello world');
    wrapper.unmount();
  });

  it('shows empty state when search returns no results', async () => {
    mockFetch(defaultRoutes({
      '/api/vector/search?q=test&topk=10': { results: [] },
    }));
    const wrapper = await mountVectorSearch();
    await wrapper.find('.query-input').setValue('test');
    await wrapper.find('.btn--primary').trigger('click');
    await flushPromises();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows inline error when stats API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/vector/stats') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountVectorSearch();
    expect(wrapper.find('.inline-error').exists()).toBe(true);
    wrapper.unmount();
  });
});