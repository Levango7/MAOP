// Tests for SkillMarket.vue — marketplace list / search / install actions,
// and the Coming Soon banner indicating planned feature.
//
// SkillMarket.onMounted calls load() → GET /api/mcp/marketplace/tools.
// Install posts to /api/mcp/marketplace/tools/{id}/install.
// We mock global.fetch, stub PageHeader, then assert on the rendered
// marketplace rows and the Coming Soon informational banner.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import SkillMarket from '../views/SkillMarket.vue';
import { EmptyState } from '../components/index.js';

// PageHeader calls useRoute() which needs a router context; stub it so we can
// mount the view without providing a full vue-router instance. ListPageLayout
// renders PageHeader internally, so the stub applies transitively.
const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('SkillMarket.vue', () => {
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
    global.fetch = vi.fn((url, opts) => {
      const u = String(url);
      const method = (opts && opts.method) || 'GET';
      const keyed = method.toUpperCase() + ' ' + u;
      const body = routes[keyed] ?? routes[u] ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultMarketRoutes(overrides = {}) {
    return {
      '/api/mcp/marketplace/tools': {
        tools: [
          { id: 'web-search', name: 'web-search', category: 'search', source: 'mcp', version: '1.2', installed: false },
          { id: 'code-linter', name: 'code-linter', category: 'code', source: 'mcp', version: '0.9', installed: false },
          { id: 'db-query', name: 'db-query', category: 'data', source: 'mcp', version: '2.0', installed: true },
        ],
      },
      ...overrides,
    };
  }

  async function mountMarket() {
    const wrapper = mount(SkillMarket, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── 1. 渲染根元素 ──
  it('renders the skill-market root element', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    expect(wrapper.find('.skill-market-page').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 2. Coming Soon banner（user#1 fix：仅空数据时降级显示）──
  it('hides the Coming Soon banner when tools are loaded, shows it only for empty data', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    // loaded marketplace (3 tools) → banner hidden
    expect(wrapper.findAll('.install-row').length).toBeGreaterThan(0);
    expect(wrapper.find('.coming-soon-banner').exists()).toBe(false);
    wrapper.unmount();

    // empty marketplace → banner shown as the degraded hint
    mockFetch({ '/api/mcp/marketplace/tools': { tools: [] } });
    const wrapper2 = await mountMarket();
    const banner = wrapper2.find('.coming-soon-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain('Coming Soon');
    expect(banner.text()).toContain('Planned');
    wrapper2.unmount();
  });

  // ── 3. 市场工具列表展示 ──
  it('loads and renders marketplace tools', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const rows = wrapper.findAll('.install-row');
    expect(rows).toHaveLength(3);
    expect(wrapper.text()).toContain('web-search');
    expect(wrapper.text()).toContain('code-linter');
    expect(wrapper.text()).toContain('db-query');
    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes('/api/mcp/marketplace/tools'))).toBe(true);
    wrapper.unmount();
  });

  // ── 4. 空状态正确显示 ──
  it('shows empty state when no tools are returned', async () => {
    mockFetch({ '/api/mcp/marketplace/tools': { tools: [] } });
    const wrapper = await mountMarket();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.findAll('.install-row')).toHaveLength(0);
    // Coming Soon banner 仍然显示
    expect(wrapper.find('.coming-soon-banner').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 5. 市场搜索筛选 ──
  it('filters tools by search query', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const searchInput = wrapper.find('.search-box__input');
    await searchInput.setValue('code');
    expect(wrapper.findAll('.install-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('code-linter');
    wrapper.unmount();
  });

  // ── 6. 市场分类筛选 ──
  it('filters tools by category', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const select = wrapper.find('.category-select');
    await select.setValue('search');
    expect(wrapper.findAll('.install-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('web-search');
    wrapper.unmount();
  });

  // ── 7. 一键安装 ──
  it('installs a tool via POST /api/mcp/marketplace/tools/{id}/install', async () => {
    mockFetch({
      ...defaultMarketRoutes(),
      'POST /api/mcp/marketplace/tools/web-search/install': { status: 'ok' },
    });
    const wrapper = await mountMarket();
    const rows = wrapper.findAll('.install-row');
    // web-search is the first row and not installed
    const installBtn = rows[0].find('button.btn--primary');
    await installBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes('/api/mcp/marketplace/tools/web-search/install'))).toBe(true);
    wrapper.unmount();
  });

  // ── 8. 已安装工具的安装按钮禁用 ──
  it('disables the install button for already-installed tools', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const rows = wrapper.findAll('.install-row');
    // db-query (third row) is already installed
    const installBtn = rows[2].find('button.btn--primary');
    expect(installBtn.attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });

  // ── 9. 加载失败显示错误态 ──
  it('shows error state when the marketplace API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/mcp/marketplace/tools')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') });
    });
    const wrapper = await mountMarket();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('500');
    // user#1 fix：错误态显示 EmptyState；Coming Soon banner 只在
    // "无数据且无错误"的降级场景显示，错误时不重复叠提示。
    expect(wrapper.find('.coming-soon-banner').exists()).toBe(false);
    wrapper.unmount();
  });
});