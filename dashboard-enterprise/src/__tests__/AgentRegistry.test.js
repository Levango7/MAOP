// Contract tests for AgentRegistry.vue — the agent catalog page.
//
// onMounted → loadAll() hits GET /api/agent-catalog/agents then
// GET /api/agent-catalog/stats. We mock global.fetch and assert against the
// row/stat shapes the real producer sends (AgentDescriptor.model_dump +
// catalog_stats), covering the three defects this batch fixed:
//   1. the four stat cards read keys the API never sent (all rendered 0)
//   2. the health badge read `a.health`/`a.health_status` — always "Unknown"
//   3. the edit PUT sent `name` in the body, which the API silently dropped

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { nextTick } from 'vue';
import AgentRegistry from '../views/AgentRegistry.vue';

/** Shape of one row from `agent_service.list_agents()` — every field of
 *  `AgentDescriptor.model_dump(mode="json")`, no aliases. */
function row(overrides = {}) {
  return {
    name: 'alpha',
    display_name: 'Alpha',
    vendor: 'acme',
    version: '',
    adapter_type: 'cli',
    adapter_config: {},
    capabilities: ['chat'],
    max_context_length: 0,
    supports_streaming: false,
    supports_tool_call: false,
    billing_model: 'per_token',
    billing_config: {},
    auth_method: 'none',
    region: 'international',
    auth_credentials_ref: '',
    max_concurrent: 2,
    rate_limit_per_min: 0,
    timeout_s: 30,
    retry_count: 1,
    fallback_agents: [],
    enabled: true,
    healthy: true,
    last_health_check: 0,
    ...overrides,
  };
}

const PAYLOAD_KEYS = [
  'display_name', 'vendor', 'adapter_type', 'capabilities', 'billing_model',
  'auth_method', 'max_concurrent', 'timeout_s', 'retry_count', 'fallback_agents',
];

