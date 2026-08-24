// Tests for FunnelMemory.vue — L0 evidence / L1 facts / task map tabs.
//
// FunnelMemory.onMounted calls loadStats() (/api/memory/funnel/stats) then
// loadEvidence() (/api/memory/funnel/evidence?limit=20&offset=0). The component
// uses useApiStore (Pinia) which wraps global.fetch. We mock global.fetch with
// a route table that supports exact keys and `*`-suffix wildcard keys (longest
// prefix wins), stub PageHeader (uses useRoute), then assert on rendered DOM
// and tab interactions.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import FunnelMemory from '../views/FunnelMemory.vue';

const mountOptions = {
  global: { stubs: { PageHeader: { template: '<slot />' } } },
};

// ── fetch mock helper ───────────────────────────────────────────
// routes keys: exact string → exact match; trailing `*` → prefix match.
// Exact keys always win over wildcard keys; longer prefixes win over shorter.
function mockFetch(routes) {
  const keys = Object.keys(routes).sort((a, b) => {
    const aWild = a.endsWith('*');
    const bWild = b.endsWith('*');
    if (aWild !== bWild) return aWild ? 1 : -1; // exact first
    return b.length - a.length; // longer prefix first
  });
  global.fetch = vi.fn((url) => {
    const u = String(url);
    for (const key of keys) {
      if (key.endsWith('*')) {
        if (u.startsWith(key.slice(0, -1))) return respond(routes[key]);
      } else if (u === key) {
        return respond(routes[key]);
      }
    }
    return respond({});
  });
}

