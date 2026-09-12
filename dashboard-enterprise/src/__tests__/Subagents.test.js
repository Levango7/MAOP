// Smoke tests for Subagents.vue — subagent management page.
//
// Subagents.onMounted calls loadList() which hits /api/subagent/list.
// We mock global.fetch, stub PageHeader (via ListPageLayout), then
// assert the root renders, tabs switch, subagent list displays,
// spawn form works, and transcript drawer opens.

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
      '/api/subagent/list': { agents: [], count: 0 },
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
        agents: [
          { agent_id: 'sa-1', agent: 'coder', task: 'refactor module', model: 'gpt-4', status: 'running' },
          { agent_id: 'sa-2', agent: 'reviewer', task: 'review PR', model: '', status: 'done' },
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

  it('displays correct status badges for subagents', async () => {
    mockFetch(defaultRoutes({
      '/api/subagent/list': {
        agents: [
          { agent_id: 'sa-1', agent: 'a1', status: 'running' },
          { agent_id: 'sa-2', agent: 'a2', status: 'done' },
          { agent_id: 'sa-3', agent: 'a3', status: 'error' },
        ],
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
      '/api/subagent/list': {
        agents: [
          { agent_id: 'sa-1', agent: 'coder', task: 'do work', status: 'running' },
        ],
      },
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
        agents: [
          { agent_id: 'sa-1', agent: 'a1', status: 'running' },
          { agent_id: 'sa-2', agent: 'a2', status: 'done' },
        ],
      },
    }));
    const wrapper = await mountSubagents();
    const transcriptBtns = wrapper.findAll('button[aria-label="Transcript"]');
    expect(transcriptBtns.length).toBe(2);
    wrapper.unmount();
  });
});