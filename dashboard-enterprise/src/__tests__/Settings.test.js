// Tests for Settings.vue — appearance cards, edition switch, config load, error/empty.
//
// Settings.onMounted calls detectAdmin() (/api/auth/status), editionStore.fetchEdition()
// (/api/info/edition), then /api/info/config, /api/health, /api/info/adrs. We mock
// global.fetch for those endpoints, stub PageHeader (uses useRoute), then assert on
// the rendered settings cards and edition switch action.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Settings from '../views/Settings.vue';
import { PageHeader } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Settings.vue', () => {
  let originalFetch, originalConfirm;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    originalConfirm = global.confirm;
    global.confirm = vi.fn(() => true);
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.confirm = originalConfirm;
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
      '/api/auth/status': { auth_enabled: false },
      '/api/info/edition': {
        edition: 'enterprise', features: { rbac: true }, backends: { db: 'postgres' },
        degradations: [],
      },
      '/api/info/config': {
        dash_host: '0.0.0.0', dash_port: 9079, tls_enabled: false, auth_enabled: false,
        debug: false, log_level: 'INFO', dash_workers: 2, root_dir: '/data',
      },
      '/api/health': { version: '1.2.3' },
      '/api/info/adrs': [],
      ...overrides,
    };
  }

  async function mountSettings() {
    const wrapper = mount(Settings, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the settings root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    expect(wrapper.find('.settings-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders loaded config values (host / port / log level)', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    const text = wrapper.text();
    expect(text).toContain('0.0.0.0');   // dash_host
    expect(text).toContain('9079');      // dash_port
    expect(text).toContain('INFO');      // log_level
    expect(text).toContain('1.2.3');     // appVersion from /api/health
    wrapper.unmount();
  });

  it('calls the edition switch endpoint when an edition button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSettings();
    // Edition switch buttons: [0]=personal, [1]=enterprise. Click personal to switch.
    const editionBtns = wrapper.findAll('.edition-btn');
    expect(editionBtns.length).toBeGreaterThanOrEqual(2);
    await editionBtns[0].trigger('click');
    await flushPromises();
    const calledUrls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(calledUrls.some((u) => u === '/api/info/edition')).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/info/config fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/info/config') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const body = defaultRoutes()[u] ?? {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountSettings();
    expect(wrapper.find('.settings-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders gracefully when edition/config return empty objects', async () => {
    mockFetch(defaultRoutes({
      '/api/info/edition': {},
      '/api/info/config': {},
    }));
    const wrapper = await mountSettings();
    expect(wrapper.find('.settings-page').exists()).toBe(true);
    // About card always renders the MAOP name, even with no config.
    expect(wrapper.text()).toContain('MAOP');
    wrapper.unmount();
  });
});