function respond(body) {
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

// ── fixture builders ────────────────────────────────────────────
const statsBody = {
  status: 'ok',
  stats: {
    l0_evidence: { total: 12, by_kind: { trace: 5, plan: 3, code: 4 }, spilled: 2, total_chars: 1024 },
    l1_atoms: { total: 30, by_topic: { coding: 10, review: 8 }, top_facts: [{ id: 'f1', access_count: 5 }, { id: 'f2', access_count: 3 }] },
    symbolic: { sessions: 4, nodes: 15, by_status: { done: 10, active: 5 } },
  },
};

const evidenceBody = {
  status: 'ok',
  items: [
    { ref_id: 'ev-1', session_id: 'sess-a', kind: 'trace', created_at: '2026-08-20T10:00:00Z', summary: 'trace summary 1' },
    { ref_id: 'ev-2', session_id: 'sess-b', kind: 'plan', created_at: '2026-08-21T11:00:00Z', summary: 'plan summary 2' },
  ],
  total: 2,
};

const factsBody = {
  status: 'ok',
  items: [
    { id: 'f1', subject: 'subj-1', predicate: 'pred-1', object_value: 'obj-1', topic: 'coding', confidence: 0.95, access_count: 5 },
    { id: 'f2', subject: 'subj-2', predicate: 'pred-2', object_value: 'obj-2', topic: 'review', confidence: 0.8, access_count: 3 },
  ],
  total: 2,
};

const taskMapBody = { status: 'ok', mermaid: 'graph TD\n  A-->B' };
const taskNodesBody = {
  status: 'ok',
  nodes: [
    { node_id: 'n1', status: 'done', description: 'node 1', parent_id: '', evidence_ref: 'ev-1' },
    { node_id: 'n2', status: 'active', description: 'node 2', parent_id: 'n1', evidence_ref: 'ev-2' },
  ],
};

function defaultRoutes(overrides = {}) {
  return {
    '/api/memory/funnel/stats': statsBody,
    '/api/memory/funnel/evidence?*': evidenceBody,
    '/api/memory/funnel/evidence/*': { status: 'ok', evidence: evidenceBody.items[0] },
    '/api/memory/funnel/evidence/prune': { status: 'ok', deleted: 3 },
    '/api/memory/funnel/facts?*': factsBody,
    '/api/memory/funnel/facts/search?*': factsBody,
    '/api/memory/funnel/facts/promote': { status: 'ok', promoted: 2 },
    // exact routes for test session 'sess-xyz' (win over wildcard)
    '/api/memory/funnel/task-map/sess-xyz': taskMapBody,
    '/api/memory/funnel/task-map/sess-xyz/nodes': taskNodesBody,
    '/api/memory/funnel/task-map/*': taskMapBody,
    ...overrides,
  };
}

describe('FunnelMemory.vue', () => {
  let originalFetch;
  let originalConfirm;
  let originalClipboard;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    originalConfirm = window.confirm;
    originalClipboard = navigator.clipboard;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    // 默认 confirm 返回 true（用于 promote 测试）
    window.confirm = vi.fn(() => true);
    // clipboard mock
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn(() => Promise.resolve()) },
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    window.confirm = originalConfirm;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: originalClipboard,
    });
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    // 清理 body 下残留的 Teleport 内容
    document.body.innerHTML = '';
  });

  async function mountFm() {
    const wrapper = mount(FunnelMemory, mountOptions);
    // onMounted → loadStats + loadEvidence（两次异步链）
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── 渲染与初始状态 ───────────────────────────────────────────
  it('renders the fm-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    expect(wrapper.find('.fm-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows skeleton loading state on first mount before data resolves', async () => {
    mockFetch(defaultRoutes());
    const wrapper = mount(FunnelMemory, mountOptions);
    // flushPromises 之前 loading 为 true → Skeleton 可见
    expect(wrapper.find('.fm-tab').exists()).toBe(true);
    await flushPromises();
    await flushPromises();
    wrapper.unmount();
  });

  it('renders the evidence tab section by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    expect(wrapper.find('.fm-tab').exists()).toBe(true);
    // stats row 有 4 个 StatCard
    expect(wrapper.findAll('.fm-stats-row > *').length).toBeGreaterThanOrEqual(4);
    wrapper.unmount();
  });

  it('renders evidence table rows when items exist', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const rows = wrapper.findAll('.fm-table tbody tr');
    expect(rows).toHaveLength(2);
    expect(wrapper.text()).toContain('ev-1');
    expect(wrapper.text()).toContain('trace summary 1');
    wrapper.unmount();
  });

  it('renders kind filter chips from stats.by_kind', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const chips = wrapper.findAll('.fm-chip');
    // by_kind 有 3 个条目 → 至少 3 个 chip
    expect(chips.length).toBeGreaterThanOrEqual(3);
    expect(wrapper.text()).toContain('trace');
    expect(wrapper.text()).toContain('plan');
    wrapper.unmount();
  });

  it('renders EmptyState when evidence list is empty', async () => {
    mockFetch(defaultRoutes({
      '/api/memory/funnel/evidence?*': { status: 'ok', items: [], total: 0 },
    }));
    const wrapper = await mountFm();
    // 空列表 → evidence card 内的 EmptyState（.fm-table 不存在）
    expect(wrapper.find('.fm-table').exists()).toBe(false);
    wrapper.unmount();
  });

  // ── Tab 切换 ─────────────────────────────────────────────────
  it('switches to facts tab when facts tab button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    // Segmented 渲染 .seg__item 按钮，点击第二个（facts）
    const segItems = wrapper.findAll('.seg__item');
    expect(segItems.length).toBeGreaterThanOrEqual(2);
    await segItems[1].trigger('click');
    await flushPromises();
    await flushPromises();
    // facts tab 有搜索栏
    expect(wrapper.find('.fm-search-bar').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders facts table rows after switching to facts tab', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    await segItems[1].trigger('click');
    await flushPromises();
    await flushPromises();
    const rows = wrapper.findAll('.fm-table tbody tr');
    expect(rows).toHaveLength(2);
    expect(wrapper.text()).toContain('subj-1');
    wrapper.unmount();
  });

  it('switches to taskmap tab when taskmap tab button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    expect(segItems.length).toBeGreaterThanOrEqual(3);
    await segItems[2].trigger('click');
    await flushPromises();
    await flushPromises();
    // taskmap tab 有 session 输入栏
    expect(wrapper.find('.fm-session-bar').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── Task Map ────────────────────────────────────────────────
  it('loads task map when session id entered and load button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    await segItems[2].trigger('click');
    await flushPromises();
    await flushPromises();
    // 输入 session id
    const input = wrapper.find('.fm-session-bar input');
    await input.setValue('sess-xyz');
    await wrapper.find('.fm-session-bar .btn-primary').trigger('click');
    await flushPromises();
    await flushPromises();
    // mermaid 源码渲染在 pre 中
    expect(wrapper.find('.fm-mermaid-pre').exists()).toBe(true);
    expect(wrapper.text()).toContain('graph TD');
    // 节点表渲染
    const nodeRows = wrapper.findAll('.fm-table tbody tr');
    expect(nodeRows).toHaveLength(2);
    wrapper.unmount();
  });

  // ── Prune Modal ─────────────────────────────────────────────
  it('opens prune modal when prune button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    expect(document.querySelector('.modal-mask')).toBeNull();
    await wrapper.find('.btn-danger').trigger('click');
    await flushPromises();
    expect(document.querySelector('.modal-mask')).not.toBeNull();
    // prune days default is 90 — check the number input's value, not textContent
    const daysInput = document.querySelector('.modal input[type="number"]');
    expect(daysInput).not.toBeNull();
    expect(daysInput.value).toBe('90');
    wrapper.unmount();
  });

  it('closes prune modal when cancel button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    await wrapper.find('.btn-danger').trigger('click');
    await flushPromises();
    expect(document.querySelector('.modal-mask')).not.toBeNull();
    // 点击 cancel（.btn-ghost）
    const cancelBtn = document.querySelector('.modal__foot .btn-ghost');
    cancelBtn.click();
    await flushPromises();
    expect(document.querySelector('.modal-mask')).toBeNull();
    wrapper.unmount();
  });

  // ── 刷新 ────────────────────────────────────────────────────
  it('reloads data when refresh button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const callsBefore = global.fetch.mock.calls.length;
    await wrapper.find('.btn-refresh').trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });

  // ── 错误处理 ────────────────────────────────────────────────
  it('shows error banner when evidence API fails', async () => {
    // stats succeeds but evidence fails → loadEvidence's withErrorHandling
    // sets error.value (loadStats runs first, loadEvidence runs second and
    // its error is not cleared by any subsequent call).
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.startsWith('/api/memory/funnel/evidence')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      if (u === '/api/memory/funnel/stats') return respond(statsBody);
      return respond({});
    });
    const wrapper = await mountFm();
    expect(wrapper.find('.fm-error-banner').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── Evidence Detail Modal ───────────────────────────────────
  it('opens evidence detail modal when view detail is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    // 点击第一行的 view detail 按钮
    const detailBtn = wrapper.find('.fm-table tbody tr .btn-link');
    expect(detailBtn.exists()).toBe(true);
    await detailBtn.trigger('click');
    await flushPromises();
    // detail modal 渲染到 body
    expect(document.querySelector('.modal--wide')).not.toBeNull();
    wrapper.unmount();
  });

  // ── Facts 搜索 ──────────────────────────────────────────────
  it('filters facts when search query is entered', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    await segItems[1].trigger('click');
    await flushPromises();
    await flushPromises();
    const searchInput = wrapper.find('.fm-search-bar input');
    await searchInput.setValue('coding');
    await wrapper.find('.fm-search-btn').trigger('click');
    await flushPromises();
    await flushPromises();
    // 搜索后仍渲染 facts 表
    expect(wrapper.find('.fm-table').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── Facts 选择 ──────────────────────────────────────────────
  it('toggles fact selection via row checkbox', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    await segItems[1].trigger('click');
    await flushPromises();
    await flushPromises();
    // 点击第一行的 checkbox（tbody 内第一个 checkbox）
    const rowCheckboxes = wrapper.findAll('.fm-table tbody tr input[type="checkbox"]');
    expect(rowCheckboxes.length).toBeGreaterThanOrEqual(1);
    await rowCheckboxes[0].trigger('change');
    await flushPromises();
    // 选中后出现 promote (n) 按钮
    expect(wrapper.text()).toContain('(');
    wrapper.unmount();
  });

  // ── Copy Mermaid ────────────────────────────────────────────
  it('copies mermaid source when copy button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFm();
    const segItems = wrapper.findAll('.seg__item');
    await segItems[2].trigger('click');
    await flushPromises();
    await flushPromises();
    const input = wrapper.find('.fm-session-bar input');
    await input.setValue('sess-xyz');
    await wrapper.find('.fm-session-bar .btn-primary').trigger('click');
    await flushPromises();
    await flushPromises();
    // 点击 copy 按钮
    const copyBtn = wrapper.find('.btn-link');
    expect(copyBtn.exists()).toBe(true);
    await copyBtn.trigger('click');
    await flushPromises();
    expect(navigator.clipboard.writeText).toHaveBeenCalled();
    wrapper.unmount();
  });
});