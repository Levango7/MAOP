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

  it('renders running jobs when /api/control/status returns jobs', async () => {
    mockFetch(defaultRoutes({
      '/api/control/status': {
        jobs: [
          { id: 'j1', name: 'backup', status: 'running', started_at: '2026-08-20T10:00:00Z' },
          { id: 'j2', name: 'index', status: 'paused', started_at: '2026-08-20T10:01:00Z' },
        ],
      },
    }));
    const wrapper = await mountControlPanel();
    const rows = wrapper.findAll('.row-item');
    expect(rows.length).toBeGreaterThanOrEqual(2);
    expect(wrapper.text()).toContain('backup');
    expect(wrapper.text()).toContain('index');
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