// Tests for SkillEditor.vue + SkillMarket.vue — atomic skill composition,
// drag-to-add steps, parameter mapping, composite save flow, and the
// marketplace list / search / install actions.
//
// SkillEditor.onMounted calls load() → GET /api/evolution/skills.
// Save posts to /api/evolution/skills/composite.
// SkillMarket.onMounted calls load() → GET /api/mcp/marketplace/tools.
// Install posts to /api/mcp/marketplace/tools/{id}/install.
// We mock global.fetch, stub PageHeader (it depends on vue-router), then
// assert on the rendered composition area and marketplace rows.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import SkillEditor from '../views/SkillEditor.vue';
import SkillMarket from '../views/SkillMarket.vue';
import { EmptyState } from '../components/index.js';

// PageHeader calls useRoute() which needs a router context; stub it so we can
// mount the view without providing a full vue-router instance. ListPageLayout
// renders PageHeader internally, so the stub applies transitively.
const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('SkillEditor.vue', () => {
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

  function defaultRoutes(overrides = {}) {
    return {
      '/api/evolution/skills': {
        skills: [
          { id: 'search-web', name: 'search-web', description: 'Search the web for references.' },
          { id: 'analyze', name: 'analyze', description: 'Analyze gathered material.' },
          { id: 'report', name: 'report', description: 'Generate a structured report.' },
        ],
      },
      ...overrides,
    };
  }

  async function mountEditor() {
    const wrapper = mount(SkillEditor, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── 1. 渲染根元素 ──
  it('renders the skill-editor root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    expect(wrapper.find('.skill-editor-page').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 2. 原子技能列表加载 ──
  it('loads atomic skills from GET /api/evolution/skills', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const cards = wrapper.findAll('.atom-card');
    expect(cards).toHaveLength(3);
    expect(wrapper.text()).toContain('search-web');
    expect(wrapper.text()).toContain('analyze');
    expect(wrapper.text()).toContain('report');
    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes('/api/evolution/skills'))).toBe(true);
    wrapper.unmount();
  });

  // ── 3. 加载失败显示错误态 ──
  it('shows error state when the skills API fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/evolution/skills')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') });
    });
    const wrapper = await mountEditor();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('500');
    wrapper.unmount();
  });

  // ── 4. 加载空显示空态 ──
  it('shows empty state when no atomic skills are returned', async () => {
    mockFetch({ '/api/evolution/skills': { skills: [] } });
    const wrapper = await mountEditor();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.findAll('.atom-card')).toHaveLength(0);
    wrapper.unmount();
  });

  // ── 5. 点击原子卡片添加步骤 ──
  it('adds a step to the composition when an atom card is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    expect(wrapper.findAll('.step-item')).toHaveLength(0);
    const firstAtom = wrapper.find('.atom-card');
    await firstAtom.trigger('click');
    expect(wrapper.findAll('.step-item')).toHaveLength(1);
    expect(wrapper.text()).toContain('search-web');
    wrapper.unmount();
  });

  // ── 6. 拖拽 drop 添加步骤 ──
  it('adds a step via drag-and-drop onto the canvas', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    // Simulate the HTML5 DnD sequence: dragstart on the atom, drop on the canvas.
    await atomCards[1].trigger('dragstart');
    const canvas = wrapper.find('.composer__canvas');
    await canvas.trigger('drop');
    expect(wrapper.findAll('.step-item')).toHaveLength(1);
    expect(wrapper.text()).toContain('analyze');
    wrapper.unmount();
  });

  // ── 7. 步骤上移 ──
  it('moves a step up when the move-up button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click'); // search-web
    await atomCards[1].trigger('click'); // analyze
    const items = wrapper.findAll('.step-item');
    expect(items[0].text()).toContain('search-web');
    // move-up button is the first btn-icon in the step actions
    const moveUpBtn = items[1].findAll('.btn-icon')[0];
    await moveUpBtn.trigger('click');
    const reordered = wrapper.findAll('.step-item');
    expect(reordered[0].text()).toContain('analyze');
    expect(reordered[1].text()).toContain('search-web');
    wrapper.unmount();
  });

  // ── 8. 步骤下移 ──
  it('moves a step down when the move-down button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click'); // search-web
    await atomCards[1].trigger('click'); // analyze
    const items = wrapper.findAll('.step-item');
    const moveDownBtn = items[0].findAll('.btn-icon')[1];
    await moveDownBtn.trigger('click');
    const reordered = wrapper.findAll('.step-item');
    expect(reordered[0].text()).toContain('analyze');
    expect(reordered[1].text()).toContain('search-web');
    wrapper.unmount();
  });

  // ── 9. 删除步骤 ──
  it('removes a step when the remove button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click');
    await atomCards[1].trigger('click');
    expect(wrapper.findAll('.step-item')).toHaveLength(2);
    const items = wrapper.findAll('.step-item');
    // remove button is the third btn-icon (danger)
    const removeBtn = items[0].findAll('.btn-icon--danger')[0];
    await removeBtn.trigger('click');
    expect(wrapper.findAll('.step-item')).toHaveLength(1);
    expect(wrapper.text()).toContain('analyze');
    wrapper.unmount();
  });

  // ── 10. 选中步骤显示参数面板 ──
  it('selects a step and shows the parameter inspector', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click');
    // Inspector should now show the selected step name
    expect(wrapper.find('.inspector-body').exists()).toBe(true);
    expect(wrapper.text()).toContain('search-web');
    // Input/output mapping sections are present
    expect(wrapper.findAll('.map-section')).toHaveLength(2);
    wrapper.unmount();
  });

  // ── 11. 添加 input mapping ──
  it('adds an input mapping row when the add-mapping button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click');
    // First add-mapping button belongs to the input map section
    const addMappingBtns = wrapper.findAll('button.btn--ghost.btn--sm');
    expect(addMappingBtns.length).toBeGreaterThanOrEqual(2);
    await addMappingBtns[0].trigger('click');
    expect(wrapper.findAll('.map-row').length).toBeGreaterThanOrEqual(1);
    wrapper.unmount();
  });

  // ── 12. 打开保存抽屉 ──
  it('opens the save drawer when the save button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click');
    // Save button is the primary action in the header
    const saveBtn = wrapper.find('button.btn--primary');
    await saveBtn.trigger('click');
    await flushPromises();
    // DetailDrawer is teleported to body
    const drawer = document.querySelector('[role="dialog"]');
    expect(drawer).not.toBeNull();
    expect(drawer.textContent).toContain('Save');
    wrapper.unmount();
  });

  // ── 13. 保存复合 Skill ──
  it('saves the composite skill via POST /api/evolution/skills/composite', async () => {
    mockFetch({
      ...defaultRoutes(),
      'POST /api/evolution/skills/composite': { id: 'comp-1', status: 'ok' },
    });
    const wrapper = await mountEditor();
    const atomCards = wrapper.findAll('.atom-card');
    await atomCards[0].trigger('click');
    await atomCards[1].trigger('click');

    // Open save drawer
    await wrapper.find('button.btn--primary').trigger('click');
    await flushPromises();

    // Fill in the composite name (the drawer's text input)
    const drawer = document.querySelector('[role="dialog"]');
    const nameInput = drawer.querySelector('input[type="text"]');
    nameInput.value = 'research-pipeline';
    nameInput.dispatchEvent(new Event('input'));

    // Click the confirm button in the drawer footer (the last btn--primary in body)
    const footerBtns = drawer.querySelectorAll('button.btn--primary');
    const confirmBtn = footerBtns[footerBtns.length - 1];
    confirmBtn.click();
    await flushPromises();
    await flushPromises();

    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u.includes('/api/evolution/skills/composite'))).toBe(true);
    wrapper.unmount();
  });

  // ── 14. 无步骤时保存按钮禁用 ──
  it('disables the save button when there are no steps', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEditor();
    const saveBtn = wrapper.find('button.btn--primary');
    expect(saveBtn.attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });
});

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

  // ── 15. 市场渲染根元素 ──
  it('renders the skill-market root element', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    expect(wrapper.find('.skill-market-page').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 16. 市场工具列表展示 ──
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

  // ── 17. 市场搜索筛选 ──
  it('filters tools by search query', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const searchInput = wrapper.find('.search-box__input');
    await searchInput.setValue('code');
    expect(wrapper.findAll('.install-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('code-linter');
    wrapper.unmount();
  });

  // ── 18. 市场分类筛选 ──
  it('filters tools by category', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const select = wrapper.find('.category-select');
    await select.setValue('search');
    expect(wrapper.findAll('.install-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('web-search');
    wrapper.unmount();
  });

  // ── 19. 一键安装 ──
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

  // ── 20. 已安装工具的安装按钮禁用 ──
  it('disables the install button for already-installed tools', async () => {
    mockFetch(defaultMarketRoutes());
    const wrapper = await mountMarket();
    const rows = wrapper.findAll('.install-row');
    // db-query (third row) is already installed
    const installBtn = rows[2].find('button.btn--primary');
    expect(installBtn.attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });
});