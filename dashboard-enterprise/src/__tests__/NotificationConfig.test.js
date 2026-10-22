// Tests for NotificationConfig.vue — channel/rule/template CRUD against the
// real backend contract (/api/notifications/*). The masked-secret cases are
// the important ones: the API returns secrets as "***" and a PUT replaces the
// whole config object, so the form must never round-trip the mask.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockShowConfirm = vi.fn(() => Promise.resolve(true));
vi.mock('../composables/useConfirm.js', () => ({
  useConfirm: () => ({ showConfirm: mockShowConfirm }),
  confirmState: { visible: false, message: '', title: '', confirmText: '', cancelText: '', tone: 'danger', _resolve: null },
}));

import NotificationConfig from '../views/NotificationConfig.vue';

function ok(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function setupApi(initial = {}) {
  const data = {
    channels: [],
    rules: [],
    templates: [],
    ...initial,
  };
  const calls = [];

  global.fetch = vi.fn((url, init = {}) => {
    const u = String(url).split('?')[0];
    const method = (init.method || 'GET').toUpperCase();
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: u, method, body });

    if (method === 'GET' && u === '/api/notifications/channels') {
      return ok({ status: 'ok', channels: data.channels, count: data.channels.length });
    }
    if (method === 'GET' && u === '/api/notifications/rules') {
      return ok({ status: 'ok', rules: data.rules, count: data.rules.length });
    }
    if (method === 'GET' && u === '/api/notifications/templates') {
      return ok({ status: 'ok', templates: data.templates, count: data.templates.length });
    }

    if (method === 'POST' && u === '/api/notifications/channels') {
      const channel = { channel_id: 'ch_new', ...body };
      data.channels.push(channel);
      return ok({ status: 'ok', channel });
    }
    if (method === 'POST' && u === '/api/notifications/rules') {
      const rule = { rule_id: 'rule_new', ...body };
      data.rules.push(rule);
      return ok({ status: 'ok', rule });
    }
    if (method === 'POST' && u === '/api/notifications/templates') {
      const template = { template_id: 'tpl_new', ...body };
      data.templates.push(template);
      return ok({ status: 'ok', template });
    }

    if (method === 'PUT' && u.startsWith('/api/notifications/channels/')) {
      const id = decodeURIComponent(u.split('/').pop());
      const idx = data.channels.findIndex((c) => c.channel_id === id);
      data.channels[idx] = { ...data.channels[idx], ...body };
      return ok({ status: 'ok', channel: data.channels[idx] });
    }
    if (method === 'PUT' && u.startsWith('/api/notifications/rules/')) {
      const id = decodeURIComponent(u.split('/').pop());
      const idx = data.rules.findIndex((r) => r.rule_id === id);
      data.rules[idx] = { ...data.rules[idx], ...body };
      return ok({ status: 'ok', rule: data.rules[idx] });
    }

    if (method === 'DELETE' && u.startsWith('/api/notifications/channels/')) {
      const id = decodeURIComponent(u.split('/').pop());
      data.channels = data.channels.filter((c) => c.channel_id !== id);
      return ok({ status: 'ok', deleted: true });
    }
    if (method === 'DELETE' && u.startsWith('/api/notifications/rules/')) {
      const id = decodeURIComponent(u.split('/').pop());
      data.rules = data.rules.filter((r) => r.rule_id !== id);
      return ok({ status: 'ok', deleted: true });
    }
    if (method === 'DELETE' && u.startsWith('/api/notifications/templates/')) {
      const id = decodeURIComponent(u.split('/').pop());
      data.templates = data.templates.filter((t) => t.template_id !== id);
      return ok({ status: 'ok', deleted: true });
    }

    return ok({ status: 'ok' });
  });

  return { data, calls };
}

function sampleChannel(overrides = {}) {
  return {
    channel_id: 'ch_email',
    name: 'Ops email',
    type: 'email',
    config: {
      host: 'smtp.example.com',
      port: 587,
      from_addr: 'alerts@example.com',
      password: '***',
      use_tls: true,
    },
    description: '',
    enabled: true,
    ...overrides,
  };
}

function sampleRule(overrides = {}) {
  return {
    rule_id: 'rule_1',
    name: 'Notify failures',
    event_type: 'task_failed',
    channel_ids: ['ch_email'],
    template_id: '',
    level: 'error',
    filter: {},
    description: '',
    enabled: true,
    ...overrides,
  };
}

function sampleTemplate(overrides = {}) {
  return {
    template_id: 'tpl_1',
    name: 'Task failed',
    subject: 'Task failed',
    body: 'Task {task_id} failed',
    description: '',
    ...overrides,
  };
}

