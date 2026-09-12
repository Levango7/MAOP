// Tests for RoutingRules.vue — routing decision traces and statistics page.
//
// RoutingRules.onMounted calls loadAll() which hits
// /api/routing/decisions/recent?limit=100 and /api/routing/decisions/stats
// via Promise.allSettled. We mock global.fetch, stub PageHeader, then assert
// the root renders, tabs switch, decisions list displays, trace modal opens,
// and stats chart renders.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import RoutingRules from '../views/RoutingRules.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('RoutingRules.vue', () => {
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
      '/api/routing/decisions/recent?limit=100': { decisions: [], count: 0, total: 0 },
      '/api/routing/decisions/stats': { total: 0, by_stage: {}, last_24h: 0 },
      ...overrides,
    };
  }

  async function mountRouting() {
    const wrapper = mount(RoutingRules, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the routing-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRouting();
    expect(wrapper.find('.routing-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders recent decisions tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRouting();
    expect(wrapper.find('.routing-view').exists()).toBe(true);
    // Empty state shown when no decisions
    expect(wrapper.text()).toContain('No routing decisions recorded');
    wrapper.unmount();
  });

  it('renders decision rows when /api/routing/decisions/recent returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/routing/decisions/recent?limit=100': {
        decisions: [
          { trace_id: 'trace-1', stage: 'route_scorer', agent_id: 'claude', model: 'gpt-4', score: 0.92, timestamp: '2026-09-12T10:00:00Z' },
          { trace_id: 'trace-2', stage: 'load_balancer', agent_id: 'codex', model: 'auto', score: 0.85, timestamp: '2026-09-12T10:01:00Z' },
        ],
        count: 2,
        total: 2,
      },
    }));
    const wrapper = await mountRouting();
    const rows = wrapper.findAll('.dec-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('claude');
    expect(wrapper.text()).toContain('codex');
    wrapper.unmount();
  });

  it('renders stats overview when /api/routing/decisions/stats returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/routing/decisions/stats': {
        total: 150,
        by_stage: { route_scorer: 60, load_balancer: 40, model_selector: 30, dispatcher: 20 },
        last_24h: 25,
      },
    }));
    const wrapper = await mountRouting();
    // Switch to stats tab (index 1)
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.find('.stats-overview').exists()).toBe(true);
    expect(wrapper.find('.stats-chart__svg').exists()).toBe(true);
    // Total = 150
    expect(wrapper.text()).toContain('150');
    wrapper.unmount();
  });

  it('opens trace detail modal when view-trace button clicked', async () => {
    const traceId = 'trace-modal-1';
    mockFetch(defaultRoutes({
      '/api/routing/decisions/recent?limit=100': {
        decisions: [
          { trace_id: traceId, stage: 'route_scorer', agent_id: 'claude', score: 0.9, timestamp: '2026-09-12T10:00:00Z' },
        ],
        count: 1,
        total: 1,
      },
      [`/api/routing/decisions/${traceId}`]: {
        trace_id: traceId,
        decisions: [
          { trace_id: traceId, stage: 'route_scorer', agent_id: 'claude', model: 'gpt-4', timestamp: '2026-09-12T10:00:00Z' },
          { trace_id: traceId, stage: 'dispatcher', agent_id: 'claude', timestamp: '2026-09-12T10:00:01Z' },
        ],
        count: 2,
        stages: ['route_scorer', 'dispatcher'],
      },
    }));
    const wrapper = await mountRouting();
    // Click the view-trace button (icon-btn in the row)
    const traceBtn = wrapper.find('.dec-table__row .icon-btn');
    expect(traceBtn.exists()).toBe(true);
    await traceBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    // Trace chain steps should be rendered
    const steps = document.querySelectorAll('.trace-step');
    expect(steps.length).toBe(2);
    wrapper.unmount();
  });

  it('does not crash when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountRouting();
    expect(wrapper.find('.routing-view').exists()).toBe(true);
    // Error state shown for recent tab
    expect(wrapper.text()).toContain('Failed to load recent decisions');
    wrapper.unmount();
  });

  it('displays stage badge with correct label for each decision', async () => {
    mockFetch(defaultRoutes({
      '/api/routing/decisions/recent?limit=100': {
        decisions: [
          { trace_id: 't1', stage: 'route_scorer', agent_id: 'a1', timestamp: '2026-09-12T10:00:00Z' },
          { trace_id: 't2', stage: 'dispatcher', agent_id: 'a2', timestamp: '2026-09-12T10:01:00Z' },
        ],
        count: 2,
        total: 2,
      },
    }));
    const wrapper = await mountRouting();
    const text = wrapper.text();
    expect(text).toContain('Route Scorer');
    expect(text).toContain('Dispatcher');
    wrapper.unmount();
  });

  it('switches to stats tab and shows empty state when no stats data', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountRouting();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('No statistics available');
    wrapper.unmount();
  });

  it('reloads data when refresh button is clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/routing/decisions/recent?limit=100': {
        decisions: [{ trace_id: 't1', stage: 'route_scorer', agent_id: 'a1', timestamp: '2026-09-12T10:00:00Z' }],
        count: 1,
        total: 1,
      },
    }));
    const wrapper = await mountRouting();
    const callsBefore = global.fetch.mock.calls.length;
    // Find the refresh button (contains "Refresh" text)
    const buttons = wrapper.findAll('button');
    const refreshBtn = buttons.find((b) => b.text().includes('Refresh'));
    expect(refreshBtn).toBeTruthy();
    await refreshBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });
});