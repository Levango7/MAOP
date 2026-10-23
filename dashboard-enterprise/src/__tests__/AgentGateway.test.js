// Tests for AgentGateway.vue — 模型授权网关页（权限规则 / 访问检查 / 使用量）。
//
// The fetch stubs mirror what the typed envelopes actually put on the wire,
// because three of this page's reads were previously aimed at keys the backend
// never sent:
//   * 访问检查读 data.result ?? data —— 决策在 data.decision 里，于是永远显示"拒绝";
//   * 用量表把 data.usage（{model: tokens} 映射）当数组遍历 —— Agent 列恒空；
//   * 配置表单读 GET /usage 的 data.config —— 该端点从不发这个键，而且当时根本
//     没有 GET /api/model-gateway/config。

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import AgentGateway from '../views/AgentGateway.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

const BASE_CONFIG = {
  default_policy: 'allow',
  permissions: [],
  global_daily_token_limit: 0,
  enable_quota_check: true,
};

describe('AgentGateway.vue', () => {
  let originalFetch;
  let calls;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    calls = [];
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    document.body.replaceChildren();
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url, opts = {}) => {
      const u = String(url);
      calls.push({ url: u, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
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
      '/api/model-gateway/permissions': { status: 'ok', permissions: [], count: 0, error: '' },
      '/api/model-gateway/usage': { status: 'ok', usage: {}, total_tokens: 0, rows: [], error: '' },
      '/api/model-gateway/config': { status: 'ok', config: { ...BASE_CONFIG }, error: '' },
      '/api/model-gateway/check': { status: 'ok', decision: null, error: '' },
      ...overrides,
    };
  }

  async function mountView(overrides = {}) {
    mockFetch(defaultRoutes(overrides));
    const wrapper = mount(AgentGateway, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  function callsFor(method, urlPrefix) {
    return calls.filter((c) => c.method === method && c.url.startsWith(urlPrefix));
  }

  // ── Permissions ──────────────────────────────────────────────────

  it('shows the empty state when no permission rules exist', async () => {
    const wrapper = await mountView();
    expect(wrapper.find('.agentgateway-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('No permission rules');
    wrapper.unmount();
  });

  it('renders one row per rule with the real pattern and priority', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/permissions': {
        status: 'ok',
        permissions: [
          { model_pattern: 'gpt-4*', allowed: true, max_tokens_per_call: 0, daily_token_limit: 1000, priority: 10 },
          { model_pattern: 'claude-*', allowed: false, max_tokens_per_call: 200, daily_token_limit: 0, priority: 99 },
        ],
        count: 2,
        error: '',
      },
    });
    const rows = wrapper.findAll('.gw-table__row');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain('gpt-4*');
    expect(rows[0].text()).toContain('Unlimited'); // max_tokens_per_call = 0
    expect(rows[0].text()).toContain('10');
    expect(rows[1].text()).toContain('claude-*');
    expect(rows[1].text()).toContain('200');
    expect(rows[1].text()).toContain('99');
    wrapper.unmount();
  });

  it('edits a renamed rule against its ORIGINAL pattern, not the new one', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/permissions': {
        status: 'ok',
        permissions: [
          { model_pattern: 'old-*', allowed: true, max_tokens_per_call: 0, daily_token_limit: 0, priority: 10 },
        ],
        count: 1,
        error: '',
      },
    });
    await wrapper.findAll('.icon-btn')[0].trigger('click');
    await flushPromises();
    // 弹窗 Teleport 到 body，只能从 document 里取
    const patternInput = document.querySelector('.modal input[type=text]');
    expect(patternInput.value).toBe('old-*');
    patternInput.value = 'new-*';
    patternInput.dispatchEvent(new Event('input', { bubbles: true }));
    const saveBtn = [...document.querySelectorAll('.modal__foot button')].find((b) => b.textContent.includes('Save'));
    saveBtn.click();
    await flushPromises();
    await flushPromises();
    const put = callsFor('PUT', '/api/model-gateway/permissions/');
    expect(put).toHaveLength(1);
    // URL 定位旧规则；body 带新名字。反过来（都用新名字）会把改名做成新增，旧规则残留。
    expect(put[0].url).toBe('/api/model-gateway/permissions/old-*');
    expect(put[0].body.model_pattern).toBe('new-*');
    wrapper.unmount();
  });

  it('deletes a rule by its pattern', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const wrapper = await mountView({
      '/api/model-gateway/permissions': {
        status: 'ok',
        permissions: [
          { model_pattern: 'gpt-4*', allowed: true, max_tokens_per_call: 0, daily_token_limit: 0, priority: 100 },
        ],
        count: 1,
        error: '',
      },
    });
    await wrapper.findAll('.icon-btn')[1].trigger('click');
    await flushPromises();
    const del = callsFor('DELETE', '/api/model-gateway/permissions/');
    expect(del).toHaveLength(1);
    expect(del[0].url).toBe('/api/model-gateway/permissions/gpt-4*');
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  // ── Access check ─────────────────────────────────────────────────

  it('reads the decision out of data.decision and shows the matched rule', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/check': {
        status: 'ok',
        decision: {
          allowed: true,
          model: 'gpt-4o',
          reason: 'matched rule: gpt-4* (allowed=True)',
          matched_permission: { model_pattern: 'gpt-4*', allowed: true, max_tokens_per_call: 0, daily_token_limit: 1000, priority: 10 },
          max_tokens_per_call: 0,
          remaining_daily_tokens: 640,
        },
        error: '',
      },
    });
    await wrapper.find('.ac-form input[type=text]').setValue('gpt-4o');
    const checkBtn = wrapper.findAll('.ac-form button').find((b) => b.text().includes('Check'));
    await checkBtn.trigger('click');
    await flushPromises();
    const result = wrapper.find('.ac-result');
    expect(result.exists()).toBe(true);
    expect(result.text()).toContain('Allowed');
    expect(result.text()).toContain('gpt-4*');
    expect(result.text()).toContain('640');
    expect(result.text()).toContain('matched rule: gpt-4* (allowed=True)');
    wrapper.unmount();
  });

  it('renders -1 remaining as "Unlimited", not as a number', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/check': {
        status: 'ok',
        decision: {
          allowed: true, model: 'gpt-4o', reason: 'no matching rule; default_policy=allow',
          matched_permission: null, max_tokens_per_call: 0, remaining_daily_tokens: -1,
        },
        error: '',
      },
    });
    await wrapper.find('.ac-form input[type=text]').setValue('gpt-4o');
    const checkBtn = wrapper.findAll('.ac-form button').find((b) => b.text().includes('Check'));
    await checkBtn.trigger('click');
    await flushPromises();
    const result = wrapper.find('.ac-result');
    expect(result.text()).toContain('Unlimited');
    expect(result.text()).not.toContain('-1');
    expect(result.text()).toContain('No matching rule (default policy applies)');
    wrapper.unmount();
  });

  it('does not claim a decision when the backend reports an error', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/check': { status: 'error', decision: null, error: 'Check failed' },
    });
    await wrapper.find('.ac-form input[type=text]').setValue('gpt-4o');
    const checkBtn = wrapper.findAll('.ac-form button').find((b) => b.text().includes('Check'));
    await checkBtn.trigger('click');
    await flushPromises();
    expect(wrapper.find('.ac-result').exists()).toBe(false);
    expect(wrapper.find('.ac-err').text()).toContain('Check failed');
    wrapper.unmount();
  });

  // ── Usage ────────────────────────────────────────────────────────

  it('renders usage rows including the agent column', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/usage': {
        status: 'ok',
        usage: { 'gpt-4': 400 },
        total_tokens: 400,
        rows: [
          { model: 'gpt-4', agent: 'coder', tokens: 300 },
          { model: 'gpt-4', agent: 'reviewer', tokens: 100 },
        ],
        error: '',
      },
    });
    const rows = wrapper.findAll('[role="table"][aria-label="Today\'s Usage"] .gw-table__row');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain('coder');
    expect(rows[0].text()).toContain('300');
    expect(rows[1].text()).toContain('reviewer');
    expect(rows[1].text()).toContain('100');
    wrapper.unmount();
  });

  it('shows "—" for usage recorded without an agent', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/usage': {
        status: 'ok', usage: { 'gpt-4': 42 }, total_tokens: 42,
        rows: [{ model: 'gpt-4', agent: '', tokens: 42 }], error: '',
      },
    });
    const rows = wrapper.findAll('[role="table"][aria-label="Today\'s Usage"] .gw-table__row');
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain('—');
    wrapper.unmount();
  });

  it('clears today\'s usage through DELETE /usage', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const wrapper = await mountView();
    const clearBtn = wrapper.findAll('button').find((b) => b.text().includes('Clear Today'));
    await clearBtn.trigger('click');
    await flushPromises();
    expect(callsFor('DELETE', '/api/model-gateway/usage')).toHaveLength(1);
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  // ── Config ───────────────────────────────────────────────────────

  it('hydrates the config form from GET /api/model-gateway/config', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/config': {
        status: 'ok',
        config: { ...BASE_CONFIG, global_daily_token_limit: 500000, default_policy: 'deny' },
        error: '',
      },
    });
    expect(wrapper.find('input[type=number]').element.value).toBe('500000');
    // 保存时把界面上的默认策略原样带上，证明两个字段都回填了
    const updateBtn = wrapper.findAll('button').find((b) => b.text().includes('Update Config'));
    await updateBtn.trigger('click');
    await flushPromises();
    const put = callsFor('PUT', '/api/model-gateway/config');
    expect(put[0].body.default_policy).toBe('deny');
    expect(put[0].body.global_daily_token_limit).toBe(500000);
    wrapper.unmount();
  });

  it('sends only the two form fields on config save — the PUT is a merge', async () => {
    const wrapper = await mountView();
    const updateBtn = wrapper.findAll('button').find((b) => b.text().includes('Update Config'));
    await updateBtn.trigger('click');
    await flushPromises();
    const put = callsFor('PUT', '/api/model-gateway/config');
    expect(put).toHaveLength(1);
    // 带上 permissions 就是整体替换；后端每次保存都会重建权限表。
    expect(Object.keys(put[0].body).sort()).toEqual(['default_policy', 'global_daily_token_limit']);
    wrapper.unmount();
  });

  it('re-reads the effective config after a save', async () => {
    const wrapper = await mountView({
      '/api/model-gateway/config': {
        status: 'ok',
        config: { ...BASE_CONFIG, global_daily_token_limit: 500000 },
        error: '',
      },
    });
    const limitInput = wrapper.find('input[type=number]');
    await limitInput.setValue('999');
    const updateBtn = wrapper.findAll('button').find((b) => b.text().includes('Update Config'));
    await updateBtn.trigger('click');
    await flushPromises();
    // PUT 的回包才是生效配置：这里仍是 500000，界面必须跟着真值走，而不是本地草稿
    expect(limitInput.element.value).toBe('500000');
    wrapper.unmount();
  });

  it('surfaces a config load failure instead of showing defaults silently', async () => {
    global.fetch = vi.fn((url, opts = {}) => {
      const u = String(url);
      calls.push({ url: u, method: opts.method || 'GET', body: null });
      if (u === '/api/model-gateway/config') {
        return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: 'gateway down' }) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') });
    });
    const wrapper = mount(AgentGateway, mountOptions);
    await flushPromises();
    await flushPromises();
    expect(wrapper.find('.cfg-err').exists()).toBe(true);
    wrapper.unmount();
  });
});
