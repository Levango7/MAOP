// Tests for Overview.vue — KPI grid, loading skeleton, error state, refresh.
//
// Overview.onMounted calls edition.fetchEdition() then load() which hits
// /api/overview and /api/info/activity. We mock global.fetch for those
// endpoints, stub PageHeader (uses useRoute) and Line (vue-chartjs canvas),
// then assert on the rendered DOM.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Overview from '../views/Overview.vue';
import { EmptyState } from '../components/index.js';

const mountOptions = {
  global: { stubs: { PageHeader: { template: '<slot />' }, Line: true } },
};

describe('Overview.vue', () => {
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

  const overviewData = {
    agents_total: 5, delegations_total: 120, success_rate: 98.5, avg_latency_ms: 150,
    tests_total: 80, modules_total: 12, code_lines: 5000, api_endpoints: 30,
    source_files: 40, test_files: 20, version: '1.0.0', uptime: '2d 3h',
    python_ver: '3.11.5', platform: 'linux', recent_delegations: [], fail_ranking: [],
    timeseries: [],
  };

  function defaultRoutes(overrides = {}) {
    return {
      '/api/info/edition': { edition: 'enterprise', features: {}, backends: {}, degradations: [] },
      '/api/overview': overviewData,
      '/api/info/activity?limit=8': { status: 'ok', events: [] },
      ...overrides,
    };
  }

  async function mountOverview() {
    const wrapper = mount(Overview, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the overview root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountOverview();
    expect(wrapper.find('.overview').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows skeleton loading state on first mount', async () => {
    mockFetch(defaultRoutes());
    const wrapper = mount(Overview, mountOptions);
    // Before flushPromises, loading is true → health skeleton visible
    expect(wrapper.find('.health-skel').exists()).toBe(true);
    await flushPromises();
    await flushPromises();
    wrapper.unmount();
  });

  it('renders key data points after load', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountOverview();
    const text = wrapper.text();
    expect(text).toContain('5');      // agents_total
    expect(text).toContain('98.5');   // success_rate
    expect(text).toContain('1.0.0');  // version
    expect(text).toContain('linux');  // platform
    wrapper.unmount();
  });

  it('handles empty data without crashing and shows chart EmptyState', async () => {
    mockFetch(defaultRoutes({ '/api/overview': {} }));
    const wrapper = await mountOverview();
    expect(wrapper.find('.overview').exists()).toBe(true);
    // Empty timeseries → EmptyState rendered for the throughput chart
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows error card when /api/overview fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u === '/api/overview') {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('{}') });
    });
    const wrapper = await mountOverview();
    expect(wrapper.find('.overview').exists()).toBe(true);
    expect(wrapper.text()).toContain('API /api/overview: 500');
    wrapper.unmount();
  });

  it('reloads data when refresh button is clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountOverview();
    const callsBefore = global.fetch.mock.calls.length;
    await wrapper.find('.refresh-btn').trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });
});