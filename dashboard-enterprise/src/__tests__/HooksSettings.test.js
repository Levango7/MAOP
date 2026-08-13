// Tests for Settings.vue Hook 管理 Tab（任务199）— 列表渲染、新建表单、编辑/删除操作。
//
// Settings.onMounted 会调用 detectAdmin() (/api/auth/status)、editionStore.fetchEdition()
// (/api/info/edition)、/api/info/config、/api/health、/api/info/adrs、
// /api/config/history、/api/hooks、/api/hooks/events。我们 mock global.fetch
// 让所有端点返回空数据，再切到 hooks tab 进行交互测试。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Settings from '../views/Settings.vue';


const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Settings.vue Hook 管理 Tab', () => {
  let originalFetch, originalConfirm, originalAlert;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    originalConfirm = global.confirm;
    originalAlert = global.alert;
    global.confirm = vi.fn(() => true);
    global.alert = vi.fn();
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.confirm = originalConfirm;
    global.alert = originalAlert;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      const method = (init && init.method) || 'GET';
      // POST/PUT/DELETE 路由匹配
      if (method !== 'GET') {
        for (const key of Object.keys(routes)) {
          if (key.startsWith(method + ' ') && u === key.slice(method.length + 1)) {
            const body = routes[key];
            return Promise.resolve({
              ok: true, status: 200,
              json: () => Promise.resolve(typeof body === 'function' ? body(init) : body),
              text: () => Promise.resolve(JSON.stringify(body)),
            });
          }
        }
      }
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
      '/api/info/edition': { edition: 'enterprise', features: {}, backends: {}, degradations: [] },
      '/api/info/config': {},
      '/api/health': { version: '1.0.0' },
      '/api/info/adrs': [],
      '/api/config/history?limit=100': { history: [] },
      '/api/hooks': { hooks: [], count: 0 },
      '/api/hooks/events': { events: [
        { name: 'loop.complete', phase: 'complete', domain: 'loop' },
        { name: 'agent.pre_dispatch', phase: 'pre_dispatch', domain: 'agent' },
      ], count: 2 },
      ...overrides,
    };
  }

  async function mountSettings() {
    const wrapper = mount(Settings, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  function makeHook(overrides = {}) {
    return {
      id: 'hk-001',
      name: 'my-hook',
      event: 'loop.complete',
      url: 'https://example.com/hook',
      method: 'POST',
      headers: {},
      enabled: true,
      timeout: 10,
      retry_count: 0,
      ...overrides,
    };
  }

  // ── Tab 切换 ─────────────────────────────────────────────────────
  it('renders the Hook Management tab button', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    // 三个 tab: config / history / hooks
    expect(tabs.length).toBe(3);
    wrapper.unmount();
  });

  it('shows hooks panel when hooks tab is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    // 切到 hooks tab
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    expect(wrapper.find('.hooks-panel').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 列表渲染 ─────────────────────────────────────────────────────
  it('renders hook list table with loaded hooks', async () => {
    const hook = makeHook();
    mockFetch(defaultRoutes({
      '/api/hooks': { hooks: [hook], count: 1 },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 表格存在
    expect(wrapper.find('.hooks-table').exists()).toBe(true);
    // 显示 hook 名称和事件
    expect(wrapper.text()).toContain('my-hook');
    expect(wrapper.text()).toContain('loop.complete');
    expect(wrapper.text()).toContain('https://example.com/hook');
    wrapper.unmount();
  });

  it('shows empty state when no hooks', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    expect(wrapper.find('.hooks-empty').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 新建 Hook 表单 ──────────────────────────────────────────────
  it('opens create dialog when New Hook button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 点击新建按钮
    const createBtn = wrapper.find('.hooks-btn.primary');
    expect(createBtn.exists()).toBe(true);
    await createBtn.trigger('click');
    await flushPromises();
    // dialog 应该打开
    expect(wrapper.find('.hooks-modal').exists()).toBe(true);
    expect(wrapper.find('.hooks-modal-head h3').text()).toContain('Create');
    wrapper.unmount();
  });

  it('submits create form and calls POST /api/hooks', async () => {
    mockFetch(defaultRoutes({
      'POST /api/hooks': { id: 'hk-new', name: 'test', event: 'loop.complete', url: 'https://x.com/h' },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 打开新建 dialog
    await wrapper.find('.hooks-btn.primary').trigger('click');
    await flushPromises();
    // 填表
    const inputs = wrapper.findAll('.hooks-form-input');
    // inputs[0]=name, inputs[1]=event(select), inputs[2]=url, inputs[3]=method(select), inputs[4]=headers, inputs[5]=timeout, inputs[6]=retry
    await inputs[0].setValue('test-hook');
    await inputs[2].setValue('https://example.com/new');
    // 选择事件
    await inputs[1].setValue('loop.complete');
    // 点击保存
    const saveBtn = wrapper.findAll('.hooks-modal-foot .hooks-btn').find(b => b.classes().includes('primary'));
    await saveBtn.trigger('click');
    await flushPromises();
    // 验证 POST /api/hooks 被调用
    const postCalls = global.fetch.mock.calls.filter(c => c[1] && c[1].method === 'POST' && String(c[0]) === '/api/hooks');
    expect(postCalls.length).toBeGreaterThanOrEqual(1);
    wrapper.unmount();
  });

  // ── 编辑 Hook ───────────────────────────────────────────────────
  it('opens edit dialog with hook data when Edit button is clicked', async () => {
    const hook = makeHook();
    mockFetch(defaultRoutes({
      '/api/hooks': { hooks: [hook], count: 1 },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 点击编辑按钮
    const editBtn = wrapper.findAll('.hooks-action-btn').find(b => b.text().includes('Edit'));
    expect(editBtn).toBeDefined();
    await editBtn.trigger('click');
    await flushPromises();
    // dialog 应打开且为编辑模式
    expect(wrapper.find('.hooks-modal').exists()).toBe(true);
    expect(wrapper.find('.hooks-modal-head h3').text()).toContain('Edit');
    // 表单应预填 hook 数据
    const inputs = wrapper.findAll('.hooks-form-input');
    expect(inputs[0].element.value).toBe('my-hook');
    expect(inputs[2].element.value).toBe('https://example.com/hook');
    wrapper.unmount();
  });

  // ── 删除 Hook ───────────────────────────────────────────────────
  it('calls DELETE /api/hooks/{id} when Delete is confirmed', async () => {
    const hook = makeHook();
    mockFetch(defaultRoutes({
      '/api/hooks': { hooks: [hook], count: 1 },
      'DELETE /api/hooks/hk-001': { status: 'ok', removed: true },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 点击删除按钮
    const deleteBtn = wrapper.findAll('.hooks-action-btn').find(b => b.classes().includes('danger'));
    expect(deleteBtn).toBeDefined();
    await deleteBtn.trigger('click');
    await flushPromises();
    // confirm 已被调用
    expect(global.confirm).toHaveBeenCalled();
    // DELETE 请求已发出
    const deleteCalls = global.fetch.mock.calls.filter(c => c[1] && c[1].method === 'DELETE' && String(c[0]) === '/api/hooks/hk-001');
    expect(deleteCalls.length).toBe(1);
    wrapper.unmount();
  });

  // ── 启用/禁用切换 ──────────────────────────────────────────────
  it('toggles hook enabled state via enable/disable endpoint', async () => {
    const hook = makeHook({ enabled: true });
    mockFetch(defaultRoutes({
      '/api/hooks': { hooks: [hook], count: 1 },
      'POST /api/hooks/hk-001/disable': { status: 'ok' },
      'POST /api/hooks/hk-001/enable': { status: 'ok' },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 找到 toggle checkbox
    const checkbox = wrapper.find('.hooks-toggle input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);
    expect(checkbox.element.checked).toBe(true);
    // 取消勾选 → 调用 disable
    await checkbox.setValue(false);
    await flushPromises();
    const disableCalls = global.fetch.mock.calls.filter(c => c[1] && c[1].method === 'POST' && String(c[0]) === '/api/hooks/hk-001/disable');
    expect(disableCalls.length).toBe(1);
    wrapper.unmount();
  });

  // ── 事件类型下拉 ────────────────────────────────────────────────
  it('populates event select with options from /api/hooks/events', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 打开新建 dialog
    await wrapper.find('.hooks-btn.primary').trigger('click');
    await flushPromises();
    // 事件 select 应有 2 个 option（来自 mock）+ 1 个空选项
    const selects = wrapper.findAll('select.hooks-form-input');
    // selects[0]=event, selects[1]=method
    const eventSelect = selects[0];
    const options = eventSelect.findAll('option');
    expect(options.length).toBe(3);  // 1 placeholder + 2 events
    wrapper.unmount();
  });

  // ── 测试 Hook 触发 ──────────────────────────────────────────────
  it('calls POST /api/hooks/{id}/test when Test button is clicked', async () => {
    const hook = makeHook();
    mockFetch(defaultRoutes({
      '/api/hooks': { hooks: [hook], count: 1 },
      'POST /api/hooks/hk-001/test': { hook_id: 'hk-001', success: true, response: 'HTTP 200', duration_ms: 5 },
    }));
    const wrapper = await mountSettings();
    const tabs = wrapper.findAll('.settings-tab');
    await tabs[2].trigger('click');
    await flushPromises();
    // 点击测试按钮
    const testBtn = wrapper.findAll('.hooks-action-btn').find(b => b.text().includes('Test'));
    expect(testBtn).toBeDefined();
    await testBtn.trigger('click');
    await flushPromises();
    const testCalls = global.fetch.mock.calls.filter(c => c[1] && c[1].method === 'POST' && String(c[0]) === '/api/hooks/hk-001/test');
    expect(testCalls.length).toBe(1);
    // alert 应被调用显示结果
    expect(global.alert).toHaveBeenCalled();
    wrapper.unmount();
  });
});