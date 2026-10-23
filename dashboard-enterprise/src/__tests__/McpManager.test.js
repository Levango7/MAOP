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
    // 弹窗是 Teleport 到 body 的，不清会污染下一个用例的 querySelector
    document.body.replaceChildren();
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
          { id: 'srv1', name: 'fetch-server', transport: 'streamable_http', status: 'connected',
            tools_count: 3, resources_count: 1, error: '', enabled: true, auto_connect: false,
            url: 'http://localhost:8080/mcp' },
          { id: 'srv2', name: 'github-server', transport: 'stdio', status: 'disconnected',
            tools_count: 0, resources_count: 0, error: 'boom', enabled: false, auto_connect: false,
            url: '' },
        ],
      },
      // Latency is not a server field: the row joins it from per-tool call stats.
      '/api/mcp/stats': {
        series: [{ ts: 1700000000, count: 5, errors: 1, avg_ms: 120 }],
        per_tool: [
          { tool: 'search', server_name: 'fetch-server', calls: 4, errors: 1,
            avg_ms: 100, max_ms: 250, error_rate: 0.25 },
          { tool: 'fetch', server_name: 'fetch-server', calls: 1, errors: 0,
            avg_ms: 200, max_ms: 200, error_rate: 0 },
        ],
        total: { calls: 5, errors: 1, avg_ms: 120 },
      },
    }));
    const wrapper = await mountMcp();
    const rows = wrapper.findAll('.srv-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('fetch-server');
    expect(wrapper.text()).toContain('github-server');
    // tools_count + url + joined latency (4*100 + 1*200)/5 = 120 ms
    expect(wrapper.text()).toContain('http://localhost:8080/mcp');
    expect(rows[0].text()).toContain('3');
    expect(rows[0].text()).toContain('120');
    wrapper.unmount();
  });

  it('renders tools list when /api/mcp/tools returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/tools': {
        tools: [
          { id: 'fetch-server:search', name: 'search', description: 'Search the web',
            server_name: 'fetch-server', concurrency_limit: 3,
            input_schema: { type: 'object', properties: { query: { type: 'string' } } } },
          { id: 'fetch-server:fetch', name: 'fetch', description: 'Fetch a URL',
            server_name: 'fetch-server', concurrency_limit: 0, input_schema: {} },
        ],
      },
      '/api/mcp/stats': {
        series: [],
        per_tool: [
          { tool: 'search', server_name: 'fetch-server', calls: 42, errors: 0,
            avg_ms: 10, max_ms: 20, error_rate: 0 },
        ],
        total: { calls: 42, errors: 0, avg_ms: 10 },
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
    // call_count joined from stats; the schema block reads input_schema
    expect(wrapper.text()).toContain('42');
    expect(wrapper.find('.tool-item__schema').text()).toContain('query');
    wrapper.unmount();
  });

  it('renders stats overview when /api/mcp/stats returns series', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/stats': {
        series: [
          { ts: 1700000000, count: 10, errors: 0, avg_ms: 120 },
          { ts: 1700003600, count: 20, errors: 1, avg_ms: 110 },
        ],
        per_tool: [
          { tool: 'search', server_name: 'fetch-server', calls: 30, errors: 1,
            avg_ms: 115, max_ms: 300, error_rate: 0.0333 },
        ],
        total: { calls: 30, errors: 1, avg_ms: 115 },
      },
    }));
    const wrapper = await mountMcp();
    // Switch to stats tab (index 2)
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.find('.stats-overview').exists()).toBe(true);
    expect(wrapper.find('.stats-chart__svg').exists()).toBe(true);
    // Total calls = 10 + 20 = 30; last bucket success = (20-1)/20 = 95.0%
    expect(wrapper.text()).toContain('30');
    expect(wrapper.text()).toContain('95.0');
    expect(wrapper.text()).toContain('110');
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

  it('loads editable server config before opening the edit modal', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/servers': {
        servers: [{ id: 's1', name: 'remote', transport: 'sse', status: 'disconnected' }],
      },
      '/api/mcp/servers/s1': {
        status: 'ok',
        server: {
          id: 's1', name: 'remote', transport: 'sse',
          url: 'https://mcp.example/sse', command: '', args: [],
          timeout_s: 12, auth_type: 'bearer', has_auth: true,
        },
      },
    }));
    const wrapper = await mountMcp();
    await wrapper.find('.srv-table__row .icon-btn').trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();
    const inputs = overlay.querySelectorAll('input');
    expect(inputs[0].value).toBe('remote');
    expect(inputs[1].value).toBe('https://mcp.example/sse');
    const selects = overlay.querySelectorAll('select');
    expect(selects[0].value).toBe('sse');
    expect(selects[1].value).toBe('bearer');
    // 超时以秒为单位，详情里是 12，不能再按毫秒渲染成 12000
    expect(overlay.querySelector('input[type="number"]').value).toBe('12');
    // 停用/自动连接开关回填自详情
    const checks = [...overlay.querySelectorAll('.check input[type="checkbox"]')];
    expect(checks.map((c) => c.checked)).toEqual([true, false]);
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

  it('shows a Disabled badge instead of Disconnected for a disabled server', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/servers': {
        servers: [{ id: 's1', name: 'paused', url: '', transport: 'stdio', status: 'disconnected', enabled: false }],
      },
    }));
    const wrapper = await mountMcp();
    expect(wrapper.text()).toContain('Disabled');
    expect(wrapper.text()).not.toContain('Disconnected');
    wrapper.unmount();
  });

  it('sends enabled and auto_connect when creating a server', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountMcp();
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('Add Server'));
    await addBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();
    const nameInput = overlay.querySelector('.field__input');
    nameInput.value = 'new-srv';
    nameInput.dispatchEvent(new Event('input'));
    const checks = [...overlay.querySelectorAll('.check input[type="checkbox"]')];
    expect(checks.length).toBe(2);
    checks[0].checked = false;
    checks[0].dispatchEvent(new Event('change'));
    checks[1].checked = true;
    checks[1].dispatchEvent(new Event('change'));
    await flushPromises();

    const saveBtn = [...overlay.querySelectorAll('button')].find((b) => /Save/i.test(b.textContent));
    expect(saveBtn).toBeTruthy();
    saveBtn.click();
    await flushPromises();
    await flushPromises();

    const post = global.fetch.mock.calls.find(
      ([u, init]) => String(u) === '/api/mcp/servers' && (init?.method || 'GET') === 'POST',
    );
    expect(post).toBeTruthy();
    const body = JSON.parse(post[1].body);
    expect(body.name).toBe('new-srv');
    expect(body.enabled).toBe(false);
    expect(body.auto_connect).toBe(true);
    wrapper.unmount();
  });

  it('prefills enablement toggles from the server detail', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp/servers': {
        servers: [{ id: 's1', name: 'svc', url: '', transport: 'stdio', status: 'disconnected' }],
      },
      '/api/mcp/servers/s1': {
        status: 'ok',
        server: {
          id: 's1', name: 'svc', transport: 'stdio', command: 'node', args: [],
          url: '', timeout_s: 12, enabled: false, auto_connect: true,
          auth_type: 'none', has_auth: false,
        },
      },
    }));
    const wrapper = await mountMcp();
    const editBtn = wrapper.findAll('button').find((b) => b.attributes('aria-label') === 'Edit');
    expect(editBtn).toBeTruthy();
    await editBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    const checks = [...overlay.querySelectorAll('.check input[type="checkbox"]')];
    expect(checks[0].checked).toBe(false);
    expect(checks[1].checked).toBe(true);
    wrapper.unmount();
  });
});