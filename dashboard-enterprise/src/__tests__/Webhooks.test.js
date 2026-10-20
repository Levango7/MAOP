import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Webhooks from '../views/Webhooks.vue';

describe('Webhooks.vue', () => {
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

  const hooks = [
    { id: 'h1', name: 'deploy', event: 'step.post_run', url: 'https://example.com/hook', enabled: true },
  ];
  const events = [
    { name: 'step.post_run', phase: 'step', domain: 'execution' },
    { name: 'agent.post_dispatch', phase: 'agent', domain: 'agent' },
  ];

  function mockFetch() {
    return vi.fn((url, init = {}) => {
      const u = String(url);
      const method = (init.method || 'GET').toUpperCase();
      let body = {};
      if (u === '/api/hooks' && method === 'GET') body = { hooks, count: hooks.length };
      else if (u === '/api/hooks/events') body = { events, count: events.length };
      else if (u === '/api/hooks' && method === 'POST') body = { id: 'h2', ...JSON.parse(init.body || '{}') };
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  async function mountPage() {
    global.fetch = mockFetch();
    const wrapper = mount(Webhooks, {
      global: { stubs: { PageHeader: { template: '<slot />' } } },
    });
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('reads hooks from the backend hooks key and shows the single event', async () => {
    const wrapper = await mountPage();
    expect(wrapper.findAll('.wh-row:not(.wh-row--head)').length).toBe(1);
    expect(wrapper.text()).toContain('deploy');
    expect(wrapper.text()).toContain('step.post_run');
    wrapper.unmount();
  });

  it('creates a hook with the backend contract (event, no events/auth fields)', async () => {
    const wrapper = await mountPage();
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('New Webhook'));
    expect(createBtn).toBeTruthy();
    await createBtn.trigger('click');
    await flushPromises();

    const overlay = wrapper.find('.modal-overlay');
    expect(overlay.exists()).toBe(true);
    const textInputs = overlay.findAll('input[type="text"]');
    await textInputs[0].setValue('notify');
    await textInputs[1].setValue('https://example.com/notify');

    const select = overlay.find('select');
    expect(select.findAll('option').map((o) => o.element.value)).toContain('step.post_run');
    await select.setValue('step.post_run');

    const saveBtn = overlay.findAll('button').find((b) => b.text().includes('Save'));
    expect(saveBtn).toBeTruthy();
    await saveBtn.trigger('click');
    await flushPromises();

    const post = global.fetch.mock.calls.find(([, init]) => (init?.method || 'GET') === 'POST');
    expect(post).toBeTruthy();
    const body = JSON.parse(post[1].body);
    expect(body).toEqual({
      name: 'notify',
      event: 'step.post_run',
      url: 'https://example.com/notify',
      enabled: true,
    });
    wrapper.unmount();
  });
});
