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
    // execActions has 6 entries; each renders a .ctrl-btn
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
});