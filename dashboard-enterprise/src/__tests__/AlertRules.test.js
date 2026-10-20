import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import AlertRules from '../views/AlertRules.vue';

describe('AlertRules.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_edition', JSON.stringify({ edition: 'enterprise' }));
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    localStorage.removeItem('maop_edition');
    document.body.replaceChildren();
  });

  function mockFetch() {
    return vi.fn((url) => {
      const u = String(url);
      let body = {};
      if (u === '/api/audit/alert/rules') {
        body = {
          rules: [{
            id: 'r1', name: 'cpu-hot', metric: 'cpu_usage', condition: '>',
            threshold: 90, channel: 'email', enabled: true,
          }],
          count: 1,
        };
      } else if (u.startsWith('/api/audit/alert/history')) {
        body = {
          alerts: [{
            id: 'a1', triggered_at: '2026-10-20T10:00:00Z',
            rule_name: 'cpu-hot', value: 7.5, channel: 'email', status: 'fired',
          }],
          count: 1,
        };
      } else if (u === '/api/hooks') {
        body = { hooks: [{ id: 'h1', name: 'notify' }], count: 1 };
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  it('offers hooks returned under the backend hooks key as webhook targets', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Rule'));
    expect(createBtn).toBeTruthy();
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();
    const selects = [...overlay.querySelectorAll('select')];
    const channelSelect = selects.find((s) => [...s.options].some((o) => o.value === 'webhook'));
    expect(channelSelect).toBeTruthy();
    channelSelect.value = 'webhook';
    channelSelect.dispatchEvent(new Event('change'));
    await flushPromises();

    const webhookSelect = [...overlay.querySelectorAll('select')]
      .find((s) => [...s.options].some((o) => o.textContent === 'notify'));
    expect(webhookSelect).toBeTruthy();
    wrapper.unmount();
  });

  it('renders triggered history returned under the backend alerts key', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const historyBtn = wrapper.findAll('button').find((b) => b.attributes('title') === 'History');
    expect(historyBtn).toBeTruthy();
    await historyBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    const drawerText = document.body.textContent;
    expect(drawerText).toContain('cpu-hot');
    expect(drawerText).toContain('7.5');
    wrapper.unmount();
  });
});
