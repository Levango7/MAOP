// Smoke tests for Tools.vue — multi-tab tools page (skills/mcp/topology/routing/prompts/security).
//
// Tools.onMounted calls load() (hits /api/skills, /api/mcp, /api/routing,
// /api/prompts, /api/security/config via Promise.allSettled) and loadTopology()
// (/api/mcp/topology). We mock global.fetch, stub PageHeader (via ListPageLayout)
// and McpTopology, then assert the root renders and tabs are present.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Tools from '../views/Tools.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
      McpTopology: { name: 'McpTopology', template: '<div class="topo-stub">topology</div>' },
    },
  },
};

describe('Tools.vue', () => {
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
      '/api/skills': { skills: [] },
      '/api/mcp': { servers: [], server_count: 0, tool_count: 0, tools: [] },
      '/api/routing': { routes: [] },
      '/api/prompts': { prompts: [] },
      '/api/security/config': {},
      '/api/mcp/topology': { servers: [], tools: [], agents: [], edges: [] },
      ...overrides,
    };
  }

  async function mountTools() {
    const wrapper = mount(Tools, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the tools-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTools();
    expect(wrapper.find('.tools-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the skills tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTools();
    // Skills section has a .skills-head with filter + create/import buttons
    expect(wrapper.find('.skills-head').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders built-in skill fallback cards when /api/skills is empty', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountTools();
    // BUILTIN_SKILLS_FALLBACK has 8 entries → 8 .skill-card
    const cards = wrapper.findAll('.skill-card');
    expect(cards.length).toBeGreaterThanOrEqual(8);
    wrapper.unmount();
  });

  it('renders MCP servers list when /api/mcp returns servers', async () => {
    mockFetch(defaultRoutes({
      '/api/mcp': {
        servers: [
          { name: 'fetch', transport: 'stdio', url: '', enabled: true },
          { name: 'github', transport: 'http', url: 'http://x', enabled: false },
        ],
        server_count: 2,
        tool_count: 5,
        tools: [],
      },
    }));
    const wrapper = await mountTools();
    // MCP tab is v-show (always rendered, just hidden). .mcp-row should exist.
    const rows = wrapper.findAll('.mcp-row');
    expect(rows.length).toBeGreaterThanOrEqual(2);
    expect(wrapper.text()).toContain('fetch');
    expect(wrapper.text()).toContain('github');
    wrapper.unmount();
  });

  it('does not crash when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountTools();
    expect(wrapper.find('.tools-view').exists()).toBe(true);
    wrapper.unmount();
  });
});