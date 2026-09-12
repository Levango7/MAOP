// Smoke tests for Plugins.vue — plugin management page.
//
// Plugins.onMounted calls loadAll() which hits /api/plugins via
// Promise.allSettled. We mock global.fetch, stub PageHeader
// (via ListPageLayout), then assert the root renders, tabs switch,
// plugin list displays, config modal opens, and bulk actions work.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Plugins from '../views/Plugins.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Plugins.vue', () => {
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
      '/api/plugins': { plugins: [] },
      ...overrides,
    };
  }

  async function mountPlugins() {
    const wrapper = mount(Plugins, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the plugins-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountPlugins();
    expect(wrapper.find('.plugins-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders plugin list tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountPlugins();
    expect(wrapper.find('.plugins-view').exists()).toBe(true);
    // Empty state shown when no plugins
    expect(wrapper.text()).toContain('No plugins installed');
    wrapper.unmount();
  });

  it('renders plugin rows when /api/plugins returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/plugins': {
        plugins: [
          { id: 'p1', name: 'logger', version: '1.0.0', state: 'started' },
          { id: 'p2', name: 'metrics', version: '2.1.0', state: 'stopped' },
        ],
      },
    }));
    const wrapper = await mountPlugins();
    const rows = wrapper.findAll('.pl-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('logger');
    expect(wrapper.text()).toContain('metrics');
    wrapper.unmount();
  });

  it('displays correct state badges for plugins', async () => {
    mockFetch(defaultRoutes({
      '/api/plugins': {
        plugins: [
          { id: 'p1', name: 'running-plug', state: 'started' },
          { id: 'p2', name: 'stopped-plug', state: 'stopped' },
          { id: 'p3', name: 'error-plug', state: 'error' },
        ],
      },
    }));
    const wrapper = await mountPlugins();
    const badges = wrapper.findAll('.badge');
    expect(badges[0].text()).toContain('Running');
    expect(badges[1].text()).toContain('Stopped');
    expect(badges[2].text()).toContain('Error');
    wrapper.unmount();
  });

  it('switches to discover tab when clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountPlugins();
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: list, discover → discover is index 1
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Discovered Plugins');
    wrapper.unmount();
  });

  it('opens config modal when config button clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/plugins': {
        plugins: [
          { id: 'p1', name: 'configurable', state: 'started', config: { key: 'value' } },
        ],
      },
    }));
    const wrapper = await mountPlugins();
    // Find the config button (gear icon)
    const configBtn = wrapper.find('button[aria-label="Configure"]');
    expect(configBtn.exists()).toBe(true);
    await configBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('renders discover list when discover API returns data', async () => {
    mockFetch({
      '/api/plugins': { plugins: [] },
      '/api/plugins/discover': {
        discovered: [
          { id: 'd1', name: 'new-plugin', version: '0.1.0' },
          { id: 'd2', name: 'another-plugin', version: '1.0.0' },
        ],
      },
    });
    const wrapper = await mountPlugins();
    // Switch to discover tab
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Click the scan button
    const scanBtn = wrapper.findAll('button').find((b) => b.text().includes('Scan Now'));
    expect(scanBtn).toBeTruthy();
    await scanBtn.trigger('click');
    await flushPromises();
    const items = wrapper.findAll('.dc-item');
    expect(items.length).toBe(2);
    expect(wrapper.text()).toContain('new-plugin');
    wrapper.unmount();
  });

  it('does not crash when API endpoint fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountPlugins();
    expect(wrapper.find('.plugins-view').exists()).toBe(true);
    // Error state shown for list tab
    expect(wrapper.text()).toContain('Failed to load plugins');
    wrapper.unmount();
  });

  it('renders bulk action buttons in list tab', async () => {
    mockFetch(defaultRoutes({
      '/api/plugins': {
        plugins: [{ id: 'p1', name: 'plug', state: 'started' }],
      },
    }));
    const wrapper = await mountPlugins();
    const buttons = wrapper.findAll('button');
    expect(buttons.some((b) => b.text().includes('Load All'))).toBe(true);
    expect(buttons.some((b) => b.text().includes('Start All'))).toBe(true);
    expect(buttons.some((b) => b.text().includes('Stop All'))).toBe(true);
    wrapper.unmount();
  });
});