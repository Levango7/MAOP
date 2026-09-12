// Smoke tests for AgentProxy.vue — agent proxy / bridge management page.
//
// AgentProxy.onMounted calls loadAll() which hits /api/bridge/adapters.
// We mock global.fetch, stub PageHeader (via ListPageLayout), then
// assert the root renders, tabs switch, adapter list displays,
// health check works, and call form functions.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import AgentProxy from '../views/AgentProxy.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('AgentProxy.vue', () => {
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
      '/api/bridge/adapters': { adapters: [], count: 0 },
      ...overrides,
    };
  }

  async function mountAgentProxy() {
    const wrapper = mount(AgentProxy, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the agentproxy-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgentProxy();
    expect(wrapper.find('.agentproxy-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders adapters tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgentProxy();
    expect(wrapper.find('.agentproxy-view').exists()).toBe(true);
    // Empty state shown when no adapters
    expect(wrapper.text()).toContain('No adapters registered');
    wrapper.unmount();
  });

  it('renders adapter rows when /api/bridge/adapters returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/bridge/adapters': {
        adapters: [
          { name: 'cli-bridge', status: 'healthy', latency_ms: 50, call_count: 10 },
          { name: 'api-bridge', status: 'unhealthy', latency_ms: null, call_count: 0 },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountAgentProxy();
    const rows = wrapper.findAll('.ap-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('cli-bridge');
    expect(wrapper.text()).toContain('api-bridge');
    wrapper.unmount();
  });

  it('displays correct status badges for adapters', async () => {
    mockFetch(defaultRoutes({
      '/api/bridge/adapters': {
        adapters: [
          { name: 'ok-adapter', status: 'healthy' },
          { name: 'bad-adapter', status: 'unhealthy' },
        ],
      },
    }));
    const wrapper = await mountAgentProxy();
    const badges = wrapper.findAll('.badge');
    expect(badges[0].text()).toContain('Healthy');
    expect(badges[1].text()).toContain('Unhealthy');
    wrapper.unmount();
  });

  it('switches to health tab when clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgentProxy();
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: adapters, health, call → health is index 1
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Health Check');
    wrapper.unmount();
  });

  it('switches to call tab when clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgentProxy();
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: adapters, health, call → call is index 2
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Proxy Call');
    expect(wrapper.find('.call-form').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders health overview when health API returns data', async () => {
    mockFetch({
      '/api/bridge/adapters': { adapters: [] },
      '/api/bridge/health': {
        health: {
          'adapter-1': { healthy: true, latency_ms: 30 },
          'adapter-2': { healthy: false, latency_ms: null },
          'adapter-3': { healthy: true, latency_ms: 100 },
        },
      },
    });
    const wrapper = await mountAgentProxy();
    // Switch to health tab
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Click run health check button
    const runBtn = wrapper.findAll('button').find((b) => b.text().includes('Run Health Check'));
    expect(runBtn).toBeTruthy();
    await runBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.health-overview').exists()).toBe(true);
    // 2 healthy, 1 unhealthy, 3 total
    expect(wrapper.text()).toContain('2');
    expect(wrapper.text()).toContain('1');
    expect(wrapper.text()).toContain('3');
    wrapper.unmount();
  });

  it('opens sync config modal when sync button clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/bridge/adapters': {
        adapters: [
          { name: 'sync-adapter', status: 'healthy', config: { timeout: 30 } },
        ],
      },
    }));
    const wrapper = await mountAgentProxy();
    const syncBtn = wrapper.find('button[aria-label="Sync Config"]');
    expect(syncBtn.exists()).toBe(true);
    await syncBtn.trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('does not crash when API endpoint fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountAgentProxy();
    expect(wrapper.find('.agentproxy-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Failed to load adapters');
    wrapper.unmount();
  });

  it('renders call form fields in call tab', async () => {
    mockFetch(defaultRoutes({
      '/api/bridge/adapters': {
        adapters: [{ name: 'test-adapter', status: 'healthy' }],
      },
    }));
    const wrapper = await mountAgentProxy();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Adapter');
    expect(wrapper.text()).toContain('Task');
    expect(wrapper.text()).toContain('Keyword arguments');
    // The adapter should appear in the select
    const options = wrapper.findAll('option');
    expect(options.some((o) => o.text() === 'test-adapter')).toBe(true);
    wrapper.unmount();
  });
});