// Tests for Tasks.vue — 任务历史页 (P1-3)
//
// Tasks.onMounted 调用 loadTasks() 命中 GET /api/sessions。
// 我们 mock global.fetch, stub PageHeader/DetailDrawer (Teleport),
// 然后断言:
//   - 表格行渲染 (任务名/状态标签/时间/耗时)
//   - 搜索/状态过滤触发重新请求
//   - 分页按钮翻页
//   - 重跑确认对话框 → POST /api/sessions/{id}/rerun
//   - 错误态 / 空态 / 加载态

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Tasks from '../views/Tasks.vue';
import EmptyState from '../components/EmptyState.vue';
import Badge from '../components/Badge.vue';
import Skeleton from '../components/Skeleton.vue';

// PageHeader 调用 useRoute(), stub 掉以避免提供完整 router。
// DetailDrawer 使用 Teleport to body, stub 以便断言。需渲染 footer slot
// 以便测试点击确认按钮。
const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
      DetailDrawer: {
        template: '<div v-if="open" class="detail-drawer-stub"><slot /><div class="drawer-footer"><slot name="footer" /></div></div>',
        props: ['open', 'title', 'icon'],
      },
    },
  },
};

// ── 测试数据 ───────────────────────────────────────────────────
const SAMPLE_TASKS = [
  {
    id: 'sess-aaa001',
    agent: 'mavis',
    workdir: '/project-a',
    status: 'completed',
    tags: [],
    metadata: { task: 'Generate report' },
    token_count: 1000,
    token_budget: 5000,
    message_count: 5,
    created_at: '2026-08-14T10:00:00Z',
    updated_at: '2026-08-14T10:05:00Z',
    last_active_at: '2026-08-14T10:05:00Z',
  },
  {
    id: 'sess-bbb002',
    agent: 'nova',
    workdir: '/project-b',
    status: 'failed',
    tags: [],
    metadata: { task: 'Run tests' },
    token_count: 500,
    token_budget: 5000,
    message_count: 3,
    created_at: '2026-08-14T09:00:00Z',
    updated_at: '2026-08-14T09:02:30Z',
    last_active_at: '2026-08-14T09:02:30Z',
  },
  {
    id: 'sess-ccc003',
    agent: 'mavis',
    workdir: '/project-c',
    status: 'active',
    tags: [],
    metadata: { description: 'Health check' },
    token_count: 200,
    token_budget: 5000,
    message_count: 2,
    created_at: '2026-08-14T11:00:00Z',
    updated_at: '2026-08-14T11:00:10Z',
    last_active_at: '2026-08-14T11:00:10Z',
  },
];

