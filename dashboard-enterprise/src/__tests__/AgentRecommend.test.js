import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

// Mock useToast (same reason as the other view suites: it touches a global
// singleton that is irrelevant to what these cases assert).
const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

// The component talks to the backend through `useApiStore().get(...)`. Mocking
// the store (rather than fetch) keeps these cases about the component's own
// branching, not about the transport layer.
const mockGet = vi.fn();
vi.mock('../stores/api.js', () => ({
  useApiStore: () => ({ get: mockGet, post: vi.fn(), put: vi.fn(), delete: vi.fn() }),
}));

import AgentRecommend from '../views/AgentRecommend.vue';

/**
 * The mock shapes below mirror what `GET /api/agent-catalog/recommend` actually
 * returns (`RecommendAgentsPayload`). In particular `scored=false` is a real
 * backend state (no usage history yet) — NOT an error — so the view must render
 * the "run some tasks first" empty state instead of a failure.
 */
describe('AgentRecommend.vue', () => {
  let originalFetch;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalFetch = global.fetch;
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
    localStorage.setItem('maop_locale', 'en');
    mockGet.mockReset();
    mockToast.error.mockClear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function mountView() {
    // No stubs: Card/Badge/EmptyState/AppIcon are pure presentational components,
    // and stubbing them (`stubs: { Card: true }`) renders an empty element that
    // DROPS the default slot — which is exactly the content these cases assert.
    return mount(AgentRecommend);
  }

  it('shows the "no recommendation yet" empty state before any query', () => {
    const wrapper = mountView();
    expect(wrapper.text()).toContain('No recommendation yet');
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('renders recommendations with score, reason and cost', async () => {
    mockGet.mockResolvedValue({
      agents: [
        { agent_name: 'codex', score: 0.9, reason: 'high success rate', estimated_cost: 0.01 },
      ],
      count: 1,
      scored: true,
    });

    const wrapper = mountView();
    await wrapper.find('form').trigger('submit.prevent');
    await flushPromises();

    expect(wrapper.text()).toContain('codex');
    expect(wrapper.text()).toContain('90%');
    expect(wrapper.text()).toContain('high success rate');
  });

  it('treats scored=false as "no history yet", not as a failure', async () => {
    mockGet.mockResolvedValue({ agents: [], count: 0, scored: false });

    const wrapper = mountView();
    await wrapper.find('form').trigger('submit.prevent');
    await flushPromises();

    expect(wrapper.text()).toContain('No usage history yet');
    expect(wrapper.text()).not.toContain('Recommendation failed');
    expect(mockToast.error).not.toHaveBeenCalled();
  });

  it('builds the query string from the form, omitting empty filters', async () => {
    mockGet.mockResolvedValue({ agents: [], count: 0, scored: false });

    const wrapper = mountView();
    await wrapper.find('input[type="text"]').setValue('refactor');
    await wrapper.find('input[type="checkbox"]').setValue(false);
    await wrapper.find('form').trigger('submit.prevent');
    await flushPromises();

    const url = mockGet.mock.calls[0][0];
    expect(url).toContain('/api/agent-catalog/recommend?');
    expect(url).toContain('task_type=refactor');
    expect(url).toContain('prefer_free=false');
    // capabilities left empty ⇒ must not be sent at all
    expect(url).not.toContain('capability=');
  });

  it('surfaces an error message when the request throws', async () => {
    mockGet.mockRejectedValue(new Error('boom'));

    const wrapper = mountView();
    await wrapper.find('form').trigger('submit.prevent');
    await flushPromises();

    expect(wrapper.text()).toContain('Recommendation failed');
  });
});
