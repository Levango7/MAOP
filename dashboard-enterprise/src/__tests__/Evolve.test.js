// Tests for Evolve.vue — stat cards, trigger action, empty state, error handling.
//
// Evolve.onMounted calls loadStatus() which hits /api/evolve/status. The trigger
// button POSTs to /api/evolve/analyze then reloads status. We mock global.fetch,
// stub PageHeader (uses useRoute), then assert on the rendered stats and trigger.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Evolve from '../views/Evolve.vue';
import { EmptyState } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Evolve.vue', () => {
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
      '/api/evolve/status': {
        data: { stats: { by_agent: [] } },
      },
      '/api/evolve/analyze': { status: 'ok' },
      ...overrides,
    };
  }

  async function mountEvolve() {
    const wrapper = mount(Evolve, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the evolve root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvolve();
    expect(wrapper.find('.evolve-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders per-agent stats rows when status returns by_agent data', async () => {
    mockFetch(defaultRoutes({
      '/api/evolve/status': {
        data: {
          stats: {
            by_agent: [
              { agent: 'claude', total: 10, success: 8, fail: 2, rate: 80, avg_duration_ms: 500 },
              { agent: 'codex', total: 5, success: 5, fail: 0, rate: 100, avg_duration_ms: 300 },
            ],
          },
        },
      },
    }));
    const wrapper = await mountEvolve();
    const text = wrapper.text();
    expect(text).toContain('claude');
    expect(text).toContain('codex');
    // bestAgentLabel = "codex (100%)"
    expect(text).toContain('100%');
    wrapper.unmount();
  });

  it('POSTs to /api/evolve/analyze when the trigger button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvolve();
    const triggerBtn = wrapper.find('.btn-action');
    expect(triggerBtn.exists()).toBe(true);
    await triggerBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    const postCalls = global.fetch.mock.calls.filter((c) => c[1] && c[1].method === 'POST');
    expect(postCalls.some((c) => String(c[0]) === '/api/evolve/analyze')).toBe(true);
    wrapper.unmount();
  });

  it('shows empty state when no agent stats are returned', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvolve();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/evolve/status fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/evolve/status') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountEvolve();
    expect(wrapper.find('.evolve-page').exists()).toBe(true);
    wrapper.unmount();
  });
});