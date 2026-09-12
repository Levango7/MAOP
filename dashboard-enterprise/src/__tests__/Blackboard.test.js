// Smoke tests for Blackboard.vue — shared knowledge blackboard management page.
//
// Blackboard.onMounted calls loadAll() which hits /api/blackboard/snapshot,
// /api/blackboard/domains, /api/blackboard/history, /api/blackboard/stats
// via Promise.allSettled. We mock global.fetch, stub PageHeader (via
// ListPageLayout), then assert the root renders, tabs switch, snapshot
// cards display, domain entries render, and write modal opens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Blackboard from '../views/Blackboard.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Blackboard.vue', () => {
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
      '/api/blackboard/snapshot': { status: 'ok', data: {} },
      '/api/blackboard/domains': { status: 'ok', data: { allowed: ['tasks', 'results', 'metrics'], active: [] } },
      '/api/blackboard/history': { status: 'ok', data: [] },
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 0,
          active_domains: [],
          event_bus_enabled: false,
          allowed_domains: ['tasks', 'results', 'metrics'],
        },
      },
      ...overrides,
    };
  }

  async function mountBlackboard() {
    const wrapper = mount(Blackboard, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the blackboard-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders snapshot tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    // Snapshot tab is active by default → empty state when no domains
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Blackboard is empty');
    wrapper.unmount();
  });

  it('renders snapshot cards when snapshot has data', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/snapshot': {
        status: 'ok',
        data: {
          tasks: [
            { id: 'e1', content: 'do X', contributor: 'agent_a', confidence: 0.9 },
            { id: 'e2', content: 'do Y', contributor: 'agent_b', confidence: 0.8 },
          ],
          results: [
            { id: 'e3', content: 'done', contributor: 'agent_a', confidence: 1.0 },
          ],
        },
      },
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 3,
          active_domains: ['tasks', 'results'],
          event_bus_enabled: false,
          allowed_domains: ['tasks', 'results', 'metrics'],
        },
      },
    }));
    const wrapper = await mountBlackboard();
    const cards = wrapper.findAll('.snap-card');
    expect(cards.length).toBe(2);
    expect(wrapper.text()).toContain('tasks');
    expect(wrapper.text()).toContain('results');
    wrapper.unmount();
  });

  it('switches to domains tab and shows domain entries', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/domains': {
        status: 'ok',
        data: { allowed: ['tasks', 'results'], active: ['tasks'] },
      },
      '/api/blackboard/domains/tasks': {
        status: 'ok',
        data: [
          { id: 'e1', content: 'do X', contributor: 'agent_a', confidence: 0.9 },
        ],
        count: 1,
      },
    }));
    const wrapper = await mountBlackboard();
    // tabOptions: snapshot, domains, history, stats → domains is index 1
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Domain entries should be visible
    expect(wrapper.text()).toContain('do X');
    wrapper.unmount();
  });

  it('switches to history tab', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/history': {
        status: 'ok',
        data: [
          { op: 'write', actor: 'agent_a', domain: 'tasks', timestamp: '2026-09-12T00:00:00Z' },
        ],
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('write');
    expect(wrapper.text()).toContain('agent_a');
    wrapper.unmount();
  });

  it('switches to stats tab and shows statistics', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/stats': {
        status: 'ok',
        data: {
          total_entries: 42,
          active_domains: ['tasks', 'results'],
          event_bus_enabled: true,
          allowed_domains: ['tasks', 'results', 'metrics'],
        },
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[3].trigger('click');
    await flushPromises();
    expect(wrapper.find('.stats-grid').exists()).toBe(true);
    expect(wrapper.text()).toContain('42');
    wrapper.unmount();
  });

  it('opens write entry modal when Write Entry button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountBlackboard();
    // Switch to domains tab to access the Write Entry button
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    const buttons = wrapper.findAll('button');
    const writeBtn = buttons.find((b) => b.text().includes('Write Entry'));
    expect(writeBtn).toBeTruthy();
    await writeBtn.trigger('click');
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
    const wrapper = await mountBlackboard();
    expect(wrapper.find('.blackboard-view').exists()).toBe(true);
    // Error state shown for snapshot tab
    expect(wrapper.text()).toContain('Failed to load snapshot');
    wrapper.unmount();
  });

  it('displays confidence badge with appropriate tone', async () => {
    mockFetch(defaultRoutes({
      '/api/blackboard/domains': {
        status: 'ok',
        data: { allowed: ['tasks'], active: ['tasks'] },
      },
      '/api/blackboard/domains/tasks': {
        status: 'ok',
        data: [
          { id: 'e1', content: 'high confidence', contributor: 'a', confidence: 0.95 },
          { id: 'e2', content: 'low confidence', contributor: 'b', confidence: 0.2 },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountBlackboard();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Confidence values should be formatted
    expect(wrapper.text()).toContain('0.95');
    expect(wrapper.text()).toContain('0.20');
    wrapper.unmount();
  });
});