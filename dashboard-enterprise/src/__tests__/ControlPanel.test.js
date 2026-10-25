// Smoke tests for ControlPanel.vue — execution controls, maintenance actions,
// running jobs list, and agent upgrade panel.
//
// ControlPanel.onMounted calls refreshAll() → loadJobs() (/api/control/status)
// + checkUpgrade() (/api/agent/upgrade). We mock global.fetch, stub PageHeader
// (uses useRoute), then assert the root renders, action buttons are present,
// and the view degrades gracefully on API failure.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import ControlPanel from '../views/ControlPanel.vue';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('ControlPanel.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    // 固定语言：分享按钮的文案断言依赖它（"Share" vs "分享"）。
    localStorage.setItem('maop_locale', 'en');
    // DetailDrawer 用 <Teleport to="body">：抽屉内容不在 wrapper 里，
    // 必须查 document.body；同时清掉上一个用例残留的 teleport 节点。
    document.body.replaceChildren();
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
      '/api/control/status': { jobs: [] },
      '/api/agent/upgrade': { agents: [] },
      '/api/runs?limit=20': { status: 'ok', data: { items: [], total: 0 } },
      ...overrides,
    };
  }

  async function mountControlPanel() {
    const wrapper = mount(ControlPanel, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the control-panel root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountControlPanel();
    expect(wrapper.find('.control-panel').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders execution control buttons (run/pause/resume/stop)', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountControlPanel();
    // composer 的提交按钮 + execActions 5 个 + maintActions 6 个
    const ctrlBtns = wrapper.findAll('.ctrl-btn');
    expect(ctrlBtns.length).toBeGreaterThanOrEqual(6);
    wrapper.unmount();
  });

  it('submits the composer task (unicode) to /api/control/run', async () => {
    mockFetch(defaultRoutes({
      '/api/control/run': { status: 'ok', job_id: 'j9' },
    }));
    const wrapper = await mountControlPanel();
    const input = wrapper.find('#maop-task-input');
    expect(input.exists()).toBe(true);
    // The composer must stay usable while the page's background refresh runs.
    expect(input.attributes('disabled')).toBeUndefined();
    await input.setValue('重构 auth 模块，改用 JWT。');
    await wrapper.find('.task-composer__run').trigger('click');
    await flushPromises();
    const call = global.fetch.mock.calls.find(([url]) => String(url) === '/api/control/run');
    expect(call).toBeTruthy();
    expect(JSON.parse(call[1].body).task).toBe('重构 auth 模块，改用 JWT。');
    expect(wrapper.text()).toContain('j9');
    wrapper.unmount();
  });

  it('renders jobs using the backend id/task/start fields and opens detail', async () => {
    mockFetch(defaultRoutes({
      '/api/control/status': {
        jobs: [
          { id: 'j1', task: 'backup', status: 'completed', start: '2026-08-20T10:00:00Z' },
          { id: 'j2', action: 'pause', status: 'interrupted', start: '2026-08-20T10:01:00Z' },
        ],
      },
      '/api/control/jobs/j1': {
        job: { id: 'j1', task: 'backup', status: 'completed', stdout: 'persisted output' },
      },
    }));
    const wrapper = await mountControlPanel();
    const rows = wrapper.findAll('.row-item');
    expect(rows.length).toBeGreaterThanOrEqual(2);
    expect(wrapper.text()).toContain('backup');
    expect(wrapper.text()).toContain('2026-08-20T10:00:00Z');
    await rows.find((row) => row.text().includes('backup')).findAll('button')[0].trigger('click');
    await flushPromises();
    expect(global.fetch.mock.calls.some(([url]) => String(url) === '/api/control/jobs/j1')).toBe(true);
    wrapper.unmount();
  });

  it('loads the run timeline when the job carries a trace_id', async () => {
    mockFetch(defaultRoutes({
      '/api/control/run': { status: 'ok', job_id: 'j9' },
      '/api/control/jobs/j9': {
        job: { id: 'j9', task: 't', status: 'completed', trace_id: 'tr1', stdout: 'x' },
      },
      '/api/runs/tr1': {
        status: 'ok',
        data: {
          summary: { trace_id: 'tr1' },
          events: [
            { seq: 1, kind: 'run.step', agent: 'claude', status: 'ok', duration_ms: 1234,
              payload: { stdout: 'agent said done' } },
            { seq: 2, kind: 'run.verify', agent: 'claude', status: 'passed', duration_ms: 5,
              payload: { summary: '门禁全部通过' } },
          ],
          decisions: [],
        },
      },
    }));
    const wrapper = await mountControlPanel();
    await wrapper.find('#maop-task-input').setValue('重构 auth');
    await wrapper.find('.task-composer__run').trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.some(([url]) => String(url) === '/api/runs/tr1')).toBe(true);
    // DetailDrawer 内容 Teleport 到 body，wrapper.text() 看不见。
    const timeline = document.querySelector('.run-timeline');
    expect(timeline, 'teleported timeline missing').not.toBe(null);
    expect(timeline.textContent).toContain('agent said done');
    expect(timeline.textContent).toContain('1234ms');
    expect(timeline.textContent).toContain('门禁全部通过');
    wrapper.unmount();
  });

  it('renders agent upgrade rows when /api/agent/upgrade returns agents', async () => {
    mockFetch(defaultRoutes({
      '/api/agent/upgrade': {
        agents: [
          { name: 'claude', current: '1.0', latest: '1.1', status: 'upgrade-available' },
        ],
      },
    }));
    const wrapper = await mountControlPanel();
    expect(wrapper.text()).toContain('claude');
    wrapper.unmount();
  });

  it('lists recorded runs and opens a run timeline from the list', async () => {
    mockFetch(defaultRoutes({
      '/api/runs?limit=20': {
        status: 'ok',
        data: {
          items: [
            { trace_id: 'tr-a', task: '整理台账', agent: 'claude', steps: 2,
              duration_ms: 900, success: true, status: 'finished',
              started_at: '2026-10-20T10:00:00+00:00' },
            { trace_id: 'tr-b', task: '半路断掉的', agent: '', steps: 1,
              duration_ms: 0, success: null, status: 'incomplete',
              started_at: '2026-10-20T09:00:00+00:00' },
          ],
          total: 2,
        },
      },
      '/api/runs/tr-a': {
        status: 'ok',
        data: {
          summary: {
            trace_id: 'tr-a', task: '整理台账', agent: 'claude', status: 'finished',
            success: true, steps: 2, duration_ms: 900, verify_status: 'passed',
            started_at: '2026-10-20T10:00:00+00:00',
          },
          events: [
            { seq: 1, kind: 'run.step', agent: 'claude', status: 'ok', duration_ms: 900,
              payload: { stdout: '台账已归并' } },
          ],
          decisions: [],
        },
      },
    }));
    const wrapper = await mountControlPanel();
    // 作业列表为空 ⇒ 这两行只能来自运行账本
    const rows = wrapper.findAll('.row-item');
    expect(rows.length).toBe(2);
    expect(rows[0].text()).toContain('整理台账');
    expect(rows[0].find('.badge--success').exists()).toBe(true);
    // 没有 run.finished 的运行不能报成成功，也不能报成失败
    expect(rows[1].text()).toContain('半路断掉的');
    expect(rows[1].find('.badge--neutral').exists()).toBe(true);

    await rows[0].findAll('button')[0].trigger('click');
    await flushPromises();
    expect(global.fetch.mock.calls.some(([url]) => String(url) === '/api/runs/tr-a')).toBe(true);
    const timeline = document.querySelector('.run-timeline');
    expect(timeline, 'run timeline missing').not.toBe(null);
    expect(timeline.textContent).toContain('台账已归并');
    wrapper.unmount();
  });

  it('keeps the run card usable when the ledger is unreadable', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/runs?limit=20') {
        return Promise.resolve({
          ok: false, status: 500,
          json: () => Promise.resolve({}), text: () => Promise.resolve(''),
        });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = await mountControlPanel();
    expect(wrapper.findAll('.row-item').length).toBe(0);
    expect(wrapper.find('.control-panel').exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/control/status fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/control/status') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountControlPanel();
    expect(wrapper.find('.control-panel').exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/agent/upgrade fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/agent/upgrade') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountControlPanel();
    expect(wrapper.find('.control-panel').exists()).toBe(true);
    wrapper.unmount();
  });

  // ── 分享 ──────────────────────────────────────────────────────
  // 分享按钮调的是 POST /api/share，返回的 token 拼成完整链接给用户复制。
  // 这里断言两件事：确实发起了创建请求，且链接真的渲染出来 —— 只测前者会
  // 漏掉"链接拼错/没显示"这种功能看似成功实则不可用的情况。

  function shareRoutes() {
    return defaultRoutes({
      '/api/runs?limit=20': {
        status: 'ok',
        data: { items: [{ trace_id: 'tr1', steps: 2, duration_ms: 100 }], total: 1 },
      },
      '/api/runs/tr1': {
        status: 'ok',
        data: { summary: { trace_id: 'tr1' }, events: [], decisions: [] },
      },
      '/api/share': { status: 'ok', token: 'tok-abc' },
    });
  }

  // DetailDrawer 用 <Teleport to="body">，抽屉内容不在 wrapper 树里，
  // 必须从 document.body 查询 —— 与 AgentRegistry 等套件的做法一致。
  function bodyButtons() {
    return Array.from(document.body.querySelectorAll('button'));
  }

  function bodyText() {
    return document.body.textContent || '';
  }

  it('creates a share link and renders it in the drawer', async () => {
    mockFetch(shareRoutes());
    const wrapper = await mountControlPanel();

    // 打开 run 抽屉
    await wrapper.find('.row-item button').trigger('click');
    await flushPromises();

    const shareBtn = bodyButtons().find((b) => b.textContent.includes('Share'));
    expect(shareBtn).toBeTruthy();
    shareBtn.click();
    await flushPromises();

    const posted = global.fetch.mock.calls.some(
      ([url, init]) => String(url) === '/api/share' && (init?.method || 'GET').toUpperCase() === 'POST',
    );
    expect(posted).toBe(true);

    const input = document.body.querySelector('.run-share__input');
    expect(input).toBeTruthy();
    expect(input.value).toContain('/share/tok-abc');
    wrapper.unmount();
  });

  it('shows an error when share creation fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/share') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = shareRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountControlPanel();

    await wrapper.find('.row-item button').trigger('click');
    await flushPromises();
    bodyButtons().find((b) => b.textContent.includes('Share')).click();
    await flushPromises();

    expect(bodyText()).toContain('Failed to create a share link');
    expect(document.body.querySelector('.run-share__input')).toBeNull();
    wrapper.unmount();
  });
});