async function settle() {
  await flushPromises();
  await flushPromises();
  await flushPromises();
}

function field(overlay, labelText) {
  return [...overlay.querySelectorAll('.form-label')].find((el) => {
    const span = el.querySelector('span');
    return span && span.textContent.trim() === labelText;
  });
}

function setInput(el, value) {
  el.value = value;
  el.dispatchEvent(new Event('input'));
}

function setCheckbox(el, checked) {
  el.checked = checked;
  el.dispatchEvent(new Event('change'));
}

describe('NotificationConfig.vue', () => {
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

  async function mountView() {
    const wrapper = mount(NotificationConfig);
    await settle();
    return wrapper;
  }

  function tab(wrapper, text) {
    return wrapper.findAll('.cfg-tab').find((b) => b.text().includes(text));
  }

  it('loads channels, rules and templates from the three list endpoints', async () => {
    const { calls } = setupApi({
      channels: [sampleChannel()],
      rules: [sampleRule()],
      templates: [sampleTemplate()],
    });
    const wrapper = await mountView();

    expect(calls.map((c) => c.url)).toEqual(expect.arrayContaining([
      '/api/notifications/channels',
      '/api/notifications/rules',
      '/api/notifications/templates',
    ]));
    expect(wrapper.text()).toContain('Ops email');
    expect(wrapper.text()).toContain('smtp.example.com:587');

    await tab(wrapper, 'Rules').trigger('click');
    expect(wrapper.findAll('section')[1].isVisible()).toBe(true);
    expect(wrapper.text()).toContain('Notify failures');

    await tab(wrapper, 'Templates').trigger('click');
    expect(wrapper.findAll('section')[2].isVisible()).toBe(true);
    expect(wrapper.text()).toContain('Task failed');

    wrapper.unmount();
  });

  it('creates an email channel with the structured config', async () => {
    const { calls } = setupApi();
    const wrapper = await mountView();

    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('New channel'));
    await newBtn.trigger('click');
    await settle();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();

    setInput(field(overlay, 'Name').querySelector('input'), 'Alert email');
    const typeSelect = field(overlay, 'Type').querySelector('select');
    typeSelect.value = 'email';
    typeSelect.dispatchEvent(new Event('change'));
    await settle();

    setInput(field(overlay, 'SMTP host').querySelector('input'), 'smtp.example.com');
    setInput(field(overlay, 'From address').querySelector('input'), 'alerts@example.com');
    setInput(field(overlay, 'Password').querySelector('input'), 's3cret');
    setInput(
      field(overlay, 'Default recipients (comma separated)').querySelector('input'),
      'ops@example.com, dev@example.com',
    );

    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();

    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/notifications/channels');
    expect(post).toBeTruthy();
    expect(post.body).toMatchObject({
      name: 'Alert email',
      type: 'email',
      description: '',
      enabled: true,
      config: {
        host: 'smtp.example.com',
        port: 587,
        from_addr: 'alerts@example.com',
        password: 's3cret',
        to_addrs: ['ops@example.com', 'dev@example.com'],
        use_tls: true,
        use_ssl: false,
      },
    });
    expect(JSON.stringify(post.body)).not.toContain('***');

    wrapper.unmount();
  });

  it('editing the name alone omits config so masked secrets are never written back', async () => {
    const { calls } = setupApi({ channels: [sampleChannel()] });
    const wrapper = await mountView();

    const editBtn = wrapper.findAll('button[title="Edit"]')[0];
    await editBtn.trigger('click');
    await settle();

    const overlay = document.querySelector('.modal-overlay');
    expect(overlay).toBeTruthy();
    // Config editor is collapsed on edit, so masked values cannot be submitted.
    expect(field(overlay, 'SMTP host')).toBeUndefined();
    expect(overlay.textContent).toContain('Configuration is left untouched');

    setInput(field(overlay, 'Name').querySelector('input'), 'Ops email (renamed)');
    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();

    const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/notifications/channels/ch_email');
    expect(put).toBeTruthy();
    expect(put.body).toMatchObject({ name: 'Ops email (renamed)', enabled: true });
    expect(put.body).not.toHaveProperty('config');
    expect(put.body).not.toHaveProperty('type');
    expect(JSON.stringify(put.body)).not.toContain('***');

    wrapper.unmount();
  });

  it('editing the config requires re-entering secrets and never sends the mask', async () => {
    const { calls } = setupApi({ channels: [sampleChannel()] });
    const wrapper = await mountView();

    await wrapper.findAll('button[title="Edit"]')[0].trigger('click');
    await settle();

    const overlay = document.querySelector('.modal-overlay');
    const editConfigBtn = [...overlay.querySelectorAll('button')].find((b) => b.textContent.includes('Edit configuration'));
    editConfigBtn.click();
    await settle();

    expect(overlay.textContent).toContain('Saving the configuration replaces the whole object');

    setInput(field(overlay, 'SMTP host').querySelector('input'), 'smtp2.example.com');
    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();

    const put = calls.find((c) => c.method === 'PUT' && c.url === '/api/notifications/channels/ch_email');
    expect(put).toBeTruthy();
    expect(put.body.config.host).toBe('smtp2.example.com');
    expect(put.body.config).not.toHaveProperty('password');
    expect(JSON.stringify(put.body)).not.toContain('***');

    wrapper.unmount();
  });

  it('creates a rule only after at least one channel is selected', async () => {
    const { calls } = setupApi({
      channels: [
        sampleChannel(),
        sampleChannel({ channel_id: 'ch_hook', name: 'Hook', type: 'webhook', config: { url: 'https://hooks.example.com/x' } }),
      ],
    });
    const wrapper = await mountView();

    await tab(wrapper, 'Rules').trigger('click');
    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('New rule'));
    await newBtn.trigger('click');
    await settle();

    const overlay = document.querySelector('.modal-overlay');
    setInput(field(overlay, 'Name').querySelector('input'), 'Critical failures');
    setInput(field(overlay, 'Payload filter (JSON)').querySelector('textarea'), '{"severity": "critical"}');
    field(overlay, 'Level').querySelector('select').value = 'error';
    field(overlay, 'Level').querySelector('select').dispatchEvent(new Event('change'));

    // No channel selected → validation blocks the request.
    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();
    expect(overlay.querySelector('.cfg-form-error').textContent).toContain('Select at least one delivery channel');
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/notifications/rules')).toBe(false);

    const channelChecks = [...overlay.querySelectorAll('.cfg-check input[type="checkbox"]')];
    const emailCheck = channelChecks.find((el) => el.closest('label').textContent.includes('Ops email'));
    setCheckbox(emailCheck, true);

    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();

    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/notifications/rules');
    expect(post).toBeTruthy();
    expect(post.body).toEqual({
      name: 'Critical failures',
      event_type: 'task_failed',
      channel_ids: ['ch_email'],
      template_id: '',
      filter: { severity: 'critical' },
      level: 'error',
      description: '',
      enabled: true,
    });

    wrapper.unmount();
  });

  it('creates and deletes a template (the API has no template update)', async () => {
    const { calls } = setupApi();
    const wrapper = await mountView();

    await tab(wrapper, 'Templates').trigger('click');
    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('New template'));
    await newBtn.trigger('click');
    await settle();

    const overlay = document.querySelector('.modal-overlay');
    setInput(field(overlay, 'Name').querySelector('input'), 'Failure notice');
    setInput(field(overlay, 'Subject').querySelector('input'), 'Task failed');
    setInput(field(overlay, 'Body').querySelector('textarea'), 'Task {task_id} failed');
    overlay.querySelector('.modal-actions .btn--primary').click();
    await settle();

    const post = calls.find((c) => c.method === 'POST' && c.url === '/api/notifications/templates');
    expect(post).toBeTruthy();
    expect(post.body).toEqual({
      name: 'Failure notice',
      subject: 'Task failed',
      body: 'Task {task_id} failed',
      description: '',
    });
    expect(wrapper.text()).toContain('Failure notice');

    const deleteBtn = wrapper.findAll('.cfg-table--templates button.btn-icon--danger')[0];
    await deleteBtn.trigger('click');
    await settle();

    const del = calls.find((c) => c.method === 'DELETE' && c.url === '/api/notifications/templates/tpl_new');
    expect(del).toBeTruthy();
    expect(wrapper.text()).not.toContain('Failure notice');

    wrapper.unmount();
  });

  it('shows a per-tab empty state when there are no templates', async () => {
    setupApi({ channels: [sampleChannel()] });
    const wrapper = await mountView();

    await tab(wrapper, 'Templates').trigger('click');
    const templatesSection = wrapper.findAll('section')[2];
    expect(templatesSection.isVisible()).toBe(true);
    expect(templatesSection.text()).toContain('No templates yet');

    wrapper.unmount();
  });

  it('shows the error state when the configuration cannot be loaded', async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error('offline')));
    const wrapper = await mountView();

    expect(wrapper.text()).toContain('Failed to load notification configuration');
    expect(wrapper.text()).toContain('offline');
    expect(wrapper.find('.cfg-table').exists()).toBe(false);

    wrapper.unmount();
  });
});
