// Tests for Subagents.vue — subagent management page.
//
// The payloads below mirror what the endpoint really answers. The list
// response is the envelope of /api/subagent/list and every row is a
// subagents table row renamed to the API's vocabulary (agent_id / agent);
// the producer keeps the status values of AgentStatus (running / completed /
// failed / cancelled / pending).
//
// The mock used to answer {agent_id, agent, ...} while the endpoint sent
// the raw DB columns {id, name, ...}. Both sides agreed with each other and
// nobody noticed that the view read an id the API never sent — every row
// action went out with agent_id: undefined.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Subagents from '../views/Subagents.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

function producerRow(overrides = {}) {
  return {
    agent_id: 'sa-1',
    agent: 'coder',
    role: 'leaf',
    model: 'claude-3',
    task: 'refactor module',
    status: 'running',
    created_at: '1700000000.0',
    ...overrides,
  };
}

describe('Subagents.vue', () => {
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
    const calls = [];
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      calls.push({
        url: u,
        method: (init && init.method) || 'GET',
        body: init && init.body ? JSON.parse(init.body) : null,
      });
      const body = routes[u] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    return calls;
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/subagent/list': { status: 'ok', agents: [], count: 0 },
      ...overrides,
    };
  }

  async function mountSubagents() {
    const wrapper = mount(Subagents, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the subagents-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSubagents();
    expect(wrapper.find('.subagents-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders active tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSubagents();
    expect(wrapper.find('.subagents-view').exists()).toBe(true);
    // Empty state shown when no subagents
    expect(wrapper.text()).toContain('No active subagents');
    wrapper.unmount();
  });

  it('renders subagent rows when /api/subagent/list returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': {
        status: 'ok',
        agents: [
          producerRow(),
          producerRow({ agent_id: 'sa-2', agent: 'reviewer', task: 'review PR', status: 'completed' }),
        ],
        count: 2,
      },
    }));
    const wrapper = await mountSubagents();
    const rows = wrapper.findAll('.sa-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('coder');
    expect(wrapper.text()).toContain('reviewer');
    wrapper.unmount();
  });

  it('shows the id and the name the producer sent', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': { status: 'ok', agents: [producerRow()], count: 1 },
    }));
    const wrapper = await mountSubagents();
    expect(wrapper.find('.sa-table__id').text()).toBe('sa-1');
    expect(wrapper.find('.sa-table__agent').text()).toBe('coder');
    wrapper.unmount();
  });

  it('displays correct status badges for subagents', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': {
        status: 'ok',
        agents: [
          producerRow({ agent_id: 'sa-1', agent: 'a1', status: 'running' }),
          producerRow({ agent_id: 'sa-2', agent: 'a2', status: 'completed' }),
          producerRow({ agent_id: 'sa-3', agent: 'a3', status: 'failed' }),
        ],
        count: 3,
      },
    }));
    const wrapper = await mountSubagents();
    const badges = wrapper.findAll('.badge');
    expect(badges[0].text()).toContain('Running');
    expect(badges[1].text()).toContain('Done');
    expect(badges[2].text()).toContain('Error');
    wrapper.unmount();
  });

  it('switches to spawn tab when clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSubagents();
    const segButtons = wrapper.findAll('.segmented__item');
    // tabOptions: active, spawn → spawn is index 1
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Spawn Subagent');
    expect(wrapper.find('.spawn-form').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders spawn form fields in spawn tab', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountSubagents();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Agent name');
    expect(wrapper.text()).toContain('Task');
    expect(wrapper.text()).toContain('Context');
    expect(wrapper.text()).toContain('Model');
    wrapper.unmount();
  });

  it('opens wait modal when wait button clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': { status: 'ok', agents: [producerRow()], count: 1 },
    }));
    const wrapper = await mountSubagents();
    const waitBtn = wrapper.find('button[aria-label="Wait"]');
    expect(waitBtn.exists()).toBe(true);
    await waitBtn.trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('carries the row id into the wait modal and the transcript request', async () => {
    const calls = mockFetch(defaultRoutes({
      '/api/subagent/list': { status: 'ok', agents: [producerRow()], count: 1 },
      '/api/subagent/transcript?agent_id=sa-1': {
        status: 'ok',
        transcript: [{ agent_id: 'sa-1', timestamp: 1700000000, event: 'started', data: { task: 'refactor module' } }],
      },
    }));
    const wrapper = await mountSubagents();

    await wrapper.find('button[aria-label="Wait"]').trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal.textContent).toContain('sa-1');

    await wrapper.find('button[aria-label="Transcript"]').trigger('click');
    await flushPromises();
    const transcriptCall = calls.find((c) => c.url.includes('/api/subagent/transcript'));
    expect(transcriptCall.url).toContain('agent_id=sa-1');
    wrapper.unmount();
  });

  it('does not crash when API endpoint fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountSubagents();
    expect(wrapper.find('.subagents-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Failed to load subagents');
    wrapper.unmount();
  });

  it('shows transcript button for each subagent', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': {
        status: 'ok',
        agents: [
          producerRow({ agent_id: 'sa-1', agent: 'a1', status: 'running' }),
          producerRow({ agent_id: 'sa-2', agent: 'a2', status: 'completed' }),
        ],
        count: 2,
      },
    }));
    const wrapper = await mountSubagents();
    const transcriptBtns = wrapper.findAll('button[aria-label="Transcript"]');
    expect(transcriptBtns.length).toBe(2);
    wrapper.unmount();
  });
});
