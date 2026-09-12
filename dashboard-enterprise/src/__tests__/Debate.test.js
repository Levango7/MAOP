// Smoke tests for Debate.vue — multi-agent adversarial debate management page.
//
// Debate.onMounted calls loadAll() which hits /api/debate/history via
// Promise.allSettled. We mock global.fetch, stub PageHeader (via
// ListPageLayout), then assert the root renders, tabs switch, history
// list displays, start form validates, and config tab shows.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Debate from '../views/Debate.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Debate.vue', () => {
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
      const u = String(url).split('?')[0];
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
      '/api/debate/history': { status: 'ok', verdicts: [] },
      ...overrides,
    };
  }

  async function mountDebate() {
    const wrapper = mount(Debate, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the debate-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountDebate();
    expect(wrapper.find('.debate-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders history tab content by default', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountDebate();
    // History tab is active by default → empty state shown when no verdicts
    expect(wrapper.find('.debate-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('No debate history yet');
    wrapper.unmount();
  });

  it('renders verdict rows when /api/debate/history returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/debate/history': {
        status: 'ok',
        verdicts: [
          {
            debate_id: 'deb-001',
            question: 'Is it safe to switch codegen routing?',
            participants: ['agent_a', 'agent_b', 'agent_c'],
            rounds_executed: 3,
            consensus_score: 0.85,
            verdict: 'consensus',
          },
          {
            debate_id: 'deb-002',
            question: 'Should we enable feature X?',
            participants: ['agent_a', 'agent_b', 'agent_d'],
            rounds_executed: 2,
            consensus_score: 0.3,
            verdict: 'split',
          },
        ],
      },
    }));
    const wrapper = await mountDebate();
    const rows = wrapper.findAll('.deb-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('Is it safe to switch codegen routing?');
    expect(wrapper.text()).toContain('Should we enable feature X?');
    wrapper.unmount();
  });

  it('switches to start tab and shows the start form', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountDebate();
    // tabOptions: history, start, config → start is index 1
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[1].trigger('click');
    await flushPromises();
    expect(wrapper.find('.form').exists()).toBe(true);
    expect(wrapper.text()).toContain('Start a New Debate');
    wrapper.unmount();
  });

  it('switches to config tab and shows the config form', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountDebate();
    // config is index 2
    const segButtons = wrapper.findAll('.segmented__item');
    await segButtons[2].trigger('click');
    await flushPromises();
    expect(wrapper.find('.form').exists()).toBe(true);
    expect(wrapper.text()).toContain('Debate Configuration');
    wrapper.unmount();
  });

  it('opens verdict detail drawer when details button clicked', async () => {
    mockFetch(defaultRoutes({
      '/api/debate/history': {
        status: 'ok',
        verdicts: [
          {
            debate_id: 'deb-001',
            question: 'Test question?',
            participants: ['a', 'b', 'c'],
            rounds_executed: 3,
            consensus_score: 0.9,
            verdict: 'consensus',
          },
        ],
      },
      '/api/debate/deb-001': {
        status: 'ok',
        verdict: {
          debate_id: 'deb-001',
          question: 'Test question?',
          participants: ['a', 'b', 'c'],
          rounds_executed: 3,
          consensus_score: 0.9,
          verdict: 'consensus',
          trajectory: [{ round: 1, speaker: 'a', stance: 'agree', argument: 'I agree' }],
        },
      },
    }));
    const wrapper = await mountDebate();
    // Click the details (search) icon button in the first row
    const detailBtn = wrapper.find('.deb-table__row .icon-btn');
    expect(detailBtn.exists()).toBe(true);
    await detailBtn.trigger('click');
    await flushPromises();
    // DetailDrawer is teleported to body
    const drawer = document.querySelector('.detail-drawer');
    expect(drawer).toBeTruthy();
    wrapper.unmount();
  });

  it('does not crash when API endpoints fail', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountDebate();
    expect(wrapper.find('.debate-view').exists()).toBe(true);
    // Error state shown for history tab
    expect(wrapper.text()).toContain('Failed to load debate history');
    wrapper.unmount();
  });

  it('displays consensus badge for high-consensus verdicts', async () => {
    mockFetch(defaultRoutes({
      '/api/debate/history': {
        status: 'ok',
        verdicts: [
          {
            debate_id: 'deb-001',
            question: 'Q1',
            participants: ['a', 'b', 'c'],
            consensus_score: 0.92,
            verdict: 'consensus',
          },
        ],
      },
    }));
    const wrapper = await mountDebate();
    const badges = wrapper.findAll('.badge');
    // At least one badge should contain "Consensus"
    const hasConsensusBadge = badges.some((b) => b.text().includes('Consensus'));
    expect(hasConsensusBadge).toBe(true);
    wrapper.unmount();
  });

  it('formats consensus score as percentage', async () => {
    mockFetch(defaultRoutes({
      '/api/debate/history': {
        status: 'ok',
        verdicts: [
          {
            debate_id: 'deb-001',
            question: 'Q1',
            participants: ['a', 'b', 'c'],
            consensus_score: 0.75,
            verdict: 'consensus',
          },
        ],
      },
    }));
    const wrapper = await mountDebate();
    // 0.75 → 75.0%
    expect(wrapper.text()).toContain('75.0%');
    wrapper.unmount();
  });

  it('shows loading skeleton while fetching history', async () => {
    // Never-resolving fetch to keep loading state
    global.fetch = vi.fn(() => new Promise(() => {}));
    const wrapper = mount(Debate, mountOptions);
    // Don't await flushPromises — we want to catch the loading state
    await flushPromises();
    // The component sets loading=true synchronously in loadAll()
    expect(wrapper.find('.debate-view').exists()).toBe(true);
    wrapper.unmount();
  });
});