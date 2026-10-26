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

  // ── 回滚 ──────────────────────────────────────────────────────
  // 回滚的语义是"把当前版本置 retired、把目标版本置 active"，所以入口只该
  // 出现在 active 行。如果出现在 draft 行，用户会以为自己能"回滚到一个还没
  // 生效的版本"，语义是错的。

  it('offers Rollback only on the active row', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    const rows = wrapper.findAll('tbody tr');
    const activeRowButtons = rows[0].findAll('button').map((b) => b.text());
    const draftRowButtons = rows[1].findAll('button').map((b) => b.text());

    expect(activeRowButtons).toContain('Rollback');
    expect(draftRowButtons).not.toContain('Rollback');
    expect(draftRowButtons).toContain('Canary');
  });

  it('loads same-agent candidates when the rollback dialog opens', async () => {
    mockGet.mockImplementation((url) => {
      if (String(url).includes('page_size=200')) {
        return Promise.resolve({
          status: 'ok',
          data: [
            { version_id: 'v2', version_number: '2.0.0', agent_id: 'codex', status: 'active' },
            { version_id: 'v0', version_number: '0.9.0', agent_id: 'codex', status: 'retired' },
          ],
          meta: { total: 2, page: 1, page_size: 200 },
        });
      }
      return Promise.resolve({ status: 'ok', data: VERSIONS, meta: { total: 2, page: 1, page_size: 20 } });
    });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Rollback').trigger('click');
    await flushPromises();

    const options = wrapper.findAll('.av-dialog select option').map((o) => o.text());
    // 自己（2.0.0）不该出现在候选里
    expect(options.some((o) => o.includes('0.9.0'))).toBe(true);
    expect(options.some((o) => o.includes('2.0.0'))).toBe(false);
  });

  it('refuses to roll back without a target version', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Rollback').trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'Roll back').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Pick a target version first');
  });

  it('posts the rollback with target and reason', async () => {
    mockList();
    mockPost.mockResolvedValue({ status: 'ok' });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Rollback').trigger('click');
    await flushPromises();
    await wrapper.find('.av-dialog select').setValue('v1');
    await wrapper.find('.av-dialog input[type="text"]').setValue('regression');
    await wrapper.findAll('button').find((b) => b.text() === 'Roll back').trigger('click');
    await flushPromises();

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/api/agent-versions/v2/rollback');
    expect(body).toEqual({ target_version_id: 'v1', reason: 'regression' });
  });

  // ── 灰度 ──────────────────────────────────────────────────────

  it('rejects an out-of-range canary percentage', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Canary').trigger('click');
    await flushPromises();
    await wrapper.find('.av-dialog input[type="number"]').setValue('140');
    await wrapper.findAll('button').find((b) => b.text() === 'Start canary').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('between 0 and 100');
  });

  it('posts canary with comma-split groups and omits zero duration', async () => {
    mockList();
    mockPost.mockResolvedValue({ status: 'ok' });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Canary').trigger('click');
    await flushPromises();

    const nums = wrapper.findAll('.av-dialog input[type="number"]');
    await nums[0].setValue('25');            // percentage
    await wrapper.find('.av-dialog select').setValue('both');
    await wrapper.find('.av-dialog input[type="text"]').setValue('internal, beta');
    await nums[1].setValue('0');             // duration = 0 ⇒ 不传

    await wrapper.findAll('button').find((b) => b.text() === 'Start canary').trigger('click');
    await flushPromises();

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/api/agent-versions/v1/canary');
    expect(body).toEqual({
      percentage: 25,
      strategy: 'both',
      target_user_groups: ['internal', 'beta'],
    });
    // duration_seconds 后端要求 >=1 或省略；0 不能塞进去
    expect(body).not.toHaveProperty('duration_seconds');
  });
});