describe('Tasks.vue', () => {
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
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      // 查找匹配的路由 (支持 URL 带 query string)
      const path = u.split('?')[0];
      const handler = routes[path];
      if (typeof handler === 'function') {
        return Promise.resolve(handler(u, init));
      }
      const body = handler ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountTasks() {
    const wrapper = mount(Tasks, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // ── 渲染: 表格行 ──
  it('renders task rows from /api/sessions', async () => {
    mockFetch({
      '/api/sessions': {
        items: SAMPLE_TASKS,
        total: 3,
        page: 1,
        limit: 20,
        total_pages: 1,
      },
    });
    const wrapper = await mountTasks();
    // 表格行: 3 条数据行
    const rows = wrapper.findAll('.tasks-table tbody tr');
    expect(rows.length).toBe(3);
    // 第一行任务名应来自 metadata.task
    expect(wrapper.find('.task-name').text()).toContain('Generate report');
  });

  // ── 状态标签: 颜色 tone ──
  it('renders status badges with correct tones', async () => {
    mockFetch({
      '/api/sessions': {
        items: SAMPLE_TASKS,
        total: 3,
        page: 1,
        limit: 20,
        total_pages: 1,
      },
    });
    const wrapper = await mountTasks();
    const badges = wrapper.findAllComponents(Badge);
    // completed → success, failed → fail, active → info
    const tones = badges.map((b) => b.props('tone'));
    expect(tones).toContain('success');
    expect(tones).toContain('fail');
    expect(tones).toContain('info');
  });

  // ── 空态 ──
  it('shows empty state when no tasks', async () => {
    mockFetch({
      '/api/sessions': { items: [], total: 0, page: 1, limit: 20, total_pages: 0 },
    });
    const wrapper = await mountTasks();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('No tasks yet');
  });

  // ── 加载态 ──
  it('shows skeleton during loading', async () => {
    // 用一个永不 resolve 的 fetch 制造持续 loading
    global.fetch = vi.fn(() => new Promise(() => {}));
    const wrapper = mount(Tasks, mountOptions);
    await flushPromises();
    expect(wrapper.findComponent(Skeleton).exists()).toBe(true);
  });

  // ── 错误态 ──
  it('shows error state on fetch failure', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }),
    );
    const wrapper = await mountTasks();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    expect(wrapper.text()).toContain('Failed to load tasks');
  });

  // ── 分页: 翻页触发新请求 ──
  it('paginates to next page on button click', async () => {
    let lastUrl = '';
    mockFetch({
      '/api/sessions': (url) => {
        lastUrl = url;
        const params = new URLSearchParams(url.split('?')[1] || '');
        const p = parseInt(params.get('page') || '1', 10);
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              items: p === 1 ? SAMPLE_TASKS : [SAMPLE_TASKS[0]],
              total: 25,
              page: p,
              limit: 20,
              total_pages: 2,
            }),
          text: () => Promise.resolve('{}'),
        };
      },
    });
    const wrapper = await mountTasks();
    // 初始在第 1 页
    expect(wrapper.find('.page-info').text()).toContain('1');
    expect(wrapper.find('.page-info').text()).toContain('2');

    // 点击下一页
    const nextBtn = wrapper.findAll('.tasks-pagination .act-btn')[1];
    await nextBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    // 请求 URL 应包含 page=2
    expect(lastUrl).toContain('page=2');
  });

  // ── 重跑: 确认对话框 → POST rerun ──
  it('opens rerun confirm dialog and posts to rerun endpoint', async () => {
    const postCalls = [];
    mockFetch({
      '/api/sessions': {
        items: SAMPLE_TASKS,
        total: 3,
        page: 1,
        limit: 20,
        total_pages: 1,
      },
      '/api/sessions/sess-aaa001/rerun': (url, init) => {
        postCalls.push({ url, init });
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              session: { ...SAMPLE_TASKS[0], id: 'sess-new001', status: 'active' },
              rerun_from: 'sess-aaa001',
            }),
          text: () => Promise.resolve('{}'),
        };
      },
    });
    const wrapper = await mountTasks();

    // 第一行的重跑按钮 (每行有 2 个 .act-btn.small: viewDetail + rerun)
    const firstRow = wrapper.find('.tasks-table tbody tr');
    const rerunBtn = firstRow.findAll('.act-btn.small')[1];
    await rerunBtn.trigger('click');
    await flushPromises();

    // 确认对话框应打开
    const drawer = wrapper.find('.detail-drawer-stub');
    expect(drawer.exists()).toBe(true);
    expect(drawer.text()).toContain('Generate report');

    // 点击确认按钮 (footer 中第一个 .act-btn)
    const confirmBtn = drawer.find('.act-btn');
    await confirmBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    // 应已发起 POST rerun 请求
    expect(postCalls.length).toBe(1);
    expect(postCalls[0].init.method).toBe('POST');
  });

  // ── 搜索: 触发重新请求 (防抖) ──
  it('reloads on search input change', async () => {
    const calls = [];
    mockFetch({
      '/api/sessions': (url) => {
        calls.push(url);
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              items: SAMPLE_TASKS,
              total: 3,
              page: 1,
              limit: 20,
              total_pages: 1,
            }),
          text: () => Promise.resolve('{}'),
        };
      },
    });
    const wrapper = await mountTasks();


    // 在搜索框输入
    const searchInput = wrapper.find('.filterbar__input');
    await searchInput.setValue('mavis');
    await searchInput.trigger('input');
    // 等待防抖 (300ms) — vi.useFakeTimers 会更精确, 这里用 flushPromises + setTimeout
    await new Promise((r) => setTimeout(r, 350));
    await flushPromises();
    await flushPromises();

    // 应发起新请求, URL 包含 search=mavis
    const lastCall = calls[calls.length - 1];
    expect(lastCall).toContain('search=mavis');
  });

  // ── 任务名兜底: metadata.task → agent → id ──
  it('falls back to agent name when metadata.task is absent', async () => {
    const noMeta = [
      {
        id: 'sess-fallback',
        agent: 'fallback-agent',
        workdir: '/',
        status: 'active',
        tags: [],
        metadata: {},
        token_count: 0,
        token_budget: 0,
        message_count: 0,
        created_at: '2026-08-14T12:00:00Z',
        updated_at: '2026-08-14T12:00:00Z',
        last_active_at: '2026-08-14T12:00:00Z',
      },
    ];
    mockFetch({
      '/api/sessions': { items: noMeta, total: 1, page: 1, limit: 20, total_pages: 1 },
    });
    const wrapper = await mountTasks();
    expect(wrapper.find('.task-name').text()).toContain('fallback-agent');
  });
});