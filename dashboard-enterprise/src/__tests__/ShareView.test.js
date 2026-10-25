import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

const mockGet = vi.fn();
vi.mock('../stores/api.js', () => ({
  useApiStore: () => ({ get: mockGet, post: vi.fn(), put: vi.fn(), delete: vi.fn() }),
}));

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'tok-abc' } }),
}));

import ShareView from '../views/ShareView.vue';

/**
 * The public share page. Two things matter beyond "it renders":
 *
 *   1. A bad/expired token must show a neutral "link unavailable" state, not
 *      an error stack — the visitor did nothing wrong and may not be technical.
 *   2. The page must render ONLY summary fields. The backend already refuses to
 *      send event payloads, but if this view ever started reading `events` the
 *      share would silently start leaking stdout/stderr.
 */
describe('ShareView.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
    mockGet.mockReset();
  });

  const SUMMARY = {
    trace_id: 'tr-ok',
    task: 'refactor auth',
    agent: 'codex',
    status: 'finished',
    success: true,
    duration_ms: 1234,
    steps: 2,
    verify_status: 'passed',
    verify_summary: 'all gates green',
    started_at: '2026-10-25T05:00:00Z',
  };

  function mockShare(summary = SUMMARY) {
    mockGet.mockResolvedValue({ status: 'ok', share: { token: 'tok-abc', summary } });
  }

  function mountView() {
    return mount(ShareView);
  }

  it('requests the share by token from the route', async () => {
    mockShare();
    mountView();
    await flushPromises();
    expect(mockGet.mock.calls[0][0]).toBe('/api/share/tok-abc');
  });

  it('renders the summary fields', async () => {
    mockShare();
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain('refactor auth');
    expect(wrapper.text()).toContain('codex');
    expect(wrapper.text()).toContain('1234ms');
    expect(wrapper.text()).toContain('all gates green');
  });

  it('shows a neutral unavailable state for a bad token', async () => {
    mockGet.mockRejectedValue(new Error('404'));
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain('This link is not available');
    // 不暴露技术细节（状态码 / 异常名）——访问者可能不是技术人员，
    // 而且区分"过期"与"从未存在"会让人能探测 token 是否曾经有效。
    expect(wrapper.text()).not.toContain('404');
    expect(wrapper.text()).not.toContain('Error');
  });

  it('renders nothing resembling agent output', async () => {
    mockShare();
    const wrapper = mountView();
    await flushPromises();

    const text = wrapper.text();
    expect(text).not.toContain('stdout');
    expect(text).not.toContain('stderr');
  });

  it('marks a failed run distinctly from a successful one', async () => {
    mockShare({ ...SUMMARY, success: false, status: 'finished' });
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('failed');
  });
});
