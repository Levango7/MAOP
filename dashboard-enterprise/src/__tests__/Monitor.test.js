// Tests for Monitor.vue — metric cards, SSE indicator, tab switch, empty/error.
//
// Monitor.onMounted calls pollData() (/api/health + /api/live) and
// loadSystemStats() (/api/system/resources + /api/system/diagnostics). We mock
// global.fetch, stub PageHeader (useRoute), and drive the realtime store
// directly to test the connected/disconnected indicator.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Monitor from '../views/Monitor.vue';
import { useRealtimeStore } from '../stores/realtime.js';
import { EmptyState } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Monitor.vue', () => {
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
      '/api/health': { active_agents: 3 },
      '/api/live': {
        requests_per_min: 120, queue_depth: 5, cost_per_hour: 1.5,
        agents: [{ name: 'claude', healthy: true, queue: 2, load: 40 }],
      },
      '/api/system/resources': {
        memory_store: { pct: 0.5, used_mb: 100, total_mb: 200 },
        sqlite_db: { pct: 0.3, used_mb: 30, total_mb: 100 },
        vector_index: { pct: 0.2, used_mb: 10, total_mb: 50 },
        log_files: { pct: 0.1, used_mb: 5, total_mb: 50 },
      },
      '/api/system/diagnostics': { database: { ok: true, result: 'ok' } },
      ...overrides,
    };
  }

  async function mountMonitor() {
    const wrapper = mount(Monitor, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the monitor root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMonitor();
    expect(wrapper.find('.monitor-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders metric cards with loaded values', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMonitor();
    expect(wrapper.find('.metrics-grid').exists()).toBe(true);
    const text = wrapper.text();
    expect(text).toContain('120'); // requests_per_min
    expect(text).toContain('3');   // active_agents
    wrapper.unmount();
  });

  it('shows SSE disconnected indicator by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMonitor();
    expect(wrapper.find('.sse-indicator.off').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows SSE connected indicator when realtime store is connected', async () => {
    mockFetch(defaultRoutes());
    const realtime = useRealtimeStore();
    realtime.connected = true;
    const wrapper = await mountMonitor();
    expect(wrapper.find('.sse-indicator.on').exists()).toBe(true);
    wrapper.unmount();
  });

  it('switches to the maintenance tab on tab click', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMonitor();
    const tabBtns = wrapper.findAll('.tab-btn');
    expect(tabBtns.length).toBeGreaterThanOrEqual(2);
    await tabBtns[1].trigger('click');
    expect(wrapper.find('.maint-grid').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows empty agent-status state when live data has no agents', async () => {
    mockFetch(defaultRoutes({ '/api/live': { requests_per_min: 0, queue_depth: 0, cost_per_hour: 0 } }));
    const wrapper = await mountMonitor();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/health fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/health') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountMonitor();
    expect(wrapper.find('.monitor-page').exists()).toBe(true);
    wrapper.unmount();
  });
});