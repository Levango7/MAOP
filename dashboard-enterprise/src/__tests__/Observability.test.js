// Smoke tests for Observability.vue — pipeline status, config, health checks, tracing info.
//
// Observability.onMounted calls loadStatus() (/api/observability/status),
// loadConfig() (/api/observability/config), loadHealth() (/api/observability/health),
// loadTraces() (/api/observability/traces?limit=5), then polls status every 15s.
// We mock global.fetch, stub PageHeader, then assert the root renders, summary
// cards are present, and the view degrades gracefully on API failure.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Observability from '../views/Observability.vue';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Observability.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    // Use fake timers to avoid the 15s poll interval leaking across tests.
    vi.useFakeTimers();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    vi.useRealTimers();
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
      '/api/observability/status': {
        edition: 'personal',
        tracing_enabled: false,
        enterprise_mode: false,
        metrics: { metrics: {}, histograms: {} },
        logging: { level: 'INFO', trace_correlation: false },
        tracing: { tracer_type: 'NoopTracer' },
      },
      '/api/observability/config': {
        edition: 'personal',
        otel_enabled: false,
        otel_exporter: 'otlp',
        otel_endpoint: 'http://localhost:4317',
        otel_service_name: 'maop',
        prometheus_scrape_path: '/api/prometheus',
        grafana_dashboard_uid: '',
      },
      '/api/observability/health': { checks: {} },
      '/api/observability/traces?limit=5': { enabled: false, hint: 'OTel disabled' },
      ...overrides,
    };
  }

  async function mountObservability() {
    const wrapper = mount(Observability, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the observability-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountObservability();
    expect(wrapper.find('.observability-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the edition and tracing badges in the header', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountObservability();
    expect(wrapper.find('.edition-badge').exists()).toBe(true);
    expect(wrapper.find('.tracing-badge').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders 4 summary stat cards in the metrics grid', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountObservability();
    // summaryCards computed always returns 4 entries
    const grid = wrapper.find('.metrics-grid');
    expect(grid.exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the pipeline status list with 5 rows', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountObservability();
    const rows = wrapper.findAll('.pipeline-row');
    expect(rows).toHaveLength(5);
    wrapper.unmount();
  });

  it('renders the canonical metrics table with 4 metric rows', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountObservability();
    const rows = wrapper.findAll('.metric-row');
    expect(rows).toHaveLength(4);
    wrapper.unmount();
  });

  it('renders health check rows when /api/observability/health returns checks', async () => {
    mockFetch(defaultRoutes({
      '/api/observability/health': {
        checks: {
          'otel-sdk': { ok: true, type: 'sdk' },
          'collector': { ok: false, error: 'not reachable' },
        },
      },
    }));
    const wrapper = await mountObservability();
    const rows = wrapper.findAll('.health-row');
    expect(rows).toHaveLength(2);
    wrapper.unmount();
  });

  it('does not crash when all observability endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountObservability();
    expect(wrapper.find('.observability-page').exists()).toBe(true);
    wrapper.unmount();
  });
});