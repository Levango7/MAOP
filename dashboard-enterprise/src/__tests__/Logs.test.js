// Tests for Logs.vue — log list, type filter, empty state, error handling.
//
// Logs.onMounted calls load() which hits /api/logs?type=all and /api/logs/analysis
// via Promise.allSettled. We mock global.fetch, stub PageHeader (uses useRoute),
// then assert on the rendered log lines and filter behaviour.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Logs from '../views/Logs.vue';
import { EmptyState } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Logs.vue', () => {
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
      '/api/logs?type=all': { logs: [] },
      '/api/logs/analysis': { total: 0, by_status: {}, by_agent: {}, error_patterns: [] },
      ...overrides,
    };
  }

  async function mountLogs() {
    const wrapper = mount(Logs, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the logs root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLogs();
    expect(wrapper.find('.logs-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders a log line per entry returned by the API', async () => {
    mockFetch(defaultRoutes({
      '/api/logs?type=all': {
        logs: [
          { ts: '2026-08-03T10:00:00Z', level: 'info', agent: 'claude', msg: 'started' },
          { ts: '2026-08-03T10:01:00Z', level: 'error', agent: 'codex', msg: 'failed to run' },
          { ts: '2026-08-03T10:02:00Z', level: 'warn', agent: 'gemini', msg: 'slow response' },
        ],
      },
      '/api/logs/analysis': { total: 3, by_status: { success: 2, failure: 1 }, by_agent: { claude: 1, codex: 1, gemini: 1 }, error_patterns: [] },
    }));
    const wrapper = await mountLogs();
    const lines = wrapper.findAll('.log-line');
    expect(lines).toHaveLength(3);
    expect(wrapper.text()).toContain('started');
    expect(wrapper.text()).toContain('failed to run');
    wrapper.unmount();
  });

  it('filters log lines by the search input', async () => {
    mockFetch(defaultRoutes({
      '/api/logs?type=all': {
        logs: [
          { ts: 't1', level: 'info', agent: 'claude', msg: 'alpha started' },
          { ts: 't2', level: 'info', agent: 'codex', msg: 'beta finished' },
        ],
      },
      '/api/logs/analysis': { total: 2, by_status: { success: 2 }, by_agent: { claude: 1, codex: 1 }, error_patterns: [] },
    }));
    const wrapper = await mountLogs();
    expect(wrapper.findAll('.log-line')).toHaveLength(2);
    await wrapper.find('.filter-input').setValue('alpha');
    await flushPromises();
    expect(wrapper.findAll('.log-line')).toHaveLength(1);
    expect(wrapper.text()).toContain('alpha started');
    wrapper.unmount();
  });

  it('shows empty state when no logs are returned', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountLogs();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/logs fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.startsWith('/api/logs?type=')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountLogs();
    expect(wrapper.find('.logs-view').exists()).toBe(true);
    wrapper.unmount();
  });
});