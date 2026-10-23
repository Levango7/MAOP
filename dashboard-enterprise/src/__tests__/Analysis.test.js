// Smoke + contract tests for Analysis.vue — Data analysis dashboard page.
//
// The mocks below are the shapes the API actually sends (enveloped as
// `{status, data, meta}`), not the shapes the view used to wish for. The
// previous mocks invented `success_rate`, `cpu_pct`, `dimension`/`value` and
// `memory_consumers` — names the producer never sent — so the suite stayed
// green while the page rendered "0", "—" and an empty pie chart against a
// correct API.
//
// The Python counterpart is py/tests/test_analysis_router_contracts.py: it
// pins the producer's own vocabulary and asserts the view still reads it.
//
// Analysis.onMounted calls loadAll() which hits 6 endpoints via
// Promise.allSettled: /api/analysis/summary, /agent-efficiency,
// /task-trends, /resource-utilization, /cost-breakdown,
// /performance-bottlenecks.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Analysis from '../views/Analysis.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

const META = { date_from: '', date_to: '', generated_at: '', note: '' };

describe('Analysis.vue', () => {
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
      const base = u.split('?')[0];
      const body = routes[u] ?? routes[base] ?? { status: 'ok', data: {} };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  // The producer's real payloads — see py/maop/dashboard/services/data_service.py
  function producerRoutes(overrides = {}) {
    return {
      '/api/analysis/summary': {
        status: 'ok',
        data: {
          date_range: '7d',
          total_tasks: 150,
          success_rate_pct: 85.7,
          avg_latency_ms: 250,
          total_cost_usd: 12.34,
          total_calls: 42,
          total_tokens: 100000,
          active_agents: 5,
          sparkline: [{ date: '2026-09-10', count: 12 }],
          observability: {},
        },
        meta: META,
      },
      '/api/analysis/agent-efficiency': {
        status: 'ok',
        data: {
          agents: [
            { agent: 'coder', total_tasks: 50, successes: 47, failures: 3, success_rate_pct: 95.0, circuit_breaker: 'closed', tokens: 10000, cost_usd: 5.5, calls: 50, tasks_per_usd: 9.09, avg_latency_ms: 200 },
            { agent: 'reviewer', total_tasks: 30, successes: 26, failures: 4, success_rate_pct: 88.0, circuit_breaker: 'closed', tokens: 5000, cost_usd: 2.5, calls: 30, tasks_per_usd: 12, avg_latency_ms: 150 },
          ],
          total_cost_usd: 8.0,
          total_calls: 80,
        },
        meta: META,
      },
      '/api/analysis/task-trends': {
        status: 'ok',
        data: {
          granularity: 'day',
          series: [
            { bucket: '2026-09-10', success: 10, failure: 2, timeout: 1, total: 13, success_rate: 0.77, failure_rate: 0.15, timeout_rate: 0.08 },
            { bucket: '2026-09-11', success: 15, failure: 1, timeout: 0, total: 16, success_rate: 0.94, failure_rate: 0.06, timeout_rate: 0 },
          ],
          bucket_count: 2,
        },
        meta: META,
      },
      '/api/analysis/resource-utilization': {
        status: 'ok',
        data: {
          live: { cpu_percent: 42, memory_percent: 55, memory_used_mb: 512, memory_total_mb: 1024 },
          history: { 'system.cpu.percent': [{ timestamp: 1767225600, value: 12.5 }] },
          series: [
            { timestamp: 1767225600, cpu_percent: 12.5 },
            { timestamp: 1767225900, cpu_percent: 30 },
          ],
          db_pool: { max_size: 8, in_use: 3, available: 5 },
          cache: { hits: 9, misses: 1, evictions: 0, size: 10, max_size: 100, hit_rate: 0.9 },
        },
        meta: META,
      },
      '/api/analysis/cost-breakdown': {
        status: 'ok',
        data: {
          group_by: 'agent',
          breakdown: [
            { key: 'coder', cost_usd: 5.5, tokens: 10000, calls: 50, avg_cost_per_call: 0.11 },
            { key: 'reviewer', cost_usd: 2.5, tokens: 5000, calls: 30, avg_cost_per_call: 0.083 },
          ],
          trend: [{ date: '2026-09-10', cost_usd: 8.0 }],
          total_cost_usd: 8.0,
          total_tokens: 15000,
          total_calls: 80,
        },
        meta: META,
      },
      '/api/analysis/performance-bottlenecks': {
        status: 'ok',
        data: {
          slowest_endpoints: [
            { metric: 'http.request.duration', count: 4, sum_seconds: 20, avg_seconds: 5.0 },
          ],
          expensive_agents: [
            { agent: 'pricey', calls: 1, cost_usd: 100, tokens: 10 },
          ],
          top_memory_consumers: [
            { pid: 4242, name: 'worker-1.exe', memory_mb: 512, cpu_percent: 3.5 },
          ],
        },
        meta: META,
      },
      ...overrides,
    };
  }

  async function mountAnalysis() {
    const wrapper = mount(Analysis, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  async function openTab(wrapper, label) {
    const tabs = wrapper.findAll('.segmented__item');
    const tab = tabs.find((b) => b.text().includes(label));
    expect(tab, `tab ${label} not found`).toBeTruthy();
    await tab.trigger('click');
    await flushPromises();
  }

  it('renders the an-view root element', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the summary tab by default', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    // Summary is the default tab → the KPI card grid is on screen.
    expect(wrapper.find('.an-kpi-grid').exists()).toBe(true);
    expect(wrapper.text()).toContain('Total Tasks');
    wrapper.unmount();
  });

  it('renders KPI cards when summary endpoint returns data', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-kpi-grid').exists()).toBe(true);
    const kpis = wrapper.findAll('.an-kpi');
    expect(kpis.length).toBe(6);
    expect(wrapper.text()).toContain('150');
    wrapper.unmount();
  });

  it('shows the success rate the API sends (success_rate_pct)', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    // The producer sends a 0-100 percentage under `success_rate_pct`.
    // Reading `success_rate` rendered an em dash instead.
    expect(wrapper.text()).toContain('85.7%');
    expect(wrapper.text()).not.toContain('—');
    wrapper.unmount();
  });

  it('shows the token total the summary reports', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    expect(wrapper.text()).toContain('100.00k');
    wrapper.unmount();
  });

  it('renders agent efficiency table when agents endpoint returns data', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Agent Efficiency');
    expect(wrapper.text()).toContain('coder');
    expect(wrapper.text()).toContain('reviewer');
    expect(wrapper.text()).toContain('95.0%');
    expect(wrapper.text()).toContain('$5.50');
    wrapper.unmount();
  });

  it('renders trends chart when task-trends endpoint returns series', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Task Trends');
    expect(wrapper.find('.an-chart__svg').exists()).toBe(true);
    expect(wrapper.text()).toContain('2026-09-10');
    expect(wrapper.text()).toContain('77.0%');
    wrapper.unmount();
  });

  it('renders the live resource cards the API nests under live/db_pool/cache', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Resources');
    const cards = wrapper.findAll('.an-resource-card__value').map((c) => c.text());
    // CPU %, memory MB, DB pool in-use/max, cache hit rate.
    expect(cards).toContain('42%');
    expect(cards).toContain('512');
    expect(cards).toContain('3 / 8');
    expect(cards).toContain('90.0%');
    wrapper.unmount();
  });

  it('draws the CPU line from the chart-ready series', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Resources');
    const path = wrapper.find('.an-chart__line');
    expect(path.exists()).toBe(true);
    expect(path.attributes('d')).toBeTruthy();
    wrapper.unmount();
  });

  it('renders the cost table with the dimension and the dollar value', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Cost');
    expect(wrapper.text()).toContain('coder');
    expect(wrapper.text()).toContain('$5.50');
    expect(wrapper.text()).toContain('$2.50');
    wrapper.unmount();
  });

  it('renders the cost pie chart from the breakdown costs', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Cost');
    expect(wrapper.find('.an-cost-chart__svg').exists()).toBe(true);
    // Two groups with a non-zero cost → two slices.
    expect(wrapper.findAll('.an-cost-chart__slice').length).toBe(2);
    wrapper.unmount();
  });

  it('renders bottleneck tables when bottlenecks endpoint returns data', async () => {
    mockFetch(producerRoutes());
    const wrapper = await mountAnalysis();
    await openTab(wrapper, 'Bottlenecks');
    expect(wrapper.text()).toContain('Slowest Endpoints');
    expect(wrapper.text()).toContain('http.request.duration');
    expect(wrapper.text()).toContain('5000');
    expect(wrapper.text()).toContain('Most Expensive Agents');
    expect(wrapper.text()).toContain('pricey');
    expect(wrapper.text()).toContain('$100');
    expect(wrapper.text()).toContain('Top Memory Consumers');
    expect(wrapper.text()).toContain('worker-1.exe');
    expect(wrapper.text()).toContain('512');
    wrapper.unmount();
  });

  it('shows error state when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-view').exists()).toBe(true);
    // Error state shown for summary tab
    expect(wrapper.text()).toContain('Failed to load summary');
    wrapper.unmount();
  });

  it('does not crash when endpoints return empty data', async () => {
    mockFetch(producerRoutes({
      '/api/analysis/summary': { status: 'ok', data: {} },
      '/api/analysis/agent-efficiency': { status: 'ok', data: { agents: [], total_cost_usd: 0, total_calls: 0 } },
      '/api/analysis/task-trends': { status: 'ok', data: { granularity: 'day', series: [], bucket_count: 0 } },
      '/api/analysis/resource-utilization': { status: 'ok', data: {} },
      '/api/analysis/cost-breakdown': { status: 'ok', data: { breakdown: [], total_cost_usd: 0 } },
      '/api/analysis/performance-bottlenecks': { status: 'ok', data: { slowest_endpoints: [], expensive_agents: [], top_memory_consumers: [] } },
    }));
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-view').exists()).toBe(true);
    // All tabs should render without crash; summary shows empty state
    expect(wrapper.text()).toContain('No summary data available');
    wrapper.unmount();
  });
});
