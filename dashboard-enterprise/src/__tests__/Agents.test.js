// Tests for Agents.vue — agent grid, status badges, repair action, refresh.
//
// Agents.onMounted calls loadAgents() (/api/agents + /api/agents/routes),
// loadDecisions() (/api/routing/decisions/recent), and detectAdmin()
// (/api/auth/status). We mock global.fetch, stub PageHeader (useRoute), then
// assert on the rendered agent cards and action button behaviour.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Agents from '../views/Agents.vue';
import { PageHeader } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Agents.vue', () => {
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
      '/api/auth/status': { auth_enabled: false },
      '/api/agents': [],
      '/api/agents/routes': [],
      '/api/routing/decisions/recent?limit=20': { decisions: [] },
      ...overrides,
    };
  }

  async function mountAgents() {
    const wrapper = mount(Agents, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the agents root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgents();
    expect(wrapper.find('.agents-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders an agent card per agent returned by the API', async () => {
    mockFetch(defaultRoutes({
      '/api/agents': [
        { name: 'claude', model: 'gpt-4', driver: 'cli', capabilities: ['code'], health: 'healthy', enabled: true },
        { name: 'codex', model: 'auto', driver: 'cli', capabilities: ['code', 'search'], health: 'healthy', enabled: true },
        { name: 'gemini', model: 'gemini-pro', driver: 'cli', capabilities: [], health: 'unhealthy', enabled: true },
      ],
    }));
    const wrapper = await mountAgents();
    const cards = wrapper.findAll('.agent-card');
    expect(cards).toHaveLength(3);
    expect(wrapper.text()).toContain('claude');
    expect(wrapper.text()).toContain('codex');
    expect(wrapper.text()).toContain('gemini');
    wrapper.unmount();
  });

  it('reflects agent health/enabled state in the status badge', async () => {
    mockFetch(defaultRoutes({
      '/api/agents': [
        { name: 'active-one', health: 'healthy', enabled: true, capabilities: [] },
        { name: 'disabled-one', enabled: false, capabilities: [] },
      ],
    }));
    const wrapper = await mountAgents();
    const text = wrapper.text();
    expect(text).toContain('active');     // agentStatus → 'active'
    expect(text).toContain('disabled');   // agentStatus → 'disabled'
    wrapper.unmount();
  });

  it('calls diagnose endpoint when repair button is clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/agents': [{ name: 'claude', health: 'unhealthy', enabled: true, capabilities: [] }],
    }));
    const wrapper = await mountAgents();
    const card = wrapper.find('.agent-card');
    const buttons = card.findAll('.act-btn');
    // Order in agent-actions: switchModel, healthCheck, repair, upgrade, memory, evolve, remove
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    await buttons[2].trigger('click');
    await flushPromises();
    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes('/api/agents/claude/diagnose'))).toBe(true);
    wrapper.unmount();
  });

  it('renders an empty grid when no agents are returned', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAgents();
    expect(wrapper.findAll('.agent-card')).toHaveLength(0);
    wrapper.unmount();
  });

  it('does not crash when /api/agents fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/agents') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountAgents();
    expect(wrapper.find('.agents-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('reloads agents when the header refresh button is clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/agents': [{ name: 'claude', enabled: true, capabilities: [] }],
    }));
    const wrapper = await mountAgents();
    const callsBefore = global.fetch.mock.calls.length;
    // Header has two btn-action: scan, refresh. Refresh is the second.
    const headerBtns = wrapper.findAll('.btn-action');
    await headerBtns[1].trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });
});