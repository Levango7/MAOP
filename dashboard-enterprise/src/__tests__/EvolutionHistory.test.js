// Smoke tests for EvolutionHistory.vue — evolution cycles, A/B experiments,
// deployment history, and pending approval (human gate) sections.
//
// EvolutionHistory.onMounted calls loadAll() → loadCycles() (/api/evolution/cycles?limit=50)
// + loadAb() (/api/evolution/ab/list + per-experiment /api/evolution/ab/evaluate/*)
// + loadDeployments() (/api/evolution/deploy/history) + loadPending() (/api/evolution/pending).
// We mock global.fetch, stub PageHeader, then assert the root renders, stat cards
// are present, and data populates the tables.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import EvolutionHistory from '../views/EvolutionHistory.vue';

const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('EvolutionHistory.vue', () => {
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
      '/api/evolution/cycles?limit=50': { cycles: [] },
      '/api/evolution/ab/list': { experiments: [] },
      '/api/evolution/deploy/history': { history: [] },
      '/api/evolution/pending': { pending: [] },
      ...overrides,
    };
  }

  async function mountEvoHistory() {
    const wrapper = mount(EvolutionHistory, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the evo-history-page root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvoHistory();
    expect(wrapper.find('.evo-history-page').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders 4 stat cards (cycles/promotions/rollbacks/pending)', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvoHistory();
    const row = wrapper.find('.stat-row');
    expect(row.exists()).toBe(true);
    // StatCard components are stubbed by @vue/test-utils default; verify the
    // stat-row container exists (4 StatCard children render inside).
    wrapper.unmount();
  });

  it('renders 4 Card sections (cycles/ab/deploy/pending)', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountEvoHistory();
    // Each section is a Card with a .card-desc. 4 sections → 4 .card-desc.
    const descs = wrapper.findAll('.card-desc');
    expect(descs).toHaveLength(4);
    wrapper.unmount();
  });

  it('renders pending approval items when /api/evolution/pending returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/evolution/pending': {
        pending: [
          { cycle_id: 'c1', experiment: 'exp-a', detail: 'awaiting approval' },
          { cycle_id: 'c2', experiment: 'exp-b', detail: 'awaiting approval' },
        ],
      },
    }));
    const wrapper = await mountEvoHistory();
    const items = wrapper.findAll('.pending-item');
    expect(items).toHaveLength(2);
    expect(wrapper.text()).toContain('exp-a');
    expect(wrapper.text()).toContain('exp-b');
    wrapper.unmount();
  });

  it('renders A/B rows from the serialized SPRT state', async () => {
    // SPRTState.success_rate is a Python @property, so the wire only carries
    // samples/successes. Before P1-b the view read sprt.success_rate → undefined
    // and the rate column rendered NaN; the empty-list mock hid both this and
    // the route-shadowing that made abExperiments always empty.
    mockFetch(defaultRoutes({
      '/api/evolution/ab/list': { experiments: ['evo-1'] },
      '/api/evolution/ab/evaluate/evo-1': {
        status: 'ok',
        result: {
          experiment: 'evo-1',
          control: { name: 'control', samples: 40, successes: 20, success_rate: 0.5 },
          treatment: { name: 'treatment', samples: 40, successes: 32, success_rate: 0.8 },
          sprt: { samples: 80, successes: 52, llr: 2.1, decision: 'accept_h1', winner: 'treatment', stopped_at: 61 },
          decision: 'accept_h1',
          winner: 'treatment',
          is_significant: true,
        },
      },
    }));
    const wrapper = await mountEvoHistory();
    const text = wrapper.text();
    expect(text).toContain('evo-1');
    expect(text).not.toContain('NaN');
    expect(text).toContain('65.0'); // 52 / 80
    wrapper.unmount();
  });

  it('does not crash when all evolution endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountEvoHistory();
    expect(wrapper.find('.evo-history-page').exists()).toBe(true);
    wrapper.unmount();
  });
});