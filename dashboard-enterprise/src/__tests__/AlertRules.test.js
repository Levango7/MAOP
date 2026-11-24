import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import AlertRules from '../views/AlertRules.vue';
import { toastState } from '../composables/useToast.js';

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

  function mockFetch(ruleOverrides = {}) {
    return vi.fn((url) => {
      const u = String(url);
      let body = {};
      if (u === '/api/audit/alert/rules') {
        body = {
          // AuditAlertRule: keyed by rule_id, operator/value live in `condition`.
          rules: [{
            rule_id: 'r1', name: 'cpu-hot', description: '', enabled: true,
            condition_type: 'threshold', condition: { op: '>', value: 90, window_s: 300 },
            action: 'email', action_config: { email: 'ops@example.com' },
            severity: 'warning', tenant_id: '', created_at: 1763000000,
            updated_at: 1763000000, created_by: 'alice',
            ...ruleOverrides,
          }],
          count: 1,
        };
      } else if (u.startsWith('/api/audit/alert/history')) {
        body = {
          // AuditAlert: no per-alert metric value or delivery channel.
          alerts: [{
            alert_id: 'a1', rule_id: 'r1', triggered_at: 1763000000, event_id: 'ev1',
            severity: 'warning', message: 'cpu-hot fired', acknowledged: false,
            acknowledged_by: '', acknowledged_at: null, tenant_id: '',
            metadata: { rule_name: 'cpu-hot', event_actor: 'system' },
          }],
          count: 1,
        };
      } else if (u === '/api/notifications/channels') {
        body = { channels: [
          { channel_id: 'ch-ok', name: 'Ops webhook', type: 'webhook', enabled: true },
          { channel_id: 'ch-off', name: 'Disabled webhook', type: 'webhook', enabled: false },
          { channel_id: 'ch-mail', name: 'Mail channel', type: 'email', enabled: true },
        ], count: 3 };
      }
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  it('offers only enabled notification webhook channels as targets', async () => {
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
      .find((s) => [...s.options].some((o) => o.textContent === 'Ops webhook'));
    expect(webhookSelect).toBeTruthy();
    expect([...webhookSelect.options].map((o) => o.value)).toEqual(['', 'ch-ok']);
    expect(global.fetch.mock.calls.some(([url]) => String(url) === '/api/hooks')).toBe(false);
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
    // The wire carries a message and a severity, not a metric value.
    expect(drawerText).toContain('cpu-hot fired');
    expect(drawerText).toContain('warning');
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
        body = { rules: [{
          rule_id: 'r1', name: 'cpu-hot', description: '', enabled: true,
          condition_type: 'threshold', condition: { op: '>', value: 90, window_s: 300 },
          action: 'email', action_config: { email: 'ops@example.com' }, severity: 'warning',
          tenant_id: '', created_at: 1763000000, updated_at: 1763000000, created_by: 'alice',
        }], count: 1 };
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

  function mockSave(posts) {
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      if (u === '/api/audit/alert/rules' && init && init.method === 'POST') {
        posts.push(JSON.parse(init.body));
      }
      let body = {};
      if (u === '/api/audit/alert/rules') body = { rules: [], count: 0 };
      else if (u.startsWith('/api/audit/alert/history')) body = { alerts: [], count: 0 };
      else if (u === '/api/notifications/channels') body = { channels: [
        { channel_id: 'ch-ok', name: 'Ops webhook', type: 'webhook', enabled: true },
      ], count: 1 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function openCreateAndFill(overlay) {
    const inputs = [...overlay.querySelectorAll('input')];
    const nameInput = inputs[0];
    nameInput.value = 'cpu-hot';
    nameInput.dispatchEvent(new Event('input'));
    const thresholdInput = inputs.find((i) => i.type === 'number' && i.step === 'any');
    thresholdInput.value = '90';
    thresholdInput.dispatchEvent(new Event('input'));
    await flushPromises();
    return inputs;
  }

  it('saves the selected notification channel id in action_config', async () => {
    const posts = [];
    mockSave(posts);
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Rule'));
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    await openCreateAndFill(overlay);
    const selects = [...overlay.querySelectorAll('select')];
    const channelSelect = selects.find((s) => [...s.options].some((o) => o.value === 'webhook'));
    channelSelect.value = 'webhook';
    channelSelect.dispatchEvent(new Event('change'));
    await flushPromises();
    const webhookSelect = [...overlay.querySelectorAll('select')]
      .find((s) => [...s.options].some((o) => o.value === 'ch-ok'));
    webhookSelect.value = 'ch-ok';
    webhookSelect.dispatchEvent(new Event('change'));
    await flushPromises();

    const saveBtn = [...overlay.querySelectorAll('button')].find((b) => b.textContent.includes('Save'));
    saveBtn.dispatchEvent(new Event('click'));
    await flushPromises();
    await flushPromises();

    expect(posts).toHaveLength(1);
    expect(posts[0].action).toBe('webhook');
    expect(posts[0].action_config).toEqual({ channel_id: 'ch-ok' });
    wrapper.unmount();
  });

  it('saves a rule in the backend condition-dict shape with email in action_config', async () => {
    // Q3 缺陷3(重做):condition 必须是 {op, value, window_s, filter} 对象且带
    // condition_type,否则后端 dict 校验 422;收件人走 action_config(JSONB)。
    const posts = [];
    mockSave(posts);
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Rule'));
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    const inputs = await openCreateAndFill(overlay);
    // 默认 channel=email,validate() 拦空/非法邮箱;text 输入依次是 name/description/email。
    const emailInput = inputs.filter((i) => i.type === 'text')[2];
    emailInput.value = 'ops@example.com';
    emailInput.dispatchEvent(new Event('input'));
    await flushPromises();

    const saveBtn = [...overlay.querySelectorAll('button')].find((b) => b.textContent.includes('Save'));
    saveBtn.dispatchEvent(new Event('click'));
    await flushPromises();
    await flushPromises();

    expect(posts).toHaveLength(1);
    const payload = posts[0];
    expect(payload.name).toBe('cpu-hot');
    expect(payload.condition_type).toBe('threshold');
    expect(payload.condition).toEqual({ op: '>', value: 90, window_s: 300, filter: {} });
    expect(payload.action).toBe('email');
    expect(payload.action_config).toEqual({ email: 'ops@example.com' });
    wrapper.unmount();
  });

  it('saves the in-app channel as backend action notify with empty action_config', async () => {
    // 「应用内」直发后端枚举 notify;不再有必 422 的 'inline'。
    const posts = [];
    mockSave(posts);
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Rule'));
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    await openCreateAndFill(overlay);
    const selects = [...overlay.querySelectorAll('select')];
    const channelSelect = selects.find((s) => [...s.options].some((o) => o.value === 'notify'));
    channelSelect.value = 'notify';
    channelSelect.dispatchEvent(new Event('change'));
    await flushPromises();

    const saveBtn = [...overlay.querySelectorAll('button')].find((b) => b.textContent.includes('Save'));
    saveBtn.dispatchEvent(new Event('click'));
    await flushPromises();
    await flushPromises();

    expect(posts).toHaveLength(1);
    const payload = posts[0];
    expect(payload.action).toBe('notify');
    expect(payload.action_config).toEqual({});
    expect(payload.condition_type).toBe('threshold');
    wrapper.unmount();
  });

  it('prefills the email recipient from action_config when editing a rule', async () => {
    global.fetch = mockFetch();
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const editBtn = wrapper.findAll('button').find((b) => b.attributes('title') === 'Edit');
    expect(editBtn).toBeTruthy();
    await editBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();
    const inputs = [...overlay.querySelectorAll('input')];
    // name / description / email —— 收件人从 action_config.email 回填,不再恒默认空。
    const emailInput = inputs.filter((i) => i.type === 'text')[2];
    expect(emailInput.value).toBe('ops@example.com');
    const selects = [...overlay.querySelectorAll('select')];
    const channelSelect = selects.find((s) => [...s.options].some((o) => o.value === 'email'));
    expect(channelSelect.value).toBe('email');
    wrapper.unmount();
  });

  it('prefills the notification channel id when editing a webhook rule', async () => {
    global.fetch = mockFetch({ action: 'webhook', action_config: { channel_id: 'ch-ok' } });
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const editBtn = wrapper.findAll('button').find((b) => b.attributes('title') === 'Edit');
    await editBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    const webhookSelect = [...overlay.querySelectorAll('select')]
      .find((s) => [...s.options].some((o) => o.value === 'ch-ok'));
    expect(webhookSelect).toBeTruthy();
    expect(webhookSelect.value).toBe('ch-ok');
    wrapper.unmount();
  });

  it('surfaces a save failure as an error toast and keeps the modal open', async () => {
    // 异常输出面：后端 4xx/5xx 时 toast.error 且模态保持打开——不假装成功。
    toastState.items.splice(0, toastState.items.length);
    global.fetch = vi.fn((url, init) => {
      const u = String(url);
      if (u === '/api/audit/alert/rules' && init && init.method === 'POST') {
        return Promise.resolve({
          ok: false, status: 500,
          json: () => Promise.resolve({ error: 'boom' }),
          text: () => Promise.resolve('boom'),
        });
      }
      let body = {};
      if (u === '/api/audit/alert/rules') body = { rules: [], count: 0 };
      else if (u.startsWith('/api/audit/alert/history')) body = { alerts: [], count: 0 };
      else if (u === '/api/notifications/channels') body = { channels: [
        { channel_id: 'ch-ok', name: 'Ops webhook', type: 'webhook', enabled: true },
      ], count: 1 };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
    const wrapper = mount(AlertRules);
    await flushPromises();
    await flushPromises();

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Rule'));
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = document.querySelector('.modal-overlay');
    const inputs = await openCreateAndFill(overlay);
    const emailInput = inputs.filter((i) => i.type === 'text')[2];
    emailInput.value = 'ops@example.com';
    emailInput.dispatchEvent(new Event('input'));
    await flushPromises();

    const saveBtn = [...overlay.querySelectorAll('button')].find((b) => b.textContent.includes('Save'));
    saveBtn.dispatchEvent(new Event('click'));
    await flushPromises();
    await flushPromises();

    expect(toastState.items.some((i) => i.tone === 'error')).toBe(true);
    expect(document.querySelector('.modal-overlay')).toBeTruthy();
    toastState.items.splice(0, toastState.items.length);
    wrapper.unmount();
  });
});
