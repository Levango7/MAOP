// Smoke tests for McpManager.vue — MCP tools/servers/stats/concurrency management page.
//
// McpManager.onMounted calls loadAll() which hits /api/mcp/servers,
// /api/mcp/tools, /api/mcp/stats via Promise.allSettled. We mock global.fetch,
// stub PageHeader (via ListPageLayout), then assert the root renders, tabs
// switch, servers list displays, and CRUD modal opens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import McpManager from '../views/McpManager.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('McpManager.vue', () => {
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
      '/api/mcp/servers': { servers: [] },
      '/api/mcp/tools': { tools: [] },
      '/api/mcp/stats': { series: [], per_tool: [] },
      ...overrides,
    };
  }

  async function mountMcp() {
    const wrapper = mount(McpManager, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the mcp-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMcp();
    expect(wrapper.find('.mcp-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders servers tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMcp();
    // Servers tab is active by default → .srv-table or EmptyState present
    expect(wrapper.find('.mcp-view').exists()).toBe(true);
    // Empty state shown when no servers
    expect(wrapper.text()).toContain('No MCP servers configured');
    wrapper.unmount();
  });

  it('renders server rows when /api/mcp/servers returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/servers': {
        servers: [
          { id: 1, name: 'fetch-server', url: 'http://localhost:8080', status: 'connected', tool_count: 3, latency_ms: 120 },
          { id: 2, name: 'github-server', url: 'stdio://github', status: 'disconnected', tool_count: 0, latency_ms: null },
        ],
      },
    }));
    const wrapper = await mountMcp();
    const rows = wrapper.findAll('.srv-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('fetch-server');
    expect(wrapper.text()).toContain('github-server');
    wrapper.unmount();
  });

  it('renders tools list when /api/mcp/tools returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/tools': {
        tools: [
          { id: 't1', name: 'search', description: 'Search the web', server_name: 'fetch-server', call_count: 42, parameters: { query: 'string' } },
          { id: 't2', name: 'fetch', description: 'Fetch a URL', server_name: 'fetch-server', call_count: 10, parameters: null },
        ],
      },
    }));
    const wrapper = await mountMcp();
    // Switch to tools tab via Segmented
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: servers, tools, stats, concurrency → tools is index 1
    const toolsTab = segButtons[1];
    await toolsTab.trigger('click');
    await flushPromises();
    const toolItems = wrapper.findAll('.tool-item');
    expect(toolItems.length).toBe(2);
    expect(wrapper.text()).toContain('search');
    expect(wrapper.text()).toContain('fetch');
    wrapper.unmount();
  });

  it('renders stats overview when /api/mcp/stats returns series', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/stats': {
        series: [
          { ts: '2026-09-12T00:00:00Z', calls: 10, success_rate: 0.95, avg_latency_ms: 120 },
          { ts: '2026-09-12T01:00:00Z', calls: 20, success_rate: 0.98, avg_latency_ms: 110 },
        ],
        per_tool: [
          { name: 'search', calls: 30, success_rate: 0.97, avg_latency_ms: 115 },
        ],
      },
    }));
    const wrapper = await mountMcp();
    // Switch to stats tab (index 2)
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.find('.stats-overview').exists()).toBe(true);
    expect(wrapper.find('.stats-chart__svg').exists()).toBe(true);
    // Total calls = 10 + 20 = 30
    expect(wrapper.text()).toContain('30');
    wrapper.unmount();
  });

  it('renders concurrency table when tools exist', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/tools': {
        tools: [
          { id: 't1', name: 'search', server_name: 'fetch-server', concurrency_limit: 5 },
          { id: 't2', name: 'fetch', server_name: 'fetch-server', concurrency_limit: 0 },
        ],
      },
    }));
    const wrapper = await mountMcp();
    // Switch to concurrency tab (index 3)
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[3].trigger('click');
    await flushPromises();
    const rows = wrapper.findAll('.conc-table__row');
    expect(rows.length).toBe(2);
    wrapper.unmount();
  });

  it('opens add-server modal when Add Server button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMcp();
    // Find the Add Server button (in Card actions slot)
    const buttons = wrapper.findAll('button');
    const addBtn = buttons.find((b) => b.text().includes('Add Server'));
    expect(addBtn).toBeTruthy();
    await addBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('does not crash when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountMcp();
    expect(wrapper.find('.mcp-view').exists()).toBe(true);
    // Error state shown for servers tab
    expect(wrapper.text()).toContain('Failed to load servers');
    wrapper.unmount();
  });

  it('displays connected badge for connected servers', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/servers': {
        servers: [
          { id: 1, name: 'online-srv', url: 'http://x', status: 'connected', tool_count: 2, latency_ms: 50 },
          { id: 2, name: 'offline-srv', url: 'http://y', status: 'disconnected', tool_count: 0, latency_ms: null },
        ],
      },
    }));
    const wrapper = await mountMcp();
    const badges = wrapper.findAll('.badge');
    // First server badge should contain "Connected"
    expect(badges[0].text()).toContain('Connected');
    // Second server badge should contain "Disconnected"
    expect(badges[1].text()).toContain('Disconnected');
    wrapper.unmount();
  });
});