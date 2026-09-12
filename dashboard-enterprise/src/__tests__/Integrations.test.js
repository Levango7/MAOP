// Smoke tests for Integrations.vue — n8n integration management page.
//
// Integrations.onMounted calls loadAll() which hits /api/n8n/workflows
// via Promise.allSettled. We mock global.fetch, stub PageHeader (via
// ListPageLayout), then assert the root renders, tabs switch, workflows
// list displays, health check works, and trigger modal opens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Integrations from '../views/Integrations.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Integrations.vue', () => {
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
      const u = String(url).split('?')[0];
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
      '/api/n8n/workflows': { status: 'ok', workflows: [], count: 0 },
      ...overrides,
    };
  }

  async function mountIntegrations() {
    const wrapper = mount(Integrations, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the integrations-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountIntegrations();
    expect(wrapper.find('.integrations-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders workflows tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountIntegrations();
    // Workflows tab is active by default → empty state when no workflows
    expect(wrapper.find('.integrations-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('No workflows available');
    wrapper.unmount();
  });

  it('renders workflow rows when /api/n8n/workflows returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/n8n/workflows': {
        status: 'ok',
        workflows: [
          { id: 'wf-1', name: 'Sync Agents', active: true, nodes: 5, last_execution_started_at: '2026-09-12T00:00:00Z' },
          { id: 'wf-2', name: 'Notify Slack', active: false, nodes: 3 },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountIntegrations();
    const rows = wrapper.findAll('.wf-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('Sync Agents');
    expect(wrapper.text()).toContain('Notify Slack');
    wrapper.unmount();
  });

  it('switches to executions tab', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountIntegrations();
    // tabOptions: workflows, executions, health → executions is index 1
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('No executions yet');
    wrapper.unmount();
  });

  it('switches to health tab and shows health check button', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountIntegrations();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('n8n Connectivity');
    const buttons = wrapper.findAll('button');
    const checkBtn = buttons.find((b) => b.text().includes('Run Health Check'));
    expect(checkBtn).toBeTruthy();
    wrapper.unmount();
  });

  it('runs health check and displays result', async () => {
    mockFetch(defaultRoutes({
      '/api/n8n/health': {
        status: 'ok',
        n8n_reachable: true,
        base_url: 'http://localhost:5678',
      },
    }));
    const wrapper = await mountIntegrations();
    // Switch to health tab
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    const buttons = wrapper.findAll('button');
    const checkBtn = buttons.find((b) => b.text().includes('Run Health Check'));
    await checkBtn.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('http://localhost:5678');
    expect(wrapper.text()).toContain('Reachable');
    wrapper.unmount();
  });

  it('opens trigger modal when trigger button clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/n8n/workflows': {
        status: 'ok',
        workflows: [
          { id: 'wf-1', name: 'Sync Agents', active: true, nodes: 5 },
        ],
        count: 1,
      },
    }));
    const wrapper = await mountIntegrations();
    const triggerBtn = wrapper.find('.wf-table__row .icon-btn');
    expect(triggerBtn.exists()).toBe(true);
    await triggerBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('does not crash when all API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountIntegrations();
    expect(wrapper.find('.integrations-view').exists()).toBe(true);
    // Error state shown for workflows tab
    expect(wrapper.text()).toContain('Failed to load workflows');
    wrapper.unmount();
  });

  it('displays active badge for active workflows', async () => {
    mockFetch(defaultRoutes({
      '/api/n8n/workflows': {
        status: 'ok',
        workflows: [
          { id: 'wf-1', name: 'Active WF', active: true, nodes: 2 },
          { id: 'wf-2', name: 'Inactive WF', active: false, nodes: 2 },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountIntegrations();
    const badges = wrapper.findAll('.badge');
    // Should contain both On and Off labels
    const hasOn = badges.some((b) => b.text().includes('On'));
    const hasOff = badges.some((b) => b.text().includes('Off'));
    expect(hasOn).toBe(true);
    expect(hasOff).toBe(true);
    wrapper.unmount();
  });

  it('shows stats overview with workflow counts', async () => {
    mockFetch(defaultRoutes({
      '/api/n8n/workflows': {
        status: 'ok',
        workflows: [
          { id: 'wf-1', name: 'A', active: true, nodes: 2 },
          { id: 'wf-2', name: 'B', active: false, nodes: 2 },
          { id: 'wf-3', name: 'C', active: true, nodes: 2 },
        ],
        count: 3,
      },
    }));
    const wrapper = await mountIntegrations();
    // Stats card shows total workflows = 3
    expect(wrapper.text()).toContain('3');
    wrapper.unmount();
  });
});