describe('AgentRegistry.vue', () => {
  let originalFetch;
  let calls;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    calls = [];
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    document.body.replaceChildren();
  });

  function mockFetch(routes) {
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      const method = (init && init.method) || 'GET';
      calls.push({
        url: u,
        method,
        body: init && init.body ? JSON.parse(init.body) : null,
      });
      const spec = routes[`${method} ${u.split('?')[0]}`] ?? routes[u.split('?')[0]] ?? { ok: true, body: {} };
      return Promise.resolve({
        ok: spec.ok !== false,
        status: spec.status ?? (spec.ok === false ? 500 : 200),
        json: () => Promise.resolve(spec.body ?? {}),
        text: () => Promise.resolve(JSON.stringify(spec.body ?? {})),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/agent-catalog/agents': {
        body: { status: 'ok', agents: [row()], count: 1, error: '' },
      },
      '/api/agent-catalog/stats': {
        body: {
          status: 'ok', count: 1, healthy_count: 1,
          enabled_count: 1, capability_count: 1, error: '',
        },
      },
      ...overrides,
    };
  }

  async function mountView() {
    const wrapper = mount(AgentRegistry);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  function statValues(wrapper) {
    return wrapper.findAll('.stat-card__value').map((n) => n.text());
  }

  function statusBadges(wrapper, rowIdx) {
    const stack = wrapper.findAll('.ar-table__row .status-stack')[rowIdx];
    return stack.findAll('.badge').map((n) => n.text());
  }

  function modal() {
    return document.body.querySelector('.modal-overlay .modal');
  }

  async function openFormModal(wrapper) {
    await wrapper.find('.list-page .btn-primary').trigger('click');
    await nextTick();
    await flushPromises();
    return modal();
  }

  async function clickModalSubmit() {
    const buttons = modal().querySelectorAll('.modal__foot button');
    const submit = buttons[buttons.length - 1];
    submit.click();
    await flushPromises();
    await flushPromises();
  }

  // ── defect 1: the four stat cards ───────────────────────────────

  it('renders the four counts the stats endpoint sends', async () => {
    mockFetch(defaultRoutes({
      '/api/agent-catalog/agents': {
        body: { status: 'ok', agents: [row(), row({ name: 'beta', enabled: false })], count: 2 },
      },
      '/api/agent-catalog/stats': {
        body: {
          status: 'ok', count: 2, healthy_count: 2,
          enabled_count: 1, capability_count: 3, error: '',
        },
      },
    }));
    const wrapper = await mountView();
    expect(statValues(wrapper)).toEqual(['2', '1', '2', '3']);
    wrapper.unmount();
  });

  it('derives the same four counts from the rows when /stats fails', async () => {
    mockFetch(defaultRoutes({
      '/api/agent-catalog/agents': {
        body: {
          status: 'ok',
          agents: [
            row(),
            row({ name: 'beta', enabled: false, capabilities: ['chat', 'tool_use'] }),
            row({ name: 'gamma', healthy: false, capabilities: ['tool_use'] }),
          ],
          count: 3,
        },
      },
      '/api/agent-catalog/stats': { ok: false, status: 500, body: {} },
    }));
    const wrapper = await mountView();
    // count / enabled / healthy / distinct capabilities
    expect(statValues(wrapper)).toEqual(['3', '2', '2', '2']);
    wrapper.unmount();
  });

  // ── defect 2: the health + enable badges ────────────────────────

  it('reads the boolean healthy flag, never a string health key', async () => {
    mockFetch(defaultRoutes({
      '/api/agent-catalog/agents': {
        body: {
          status: 'ok',
          agents: [row({ name: 'alpha', healthy: true }), row({ name: 'gamma', healthy: false })],
          count: 2,
        },
      },
    }));
    const wrapper = await mountView();
    expect(statusBadges(wrapper, 0)).toEqual(['Enabled', 'Healthy']);
    expect(statusBadges(wrapper, 1)).toEqual(['Enabled', 'Unhealthy']);
    wrapper.unmount();
  });

  it('renders the disabled state from the boolean enabled flag', async () => {
    mockFetch(defaultRoutes({
      '/api/agent-catalog/agents': {
        body: {
          status: 'ok',
          agents: [row({ name: 'alpha', enabled: true }), row({ name: 'beta', enabled: false })],
          count: 2,
        },
      },
    }));
    const wrapper = await mountView();
    expect(statusBadges(wrapper, 0)).toEqual(['Enabled', 'Healthy']);
    expect(statusBadges(wrapper, 1)).toEqual(['Disabled', 'Healthy']);
    wrapper.unmount();
  });

  // ── defect 3: the request bodies the modal builds ───────────────

  it('POSTs name alongside the payload when registering', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();
    const form = await openFormModal(wrapper);
    expect(form).toBeTruthy();

    const nameInput = form.querySelector('.form-grid input');
    nameInput.value = 'nova';
    nameInput.dispatchEvent(new Event('input'));
    await nextTick();

    await clickModalSubmit();

    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/agent-catalog/agents');
    expect(post, JSON.stringify(calls)).toBeTruthy();
    expect(post.body.name).toBe('nova');
    expect(Object.keys(post.body).sort()).toEqual(['name', ...PAYLOAD_KEYS].sort());
    wrapper.unmount();
  });

  it('PUTs the payload without name when editing', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();

    // first row → first action button is "edit"
    await wrapper.findAll('.ar-table__td--act .icon-btn')[0].trigger('click');
    await nextTick();
    await flushPromises();
    expect(modal()).toBeTruthy();

    await clickModalSubmit();

    const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/agent-catalog/agents/alpha');
    expect(put, JSON.stringify(calls)).toBeTruthy();
    expect(Object.keys(put.body).sort()).toEqual([...PAYLOAD_KEYS].sort());
    expect(put.body.name).toBeUndefined();
    wrapper.unmount();
  });

  it('PUTs only the enabled flag when toggling the power button', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountView();

    await wrapper.findAll('.ar-table__td--act .icon-btn')[1].trigger('click');
    await flushPromises();

    const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/agent-catalog/agents/alpha');
    expect(put, JSON.stringify(calls)).toBeTruthy();
    expect(put.body).toEqual({ enabled: false });
    expect(statusBadges(wrapper, 0)).toContain('Disabled');
    wrapper.unmount();
  });

  it('shows the load error instead of a silent empty table', async () => {
    mockFetch(defaultRoutes({
      '/api/agent-catalog/agents': { ok: false, status: 403, body: { error: 'Admin role required' } },
    }));
    const wrapper = await mountView();
    expect(wrapper.text()).toContain('Failed to load agents');
    wrapper.unmount();
  });
});
