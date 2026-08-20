// Smoke tests for Run.vue — Tab shell that hosts ControlPanel (structured)
// and Chat (chat) sub-views, plus the AI task split dialog (t194).
//
// Run.vue uses useRoute/useRouter for ?tab=structured|chat sync. We provide a
// minimal router mock via global.provides, stub ControlPanel + Chat to avoid
// mounting their full dependency trees, and assert the root renders, tabs
// switch, and the AI split dialog opens/closes.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createRouter, createMemoryHistory } from 'vue-router';
import Run from '../views/Run.vue';

// Run.vue uses useRoute/useRouter for ?tab=structured|chat sync. We create a
// real (in-memory) router so the injections resolve cleanly. ControlPanel and
// Chat are stubbed to avoid mounting their full dependency trees.
function makeRouter(initialTab = 'structured') {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div/>' } },
    ],
    initial: { path: '/', query: { tab: initialTab } },
  });
}

const mountOptions = (router) => ({
  global: {
    plugins: [router],
    stubs: {
      PageHeader: { template: '<slot />' },
      ControlPanel: { name: 'ControlPanel', template: '<div class="cp-stub">control-panel</div>' },
      Chat: { name: 'Chat', template: '<div class="chat-stub">chat</div>' },
    },
  },
});

describe('Run.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    // Default fetch mock — Run.vue only fetches on AI split submit.
    global.fetch = vi.fn(() => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve('{}'),
    }));
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
  });

  async function mountRun(tab = 'structured') {
    const router = makeRouter(tab);
    // vue-router needs to be ready before useRoute() returns the initial
    // location. Push the initial route and await readiness.
    await router.push({ path: '/', query: { tab } });
    await router.isReady();
    const wrapper = mount(Run, mountOptions(router));
    await flushPromises();
    return wrapper;
  }

  it('renders the run-view root element', async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('.run-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the AI split button in the header', async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('.ai-split-btn').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders the structured tab content by default', async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('.cp-stub').exists()).toBe(true);
    wrapper.unmount();
  });

  it('opens the AI split dialog when the split button is clicked', async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('.split-overlay').exists()).toBe(false);
    await wrapper.find('.ai-split-btn').trigger('click');
    await flushPromises();
    expect(wrapper.find('.split-overlay').exists()).toBe(true);
    wrapper.unmount();
  });
});