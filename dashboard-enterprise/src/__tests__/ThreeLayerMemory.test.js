// Smoke tests for ThreeLayerMemory.vue — memory stats, breakdown chips, search.
//
// ThreeLayerMemory.onMounted calls refreshAll() → loadStats() (/api/memory/stats)
// + runSearch() (/api/memory/search?q=...&topk=50). We mock global.fetch, stub
// PageHeader, then assert the root renders, layer cards are present, and search
// results populate.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import ThreeLayerMemory from '../views/ThreeLayerMemory.vue';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('ThreeLayerMemory.vue', () => {
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
      '/api/memory/stats': {
        total_entries: 0,
        total_traces: 0,
        total_trajectory_steps: 0,
        by_agent: {},
        by_topic: {},
        episodic_count: 0,
        episodic_by_agent: {},
        episodic_by_outcome: {},
      },
      '/api/memory/search?q=&topk=50': { results: [] },
      ...overrides,
    };
  }

  async function mountMemory() {
    const wrapper = mount(ThreeLayerMemory, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the memory-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMemory();
    expect(wrapper.find('.memory-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the three layer cards (working/episodic/semantic)', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMemory();
    const cards = wrapper.findAll('.layer-card');
    expect(cards).toHaveLength(3);
    wrapper.unmount();
  });

  it('renders the search bar with input and search button', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMemory();
    expect(wrapper.find('.search-bar').exists()).toBe(true);
    expect(wrapper.find('.search-bar input').exists()).toBe(true);
    expect(wrapper.find('.search-btn').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders memory entry cards when search returns results', async () => {
    mockFetch(defaultRoutes({
      '/api/memory/search?q=&topk=50': {
        results: [
          { id: 'm1', agent: 'claude', topic: 'coding', content: 'how to test', score: 0.95, timestamp: '2026-08-20T10:00:00Z' },
          { id: 'm2', agent: 'codex', topic: 'review', content: 'code review tips', score: 0.88, timestamp: '2026-08-20T11:00:00Z' },
        ],
      },
    }));
    const wrapper = await mountMemory();
    const entries = wrapper.findAll('.entry-card');
    expect(entries).toHaveLength(2);
    expect(wrapper.text()).toContain('how to test');
    wrapper.unmount();
  });

  it('renders topic chips when stats.by_topic is populated', async () => {
    mockFetch(defaultRoutes({
      '/api/memory/stats': {
        total_entries: 10,
        by_topic: { coding: 5, review: 3, deploy: 2 },
        by_agent: {},
        episodic_by_outcome: {},
      },
    }));
    const wrapper = await mountMemory();
    const chips = wrapper.findAll('.chip');
    expect(chips.length).toBeGreaterThanOrEqual(3);
    wrapper.unmount();
  });

  it('does not crash when /api/memory/stats fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/memory/stats') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountMemory();
    expect(wrapper.find('.memory-page').exists()).toBe(true);
    wrapper.unmount();
  });
});