// Smoke tests for Analysis.vue — Data analysis dashboard page.
//
// Analysis.onMounted calls loadAll() which hits 6 endpoints via
// Promise.allSettled: /api/analysis/summary, /agent-efficiency,
// /task-trends, /resource-utilization, /cost-breakdown,
// /performance-bottlenecks. We mock global.fetch, stub PageHeader,
// then assert the root renders, tabs switch, KPI cards display,
// agent table renders, and error state shows on API failure.

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

  function defaultRoutes(overrides = {}) {
    return {
      '/api/analysis/summary': { status: 'ok', data: {} },
      '/api/analysis/agent-efficiency': { status: 'ok', data: { agents: [], total_cost_usd: 0, total_calls: 0 } },
      '/api/analysis/task-trends': { status: 'ok', data: { series: [], granularity: 'day', bucket_count: 0 } },
      '/api/analysis/resource-utilization': { status: 'ok', data: {} },
      '/api/analysis/cost-breakdown': { status: 'ok', data: { items: [], total: 0 } },
      '/api/analysis/performance-bottlenecks': { status: 'ok', data: { slow_endpoints: [], expensive_agents: [], memory_consumers: [] } },
      ...overrides,
    };
  }

  async function mountAnalysis() {
    const wrapper = mount(Analysis, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the an-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders summary tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountAnalysis();
    // Summary tab is active by default → empty state shown when no data
    expect(wrapper.text()).toContain('No summary data available');
    wrapper.unmount();
  });

  it('renders KPI cards when summary endpoint returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/analysis/summary': {
        status: 'ok',
        data: {
          total_tasks: 150,
          success_rate: 0.95,
          total_cost_usd: 12.34,
          avg_latency_ms: 250,
          active_agents: 5,
          total_tokens: 100000,
        },
      },
    }));
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-kpi-grid').exists()).toBe(true);
    const kpis = wrapper.findAll('.an-kpi');
    expect(kpis.length).toBe(6);
    expect(wrapper.text()).toContain('150');
    wrapper.unmount();
  });

  it('renders agent efficiency table when agents endpoint returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/analysis/agent-efficiency': {
        status: 'ok',
        data: {
          agents: [
            { agent: 'coder', total_tasks: 50, success_rate_pct: 95, tokens: 10000, cost_usd: 5.5, avg_latency_ms: 200, tasks_per_usd: 9.09, circuit_breaker: 'closed' },
            { agent: 'reviewer', total_tasks: 30, success_rate_pct: 88, tokens: 5000, cost_usd: 2.5, avg_latency_ms: 150, tasks_per_usd: 12, circuit_breaker: 'closed' },
          ],
          total_cost_usd: 8.0,
          total_calls: 80,
        },
      },
    }));
    const wrapper = await mountAnalysis();
    // Switch to agents tab (index 1)
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: summary, agents, trends, resources, cost, bottlenecks
    // The first Segmented is date-range, the second is tabs
    // Find the agents tab button by its text
    const agentsTab = segButtons.find((b) => b.text().includes('Agent Efficiency'));
    expect(agentsTab).toBeTruthy();
    await agentsTab.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('coder');
    expect(wrapper.text()).toContain('reviewer');
    wrapper.unmount();
  });

  it('renders trends chart when task-trends endpoint returns series', async () => {
    mockFetch(defaultRoutes({
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
      },
    }));
    const wrapper = await mountAnalysis();
    const segButtons = wrapper.findAll('.segmented__item');
    const trendsTab = segButtons.find((b) => b.text().includes('Task Trends'));
    expect(trendsTab).toBeTruthy();
    await trendsTab.trigger('click');
    await flushPromises();
    expect(wrapper.find('.an-chart__svg').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders cost pie chart when cost-breakdown returns items', async () => {
    mockFetch(defaultRoutes({
      '/api/analysis/cost-breakdown': {
        status: 'ok',
        data: {
          items: [
            { dimension: 'coder', value: 5.5, tokens: 10000, calls: 50 },
            { dimension: 'reviewer', value: 2.5, tokens: 5000, calls: 30 },
          ],
          total: 8.0,
        },
      },
    }));
    const wrapper = await mountAnalysis();
    const segButtons = wrapper.findAll('.segmented__item');
    const costTab = segButtons.find((b) => b.text().includes('Cost'));
    expect(costTab).toBeTruthy();
    await costTab.trigger('click');
    await flushPromises();
    expect(wrapper.find('.an-cost-chart__svg').exists()).toBe(true);
    expect(wrapper.text()).toContain('coder');
    wrapper.unmount();
  });

  it('renders bottleneck tables when bottlenecks endpoint returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/analysis/performance-bottlenecks': {
        status: 'ok',
        data: {
          slow_endpoints: [{ name: '/api/slow', latency_ms: 5000 }],
          expensive_agents: [{ name: 'coder', cost_usd: 100 }],
          memory_consumers: [{ name: 'worker-1', memory_mb: 512 }],
        },
      },
    }));
    const wrapper = await mountAnalysis();
    const segButtons = wrapper.findAll('.segmented__item');
    const bottleneckTab = segButtons.find((b) => b.text().includes('Bottlenecks'));
    expect(bottleneckTab).toBeTruthy();
    await bottleneckTab.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Slowest Endpoints');
    expect(wrapper.text()).toContain('/api/slow');
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
    mockFetch(defaultRoutes());
    const wrapper = await mountAnalysis();
    expect(wrapper.find('.an-view').exists()).toBe(true);
    // All tabs should render without crash; summary shows empty state
    expect(wrapper.text()).toContain('No summary data available');
    wrapper.unmount();
  });
});