// Smoke tests for Feedback.vue — User feedback collection page.
//
// Feedback.onMounted calls loadAll() which hits /api/feedback (list)
// and /api/feedback/summary via Promise.allSettled. We mock global.fetch,
// stub PageHeader, then assert the root renders, feedback rows display,
// submit modal opens, summary tab shows KPIs, and error state shows
// on API failure.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import Feedback from '../views/Feedback.vue';

const mountOptions = {
  global: {
    stubs: {
      PageHeader: { template: '<slot />' },
    },
  },
};

describe('Feedback.vue', () => {
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
      const u = String(url);
      const base = u.split('?')[0];
      const body = routes[u] ?? routes[base] ?? {};
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    });
  }

  function defaultRoutes(overrides = {}) {
    return {
      '/api/feedback': { status: 'ok', data: [], total: 0, page: 1, page_size: 20 },
      '/api/feedback/summary': {
        status: 'ok',
        summary: {
          total: 0,
          average_rating: 0,
          rating_distribution: {},
          comment_count: 0,
          tag_frequency: {},
        },
      },
      ...overrides,
    };
  }

  async function mountFeedback() {
    const wrapper = mount(Feedback, mountOptions);
    await flushPromises();
    await flushPromises();
    return wrapper;
  }

  it('renders the fb-view root element', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFeedback();
    expect(wrapper.find('.fb-view').exists()).toBe(true);
    wrapper.unmount();
  });

  it('renders empty state when no feedback exists', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFeedback();
    expect(wrapper.text()).toContain('No feedback yet');
    wrapper.unmount();
  });

  it('renders feedback rows when /api/feedback returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/feedback': {
        status: 'ok',
        data: [
          { feedback_id: 'fb_1', target_type: 'agent', target_id: 'coder', rating: 5, comment: 'Great work', tags: ['fast', 'accurate'], user_id: 'u1', created_at: 1700000000, updated_at: 1700000000 },
          { feedback_id: 'fb_2', target_type: 'task', target_id: 'task-42', rating: 3, comment: 'Could be better', tags: [], user_id: 'u2', created_at: 1700000001, updated_at: 1700000001 },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      },
    }));
    const wrapper = await mountFeedback();
    const rows = wrapper.findAll('.fb-table__row');
    expect(rows.length).toBe(2);
    expect(wrapper.text()).toContain('coder');
    expect(wrapper.text()).toContain('task-42');
    wrapper.unmount();
  });

  it('opens submit feedback modal when Submit Feedback button clicked', async () => {
    mockFetch(defaultRoutes());
    const wrapper = await mountFeedback();
    const buttons = wrapper.findAll('button');
    const submitBtn = buttons.find((b) => b.text().includes('Submit Feedback'));
    expect(submitBtn).toBeTruthy();
    await submitBtn.trigger('click');
    await flushPromises();
    const modal = document.querySelector('.modal-overlay');
    expect(modal).toBeTruthy();
    wrapper.unmount();
  });

  it('renders summary KPIs when summary endpoint returns data', async () => {
    mockFetch(defaultRoutes({
      '/api/feedback/summary': {
        status: 'ok',
        summary: {
          total: 50,
          average_rating: 4.2,
          rating_distribution: { '5': 30, '4': 10, '3': 5, '2': 3, '1': 2 },
          comment_count: 25,
          tag_frequency: { 'fast': 10, 'accurate': 8, 'slow': 3 },
        },
      },
    }));
    const wrapper = await mountFeedback();
    // Switch to summary tab
    const segButtons = wrapper.findAll('.segmented__item');
    const summaryTab = segButtons.find((b) => b.text().includes('Summary'));
    expect(summaryTab).toBeTruthy();
    await summaryTab.trigger('click');
    await flushPromises();
    expect(wrapper.find('.fb-kpi-grid').exists()).toBe(true);
    expect(wrapper.text()).toContain('50');
    expect(wrapper.text()).toContain('4.20');
    wrapper.unmount();
  });

  it('renders rating distribution bars in summary', async () => {
    mockFetch(defaultRoutes({
      '/api/feedback/summary': {
        status: 'ok',
        summary: {
          total: 10,
          average_rating: 4.0,
          rating_distribution: { '5': 6, '4': 2, '3': 1, '2': 1, '1': 0 },
          comment_count: 5,
          tag_frequency: {},
        },
      },
    }));
    const wrapper = await mountFeedback();
    const segButtons = wrapper.findAll('.segmented__item');
    const summaryTab = segButtons.find((b) => b.text().includes('Summary'));
    await summaryTab.trigger('click');
    await flushPromises();
    expect(wrapper.find('.fb-distribution').exists()).toBe(true);
    const distRows = wrapper.findAll('.fb-distribution__row');
    expect(distRows.length).toBe(5);
    wrapper.unmount();
  });

  it('shows error state when API fails', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({}),
      text: () => Promise.resolve(''),
    }));
    const wrapper = await mountFeedback();
    expect(wrapper.find('.fb-view').exists()).toBe(true);
    expect(wrapper.text()).toContain('Failed to load feedback');
    wrapper.unmount();
  });

  it('displays target type badges with correct labels', async () => {
    mockFetch(defaultRoutes({
      '/api/feedback': {
        status: 'ok',
        data: [
          { feedback_id: 'fb_1', target_type: 'agent', target_id: 'a1', rating: 5, comment: '', tags: [], user_id: 'u1', created_at: 1700000000, updated_at: 1700000000 },
          { feedback_id: 'fb_2', target_type: 'task', target_id: 't1', rating: 4, comment: '', tags: [], user_id: 'u2', created_at: 1700000001, updated_at: 1700000001 },
          { feedback_id: 'fb_3', target_type: 'result', target_id: 'r1', rating: 3, comment: '', tags: [], user_id: 'u3', created_at: 1700000002, updated_at: 1700000002 },
        ],
        total: 3,
        page: 1,
        page_size: 20,
      },
    }));
    const wrapper = await mountFeedback();
    const badges = wrapper.findAll('.badge');
    // First 3 badges should be the target type badges
    const texts = badges.map((b) => b.text());
    expect(texts.some((s) => s.includes('Agent'))).toBe(true);
    expect(texts.some((s) => s.includes('Task'))).toBe(true);
    expect(texts.some((s) => s.includes('Result'))).toBe(true);
    wrapper.unmount();
  });

  it('does not crash when endpoints return malformed data', async () => {
    mockFetch({
      '/api/feedback': { status: 'ok' },
      '/api/feedback/summary': { status: 'ok' },
    });
    const wrapper = await mountFeedback();
    expect(wrapper.find('.fb-view').exists()).toBe(true);
    // Should show empty state (no data array)
    expect(wrapper.text()).toContain('No feedback yet');
    wrapper.unmount();
  });
});