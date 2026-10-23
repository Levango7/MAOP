import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

// Mock useToast
const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

import AgentBilling from '../views/AgentBilling.vue';

/**
 * The mock below mirrors what the backend actually sends — the shapes come
 * from the real producers (``QuotaBucket.quota_bucket_views`` and
 * ``BillingEngine.get_billing_records``), not from what the view would like
 * to read. The previous mock invented a quota shape the backend never
 * produced, so the quota cards stayed broken while this suite stayed green.
 */
describe('AgentBilling.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    // Pin the locale so the label assertions below are deterministic.
    localStorage.setItem('maop_locale', 'en');
    mockToast.success.mockClear();
    mockToast.error.mockClear();
    mockToast.warn.mockClear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    localStorage.removeItem('maop_locale');
    document.body.replaceChildren();
  });

  function mockFetch() {
    return vi.fn((url, init) => {
      const u = String(url);
      const method = (init && init.method) || 'GET';
      let body = {};
      if (u === '/api/agents') {
        body = { agents: [{ name: 'coder' }], count: 1 };
      } else if (u.startsWith('/api/billing/records')) {
        body = {
          status: 'ok',
          records: [
            {
              agent_name: 'coder',
              billing_model: 'per_token',
              tokens_consumed: 1234,
              calls_made: 7,
              cost_usd: 0.5,
              quota_consumed: 1234,
              quota_source: 'free:1000,prepaid:234',
              model: 'claude-3',
              timestamp: '2026-10-23T10:00:00+00:00',
            },
          ],
          count: 1,
          summary: { total_tokens: 1234, total_cost: 0.5, total_calls: 7 },
        };
      } else if (u.startsWith('/api/billing/summary/')) {
        body = {
          status: 'ok',
          summary: {
            agent_name: 'coder',
            billing_model: 'per_token',
            total_tokens: 2222,
            total_cost_usd: 0.25,
            total_calls: 3,
            total_quota_consumed: 2222,
            quota_remaining: { free: 1000, prepaid: 0, postpaid: 0 },
            quota: {
              free: { total: 1000, used: 100, remaining: 900 },
              prepaid: { total: 0, used: 0, remaining: 0 },
              postpaid: { total: 0, used: 0, remaining: 0 },
            },
          },
        };
      } else if (method === 'POST' && u.endsWith('/consume')) {
        body = {
          status: 'ok',
          agent_name: 'coder',
          result: {
            success: false,
            consumed: 0,
            from_free: 0,
            from_prepaid: 0,
            from_postpaid: 0,
            remaining_total: 900,
            reason: 'insufficient quota in prepaid',
          },
        };
      } else if (u.startsWith('/api/quota/coder')) {
        body = {
          status: 'ok',
          agent_name: 'coder',
          quota: {
            free: { total: 1000, used: 100, remaining: 900 },
            prepaid: { total: 0, used: 0, remaining: 0 },
            postpaid: { total: 0, used: 0, remaining: 0 },
          },
        };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountWithAgent(fetchMock) {
    global.fetch = fetchMock;
    const wrapper = mount(AgentBilling);
    await flushPromises();
    await flushPromises();
    const agentSelect = wrapper.findAll('select')
      .find((s) => s.findAll('option').some((o) => o.element.value === 'coder'));
    expect(agentSelect).toBeTruthy();
    await agentSelect.setValue('coder');
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  function findButton(wrapper, text) {
    const button = wrapper.findAll('button').find((b) => b.text().includes(text));
    expect(button, `no button labelled ${text}`).toBeTruthy();
    return button;
  }

  it('unwraps the quota envelope and shows bucket usage', async () => {
    const wrapper = await mountWithAgent(mockFetch());
    expect(wrapper.text()).toContain('900');
    wrapper.unmount();
  });

  it('renders the records summary returned by the backend', async () => {
    const wrapper = await mountWithAgent(mockFetch());
    // 1234 tokens is formatted as 1.2K by formatTokens.
    expect(wrapper.text()).toContain('1.2K');
    wrapper.unmount();
  });

  it('unwraps the per-agent billing summary envelope', async () => {
    const wrapper = await mountWithAgent(mockFetch());
    // 2222 tokens from {summary:{...}} is formatted as 2.2K; the envelope
    // itself would render as an empty/zero row.
    expect(wrapper.text()).toContain('2.2K');
    wrapper.unmount();
  });

  it('sends agent_name and since when filtering records', async () => {
    const fetchMock = mockFetch();
    const wrapper = await mountWithAgent(fetchMock);

    const filterSelects = wrapper.find('.records-filters').findAll('select');
    await filterSelects[0].setValue('coder');
    await flushPromises();
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('agent_name=coder')),
    ).toBe(true);

    await filterSelects[1].setValue('24h');
    await flushPromises();
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('since=')),
    ).toBe(true);
    wrapper.unmount();
  });

  it('renders the record cells from the producer keys', async () => {
    const wrapper = await mountWithAgent(mockFetch());
    const row = wrapper.find('.records-table__row');
    expect(row.exists()).toBe(true);
    const cells = row.findAll('.records-table__td');
    expect(cells.length).toBe(8);
    // The producer sends tokens_consumed / calls_made / cost_usd / model.
    expect(cells[2].text()).toBe('claude-3');
    expect(cells[4].text()).toBe('1,234');
    expect(cells[5].text()).toBe('7');
    expect(cells[6].text()).toBe('$0.5000');
    // quota_source is "free:1000,prepaid:234" — the dominant bucket wins.
    expect(cells[7].text()).toBe('Free');
    wrapper.unmount();
  });

  it('sends the selected bucket and amount when setting a quota', async () => {
    const fetchMock = mockFetch();
    const wrapper = await mountWithAgent(fetchMock);

    const bucketSelect = wrapper.findAll('select')
      .find((s) => s.findAll('option').some((o) => o.element.value === 'prepaid'));
    await bucketSelect.setValue('prepaid');
    await wrapper.find('input[type="number"]').setValue('250');

    await findButton(wrapper, 'Set Quota').trigger('click');
    await flushPromises();

    const putCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).includes('/api/quota/coder') && init && init.method === 'PUT',
    );
    expect(putCall).toBeTruthy();
    expect(JSON.parse(putCall[1].body)).toEqual({ bucket: 'prepaid', total: 250 });
    wrapper.unmount();
  });

  it('sends the bucket with the consume request', async () => {
    const fetchMock = mockFetch();
    const wrapper = await mountWithAgent(fetchMock);

    const bucketSelect = wrapper.findAll('select')
      .find((s) => s.findAll('option').some((o) => o.element.value === 'prepaid'));
    await bucketSelect.setValue('prepaid');
    await wrapper.find('input[type="number"]').setValue('50');

    await findButton(wrapper, 'Consume').trigger('click');
    await flushPromises();

    const postCall = fetchMock.mock.calls.find(
      ([u]) => String(u).includes('/api/quota/coder/consume'),
    );
    expect(postCall).toBeTruthy();
    expect(JSON.parse(postCall[1].body)).toEqual({ bucket: 'prepaid', amount: 50 });
    wrapper.unmount();
  });

  it('reports a failed consume as an error, not a success', async () => {
    const wrapper = await mountWithAgent(mockFetch());

    await wrapper.find('input[type="number"]').setValue('50');
    await findButton(wrapper, 'Consume').trigger('click');
    await flushPromises();

    expect(mockToast.error).toHaveBeenCalled();
    expect(mockToast.success).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('refuses an empty amount instead of silently consuming zero', async () => {
    const fetchMock = mockFetch();
    const wrapper = await mountWithAgent(mockFetch());

    await wrapper.find('input[type="number"]').setValue('');

    await findButton(wrapper, 'Consume').trigger('click');
    await flushPromises();

    expect(mockToast.warn).toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes('/consume')),
    ).toBe(false);
    wrapper.unmount();
  });
});
