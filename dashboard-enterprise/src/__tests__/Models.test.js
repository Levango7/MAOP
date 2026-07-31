// Tests for Models.vue reactive→ref fix.
//
// models/providers/agents/policies were changed from reactive(...) to ref([]),
// with assignments using `.value`. These tests mount the real component, mock
// the /api/model/* endpoints, and verify that data loaded into the refs flows
// correctly through the computed row transforms into the DataTable `rows` prop.
//
// If the refs were misused (e.g. assigning to a reactive instead of `.value`),
// the rendered rows would not update — these assertions catch that regression.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Models from '../views/Models.vue';
import DataTable from '../components/DataTable.vue';
import { PageHeader } from '../components/index.js';

// PageHeader calls useRoute() which needs a router context; stub it with a
// pass-through slot so the view's slot content (e.g. the refresh button) still
// renders without requiring a full vue-router instance.
const mountOptions = { global: { stubs: { PageHeader: { template: '<slot />' } } } };

describe('Models.vue reactive→ref usage', () => {
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
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  // Default empty endpoints so loadAll's Promise.allSettled settles cleanly.
  function emptyModelEndpoints(overrides = {}) {
    return {
      '/api/model/registry': { stats: { total_models: 0, enabled_models: 0, total_providers: 0, thinking_capable: 0 } },
      '/api/model/list': { models: [] },
      '/api/model/providers': { providers: [] },
      '/api/model/agents': { agents: [] },
      '/api/model/quota': { agents: [] },
      '/api/model/policies': { policies: [] },
      '/api/model/budget': { budget: null },
      ...overrides,
    };
  }

  async function mountModels() {
    const wrapper = mount(Models, mountOptions);
    // loadAll runs 7 parallel loads via Promise.allSettled in onMounted.
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  // DataTable order in the template:
  //   [0] models, [1] providers, [2] agents, [3] quota, [4] policies
  function tableRows(wrapper, index) {
    const tables = wrapper.findAllComponents(DataTable);
    expect(tables.length).toBeGreaterThanOrEqual(index + 1);
    return tables[index].props('rows');
  }

  it('loads models into the models ref and renders modelRows', async () => {
    mockFetch(emptyModelEndpoints({
      '/api/model/list': {
        models: [
          { name: 'gpt-4', provider: 'openai', family: 'gpt', context_window: 128000, quality_tier: 'high', latency_tier: 'low', provider_healthy: true, enabled: true },
          { name: 'llama3', enabled: false },
        ],
      },
    }));
    const wrapper = await mountModels();
    const rows = tableRows(wrapper, 0);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      name: 'gpt-4',
      provider: 'openai',
      family: 'gpt',
      context_window: 128000,
      quality_tier: 'high',
      latency_tier: 'low',
      provider_healthy: 'healthy',
      enabled: 'enabled',
    });
    // Missing optional fields default to '—' and booleans map to healthy/unhealthy.
    expect(rows[1].provider).toBe('—');
    expect(rows[1].family).toBe('—');
    expect(rows[1].context_window).toBe('—');
    expect(rows[1].provider_healthy).toBe('unhealthy');
    expect(rows[1].enabled).toBe('disabled');
    wrapper.unmount();
  });

  it('loads providers into the providers ref and renders providerRows', async () => {
    mockFetch(emptyModelEndpoints({
      '/api/model/providers': {
        providers: [
          { name: 'openai', type: 'openai', protocol: 'rest', healthy: true, has_api_key: true, enabled: true },
          { name: 'local', enabled: false },
        ],
      },
    }));
    const wrapper = await mountModels();
    const rows = tableRows(wrapper, 1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      name: 'openai',
      type: 'openai',
      protocol: 'rest',
      healthy: 'healthy',
      has_key: 'yes',
      enabled: 'enabled',
    });
    expect(rows[1].healthy).toBe('unhealthy');
    expect(rows[1].has_key).toBe('no');
    expect(rows[1].enabled).toBe('disabled');
    wrapper.unmount();
  });

  it('loads agents into the agents ref and renders agentRows', async () => {
    mockFetch(emptyModelEndpoints({
      '/api/model/agents': {
        agents: [
          { name: 'researcher', driver: 'cli', model: 'gpt-4', capabilities: ['search', 'code'], cli_available: true },
          { name: 'writer' },
        ],
      },
    }));
    const wrapper = await mountModels();
    const rows = tableRows(wrapper, 2);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      name: 'researcher',
      driver: 'cli',
      model: 'gpt-4',
      capabilities: 'search, code',
      cli_available: 'yes',
    });
    expect(rows[1].driver).toBe('—');
    expect(rows[1].model).toBe('—');
    expect(rows[1].capabilities).toBe('—');
    expect(rows[1].cli_available).toBe('no');
    wrapper.unmount();
  });

  it('loads policies into the policies ref and renders policyRows', async () => {
    mockFetch(emptyModelEndpoints({
      '/api/model/policies': {
        policies: [
          { name: 'cheap', strategy: 'cost', max_cost_per_task: 0.5, prefer_low_latency: false, fallback_on_error: true },
          { name: 'fast' },
        ],
      },
    }));
    const wrapper = await mountModels();
    const rows = tableRows(wrapper, 4);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({
      name: 'cheap',
      strategy: 'cost',
      max_cost_per_task: '$0.500',
      prefer_low_latency: 'no',
      fallback_on_error: 'yes',
    });
    expect(rows[1].strategy).toBe('—');
    expect(rows[1].max_cost_per_task).toBe('—');
    wrapper.unmount();
  });

  it('loads registry stats into the registry reactive and renders StatCards', async () => {
    mockFetch(emptyModelEndpoints({
      '/api/model/registry': {
        stats: { total_models: 10, enabled_models: 7, total_providers: 3, thinking_capable: 4 },
      },
    }));
    const wrapper = await mountModels();
    // StatCards render their value prop as text. Verify the registry numbers show.
    const text = wrapper.text();
    expect(text).toContain('10');
    expect(text).toContain('7');
    expect(text).toContain('3');
    expect(text).toContain('4');
    wrapper.unmount();
  });

  it('ref reassignment on reload updates the rendered rows', async () => {
    // First load: one model.
    mockFetch(emptyModelEndpoints({
      '/api/model/list': { models: [{ name: 'a', provider: 'p', enabled: true }] },
    }));
    const wrapper = mount(Models, mountOptions);
    await flushPromises();
    await flushPromises();
    expect(tableRows(wrapper, 0)).toHaveLength(1);

    // Second load: two models — proves models.value = d.models reassigns the ref.
    mockFetch(emptyModelEndpoints({
      '/api/model/list': { models: [{ name: 'a', enabled: true }, { name: 'b', enabled: false }] },
    }));
    await wrapper.find('.btn-ghost').trigger('click'); // loadAll
    await flushPromises();
    await flushPromises();
    const rows = tableRows(wrapper, 0);
    expect(rows).toHaveLength(2);
    expect(rows[0].name).toBe('a');
    expect(rows[1].name).toBe('b');
    wrapper.unmount();
  });
});