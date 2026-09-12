// Tests for Protocols.vue — protocol registry and message log page.
//
// Protocols.onMounted calls loadProtocols() which hits /api/protocol/list.
// We mock global.fetch, stub PageHeader, then assert the root renders, tabs
// switch, protocol list displays, register modal opens, and messages load.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Protocols from '../views/Protocols.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Protocols.vue', () => {
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
      '/api/protocol/list': { protocols: [], count: 0 },
      ...overrides,
    };
  }

  async function mountProtocols() {
    const wrapper = mount(Protocols, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the protocols-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountProtocols();
    expect(wrapper.find('.protocols-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders protocol list tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountProtocols();
    expect(wrapper.find('.protocols-view').exists()).toBe(true);
    // Empty state shown when no protocols
    expect(wrapper.text()).toContain('No protocols registered');
    wrapper.unmount();
  });

  it('renders protocol rows when /api/protocol/list returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/protocol/list': {
        protocols: [
          { name: 'handshake', version: '1.0', participants: ['agent-a', 'agent-b'], description: 'Initial handshake' },
          { name: 'task-delegate', version: '2.0', participants: ['orchestrator'], description: 'Task delegation' },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountProtocols();
    const rows = wrapper.findAll('.proto-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('handshake');
    expect(wrapper.text()).toContain('task-delegate');
    wrapper.unmount();
  });

  it('opens register protocol modal when Register button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountProtocols();
    const buttons = wrapper.findAll('button');
    const addBtn = buttons.find((b) => b.text().includes('Register'));
    expect(addBtn).toBeTruthy();
    await addBtn.trigger('click');
    await flushPromises();
    // Modal should be teleported to body
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('switches to messages tab and shows empty state', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountProtocols();
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('No messages');
    wrapper.unmount();
  });

  it('renders messages when /api/protocol/messages returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/protocol/messages?recipient=agent-a&limit=100': {
        messages: [
          { sender: 'agent-b', recipient: 'agent-a', protocol: 'handshake', timestamp: '2026-09-12T10:00:00Z', payload: { type: 'hello' } },
          { sender: 'orchestrator', recipient: 'agent-a', protocol: 'task-delegate', timestamp: '2026-09-12T10:01:00Z', payload: { task: 'compute' } },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountProtocols();
    // Switch to messages tab
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    // Enter recipient and search
    const input = wrapper.find('.msg-search__input');
    expect(input.exists()).toBe(true);
    await input.setValue('agent-a');
    const searchBtn = wrapper.find('.msg-search .btn-ghost');
    await searchBtn.trigger('click');
    await flushPromises();
    const msgItems = wrapper.findAll('.msg-item');
    expect(msgItems.length).toBe(2);
    expect(wrapper.text()).toContain('agent-b');
    expect(wrapper.text()).toContain('orchestrator');
    wrapper.unmount();
  });

  it('does not crash when /api/protocol/list fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountProtocols();
    expect(wrapper.find('.protocols-view').exists()).toBe(true);
    // Error state shown
    expect(wrapper.text()).toContain('Failed to load protocols');
    wrapper.unmount();
  });

  it('reloads protocols when refresh button is clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/protocol/list': {
        protocols: [{ name: 'test-proto', version: '1.0', participants: [], description: '' }],
        count: 1,
      },
    }));
    const wrapper = await mountProtocols();
    const callsBefore = global.fetch.mock.calls.length;
    const buttons = wrapper.findAll('button');
    const refreshBtn = buttons.find((b) => b.text().includes('Refresh'));
    expect(refreshBtn).toBeTruthy();
    await refreshBtn.trigger('click');
    await flushPromises();
    await flushPromises();
    expect(global.fetch.mock.calls.length).toBeGreaterThan(callsBefore);
    wrapper.unmount();
  });

  it('displays version badge for each protocol', async () => {
    mockFetch(defaultRoutes({
      '/api/protocol/list': {
        protocols: [
          { name: 'proto-v1', version: '1.0', participants: [], description: '' },
          { name: 'proto-v2', version: '2.5', participants: [], description: '' },
        ],
        count: 2,
      },
    }));
    const wrapper = await mountProtocols();
    const text = wrapper.text();
    expect(text).toContain('v1.0');
    expect(text).toContain('v2.5');
    wrapper.unmount();
  });

  it('shows participant badges in protocol row', async () => {
    mockFetch(defaultRoutes({
      '/api/protocol/list': {
        protocols: [
          { name: 'multi-part', version: '1.0', participants: ['alpha', 'beta', 'gamma'], description: '' },
        ],
        count: 1,
      },
    }));
    const wrapper = await mountProtocols();
    const text = wrapper.text();
    expect(text).toContain('alpha');
    expect(text).toContain('beta');
    // Third participant is truncated with +N
    expect(text).toContain('+1');
    wrapper.unmount();
  });
});