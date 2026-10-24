import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('../stores/api.js', () => ({
  useApiStore: () => ({ get: mockGet, post: mockPost, put: vi.fn(), delete: vi.fn() }),
}));

import AgentVersions from '../views/AgentVersions.vue';

/**
 * The list shape below mirrors `agent_versions_service.list_versions`:
 * `{ data: [...], meta: { total, page, page_size } }`.
 *
 * The status column drives a real behavioural difference — only non-active
 * versions get an Activate button, and the active one renders as "live".
 * If that inverted, users would try to activate the version that is already
 * live (a no-op at best, a confusing error at worst).
 */
describe('AgentVersions.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
    mockGet.mockReset();
    mockPost.mockReset();
  });

  const VERSIONS = [
    { version_id: 'v2', version_number: '2.0.0', agent_id: 'codex', status: 'active', created_at: '2026-10-24' },
    { version_id: 'v1', version_number: '1.0.0', agent_id: 'codex', status: 'draft', created_at: '2026-10-20' },
  ];

  function mockList(data = VERSIONS, meta = { total: 2, page: 1, page_size: 20 }) {
    mockGet.mockResolvedValue({ status: 'ok', data, meta });
  }

  function mountView() {
    return mount(AgentVersions);
  }

  it('loads versions on mount and renders the rows', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    expect(mockGet.mock.calls[0][0]).toContain('/api/agent-versions?');
    expect(wrapper.text()).toContain('2.0.0');
    expect(wrapper.text()).toContain('1.0.0');
    expect(wrapper.text()).toContain('codex');
  });

  it('offers Activate only for versions that are not already live', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    const buttons = wrapper.findAll('button').filter((b) => b.text() === 'Activate');
    // Only the draft version (v1) gets a button; the active one shows "live".
    expect(buttons).toHaveLength(1);
    expect(wrapper.text()).toContain('live');
  });

  it('activates a version and reloads the list', async () => {
    mockList();
    mockPost.mockResolvedValue({ status: 'ok' });
    const wrapper = mountView();
    await flushPromises();

    const activate = wrapper.findAll('button').find((b) => b.text() === 'Activate');
    await activate.trigger('click');
    await flushPromises();

    expect(mockPost.mock.calls[0][0]).toBe('/api/agent-versions/v1/activate');
    // 1 initial load + 1 reload after activation
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  it('shows the empty state when there are no versions', async () => {
    mockList([], { total: 0, page: 1, page_size: 20 });
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('No versions yet');
  });

  it('shows a load error instead of an empty table', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('Failed to load versions');
  });

  it('passes filters into the query string', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue('codex');
    await wrapper.find('select').setValue('draft');
    await wrapper.findAll('button').find((b) => b.text() === 'Refresh').trigger('click');
    await flushPromises();

    const lastUrl = mockGet.mock.calls.at(-1)[0];
    expect(lastUrl).toContain('agent_id=codex');
    expect(lastUrl).toContain('status=draft');
  });
});
