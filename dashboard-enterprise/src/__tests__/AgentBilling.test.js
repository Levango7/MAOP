import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import AgentBilling from '../views/AgentBilling.vue';

describe('AgentBilling.vue', () => {
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
    document.body.replaceChildren();
  });

  function mockFetch() {
    return vi.fn((url) => {
      const u = String(url);
      let body = {};
      if (u === '/api/agents') body = { agents: [{ name: 'coder' }], count: 1 };
      else if (u.startsWith('/api/billing/records')) {
        body = {
          records: [],
          count: 0,
          summary: { total_tokens: 1234, total_cost: 0.5, total_calls: 7 },
        };
      } else if (u.startsWith('/api/billing/summary/')) {
        body = { status: 'ok', summary: {} };
      } else if (u.startsWith('/api/quota/coder')) {
        body = {
          status: 'ok',
          agent_name: 'coder',
          quota: {
            free: { total: 1000, used: 100 },
            prepaid: { total: 0, used: 0 },
            postpaid: { total: 0, used: 0 },
          },
        };
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  it('unwraps the quota envelope and shows bucket usage', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AgentBilling);
    await flushPromises();
    await flushPromises();

    const agentSelect = wrapper.findAll('select')
      .find((s) => s.findAll('option').some((o) => o.element.value === 'coder'));
    expect(agentSelect).toBeTruthy();
    await agentSelect.setValue('coder');
    await flushPromises();
    await flushPromises();

    expect(wrapper.text()).toContain('900');
    wrapper.unmount();
  });

  it('renders the records summary returned by the backend', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AgentBilling);
    await flushPromises();
    await flushPromises();

    // 1234 tokens is formatted as 1.2K by formatTokens.
    expect(wrapper.text()).toContain('1.2K');
    wrapper.unmount();
  });

  it('sends agent_name and since when filtering records', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AgentBilling);
    await flushPromises();
    await flushPromises();

    const filterSelects = wrapper.find('.records-filters').findAll('select');
    await filterSelects[0].setValue('coder');
    await flushPromises();
    expect(
      global.fetch.mock.calls.some(([u]) => String(u).includes('agent_name=coder')),
    ).toBe(true);

    await filterSelects[1].setValue('24h');
    await flushPromises();
    expect(
      global.fetch.mock.calls.some(([u]) => String(u).includes('since=')),
    ).toBe(true);
    wrapper.unmount();
  });
});
