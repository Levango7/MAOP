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

  // ── Alertmanager 入站告警（GET /api/alerts）──
  function mockInbound({ alerts = 'ok', rules = 'ok' } = {}) {
    const urls = [];
    const impl = vi.fn((url) => {
      const u = String(url);
      urls.push(u);
      let body = {};
      let ok = true;
      let status = 200;
      if (u === '/api/audit/alert/rules') {
        body = { rules: [{ id: 'r1', name: 'cpu-hot', metric: 'cpu_usage', condition: '>', threshold: 90, channel: 'email', enabled: true }], count: 1 };
        if (rules === 'missing') { ok = false; status = 404; body = { error: 'requires enterprise edition' }; }
      } else if (u.startsWith('/api/audit/alert/history')) {
        body = { alerts: [], count: 0 };
      } else if (u === '/api/hooks') {
        body = { hooks: [], count: 0 };
      } else if (u.startsWith('/api/alerts')) {
        if (alerts === 'fail') { ok = false; status = 500; body = { error: 'boom' }; }
        else if (alerts === 'empty') body = { alerts: [], count: 0, total: 0 };
        else {
          body = {
            alerts: [{
              fingerprint: 'fp-1', status: 'firing', starts_at: '2026-10-21T06:41:30.000Z',
              alertname: 'MAOPNoActiveAgents', severity: 'critical', instance: '10.0.0.7:8080',
              agent: 'code-reviewer', tenant_id: '', summary: 'No active agents for 5m',
              description: 'all agents offline', receiver: 'webhook',
              external_url: 'http://am:9093', received_at: 1760000000,
            }],
            count: 1, total: 7,
          };
        }
      }
      return Promise.resolve({
        ok, status,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    impl.urls = urls;
    return impl;
  }

  it('renders Alertmanager inbound alerts and asks for them by query string', async () => {
    global.fetch = mockInbound();
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    expect(global.fetch.urls).toContain('/api/alerts?limit=50');
    const rows = wrapper.findAll('.ai-row');
    expect(rows).toHaveLength(2); // 表头 + 一条
    const text = rows[1].text();
    expect(text).toContain('MAOPNoActiveAgents');
    expect(text).toContain('critical');
    expect(text).toContain('Firing');
    expect(text).toContain('10.0.0.7:8080');
    expect(text).toContain('No active agents for 5m');
    expect(wrapper.text()).toContain('7'); // total 徽标来自后端的 total，不是本页条数
    wrapper.unmount();
  });

  it('separates an unreadable inbound ledger from an empty one', async () => {
    global.fetch = mockInbound({ alerts: 'fail' });
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('Could not read inbound alerts');
    expect(text).not.toContain('No inbound alerts yet');
    wrapper.unmount();
  });

  it('says no inbound alerts when the ledger really is empty', async () => {
    global.fetch = mockInbound({ alerts: 'empty' });
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('No inbound alerts yet');
    expect(text).not.toContain('Could not read inbound alerts');
    wrapper.unmount();
  });

  it('still lists inbound alerts when the enterprise rule API is absent', async () => {
    // 个人版：/api/audit/alert/rules 404，但 Alertmanager 入站告警是有数据的。
    localStorage.setItem('maop_edition', JSON.stringify({ edition: 'personal' }));
    global.fetch = mockInbound({ rules: 'missing' });
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    expect(wrapper.findAll('.ai-row')).toHaveLength(2);
    expect(wrapper.text()).toContain('MAOPNoActiveAgents');
    wrapper.unmount();
  });

  it('scopes the history drawer to one rule through the query string', async () => {
    // api.get 的第二参数是 fetch init，不是查询参数 —— 传对象会被静默丢掉，
    // 抽屉于是把所有规则的历史当成这一条的。参数必须进 URL。
    global.fetch = mockInbound();
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const historyBtn = wrapper.findAll('button').find((b) => b.attributes('title') === 'History');
    await historyBtn.trigger('click');
    await flushPromises();
    await flushPromises();

    expect(global.fetch.urls).toContain('/api/audit/alert/history?rule_id=r1');
    wrapper.unmount();
  });
});
