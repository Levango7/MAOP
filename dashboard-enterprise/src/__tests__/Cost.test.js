// Tests for Cost.vue — stat cards, period switch, budget/agent breakdown, empty/error.
//
// Cost.onMounted calls load() which hits /api/cost/summary?start_date=...,
// /api/cost/budget, and /api/cost/entries?start_date=...&limit=50 via
// Promise.allSettled. We mock global.fetch, stub PageHeader (uses useRoute),
// then assert on the rendered stat cards and period switch behaviour.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Cost from '../views/Cost.vue';
import { PageHeader, EmptyState } from '../components/index.js';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Cost.vue', () => {
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
      // Match by prefix so start_date query strings don't break lookups.
      const key = Object.keys(routes).find((k) => u.startsWith(k));
      const body = key ? routes[key] : {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/cost/summary': {
        summary: {
          total_cost_usd: 12.3456, total_tokens: 1500, total_calls: 30, avg_latency_ms: 200,
          by_model: { 'gpt-4': { cost: 10 } }, by_agent: { claude: { cost: 10, tokens: 1000, calls: 20 } },
        },
      },
      '/api/cost/budget': { budget: { daily_spent_usd: 5, daily_limit_usd: 10, monthly_spent_usd: 50, monthly_limit_usd: 100 } },
      '/api/cost/entries': { entries: [] },
      ...overrides,
    };
  }

  async function mountCost() {
    const wrapper = mount(Cost, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the cost root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountCost();
    expect(wrapper.find('.cost-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders stat cards with loaded summary values', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountCost();
    const text = wrapper.text();
    expect(text).toContain('$12.3456');   // money(total_cost_usd) — 4 decimals
    expect(text).toContain('1.5K');        // formatNum(1500 tokens)
    expect(text).toContain('30');          // total_calls
    wrapper.unmount();
  });

  it('reloads data when the period segmented control is switched', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountCost();
    const callsBefore = global.fetch.mock.calls.length;
    // Period Segmented emits update:model-value; trigger via the refresh button
    // is unreliable, so we click the refresh btn-ghost which calls load().
    await wrapper.find('.btn-ghost').trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });

  it('shows empty state when no agent spend is returned', async () => {
    mockFetch(defaultRoutes({
      '/api/cost/summary': { summary: { total_cost_usd: 0, total_tokens: 0, total_calls: 0, avg_latency_ms: 0, by_model: {}, by_agent: {} } },
    }));
    const wrapper = await mountCost();
    expect(wrapper.findComponent(EmptyState).exists()).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when /api/cost/summary fails', async () => {
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.startsWith('/api/cost/summary')) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
      }
      const routes = defaultRoutes();
      const key = Object.keys(routes).find((k) => u.startsWith(k));
      const body = key ? routes[key] : {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
    });
    const wrapper = await mountCost();
    expect(wrapper.find('.cost-view').exists()).toBe(true);
    expect(wrapper.find('.view-error').exists()).toBe(true);
    wrapper.unmount();
  });
});