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
const mockDelete = vi.fn();
vi.mock('../stores/api.js', () => ({
  useApiStore: () => ({ get: mockGet, post: mockPost, put: vi.fn(), delete: mockDelete }),
}));

import CredentialVault from '../views/CredentialVault.vue';

/**
 * The shape below mirrors `CredentialSummary` — note it carries `preview`
 * (masked, e.g. "sk-****abcd") and NOT any plaintext field. Two behaviours
 * here are safety-critical and are asserted explicitly:
 *
 *   1. The view must never render a plaintext secret (it only ever receives
 *      previews, but a future refactor could start calling the detail endpoint
 *      and put `credential` on screen).
 *   2. Delete / rotate are irreversible, so they must go through a confirmation
 *      step — a single misclick must not destroy or replace a live secret.
 */
describe('CredentialVault.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
    mockGet.mockReset();
    mockPost.mockReset();
    mockDelete.mockReset();
  });

  const CREDS = [
    {
      id: 'c1', agent_name: 'codex', credential_type: 'api_key',
      preview: 'sk-****abcd', last_used_at: 1729800000,
    },
    {
      id: 'c2', agent_name: 'claude', credential_type: 'oauth',
      preview: 'oa-****wxyz', last_used_at: 0,
    },
  ];

  function mockList(creds = CREDS) {
    mockGet.mockResolvedValue({ status: 'ok', credentials: creds, count: creds.length });
  }

  function mountView() {
    return mount(CredentialVault);
  }

  it('renders masked previews only', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain('codex');
    expect(wrapper.text()).toContain('sk-****abcd');
    // The masked form must be what's shown — no bare secret appears anywhere.
    expect(wrapper.text()).not.toContain('sk-live');
  });

  it('shows a dash instead of 1970 for never-used credentials', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();
    // c2 has last_used_at = 0
    expect(wrapper.text()).not.toContain('1970');
  });

  it('asks for confirmation before deleting — no API call on first click', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    const del = wrapper.findAll('button').find((b) => b.text() === 'Delete');
    await del.trigger('click');
    await flushPromises();

    expect(mockDelete).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Delete this credential?');
  });

  it('deletes only after the user confirms', async () => {
    mockList();
    mockDelete.mockResolvedValue({ status: 'ok' });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Delete').trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'Proceed').trigger('click');
    await flushPromises();

    expect(mockDelete).toHaveBeenCalledWith('/api/credential-vault/credentials/c1');
    // reload after mutation
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  it('asks for confirmation before rotating', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Rotate').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Rotate this credential?');
  });

  it('rotates only after the user confirms', async () => {
    mockList();
    mockPost.mockResolvedValue({ status: 'ok', credential_id: 'c1' });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Rotate').trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'Proceed').trigger('click');
    await flushPromises();

    expect(mockPost).toHaveBeenCalledWith('/api/credential-vault/credentials/c1/rotate', {});
  });

  it('cancelling the dialog leaves the credential untouched', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll('button').find((b) => b.text() === 'Delete').trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'Cancel').trigger('click');
    await flushPromises();

    expect(mockDelete).not.toHaveBeenCalled();
    expect(wrapper.text()).not.toContain('Delete this credential?');
  });

  it('shows the empty state when the vault is empty', async () => {
    mockList([]);
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('No credentials stored yet');
  });

  it('shows a load error instead of an empty table', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('Failed to load credentials');
  });

  // ── 新建凭据 ──────────────────────────────────────────────────
  // 后端不校验"该类型的敏感字段是否为空"，所以前端必须自己拦。
  // 这几个用例的核心就是：**校验不通过时一个请求都不能发出去**。

  async function openCreateForm() {
    mockList();
    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'New credential').trigger('click');
    await flushPromises();
    return wrapper;
  }

  function fieldLabels(wrapper) {
    return wrapper.findAll('.cv-form__label').map((n) => n.text());
  }

  it('opens the create form with only api_key field by default', async () => {
    const wrapper = await openCreateForm();
    expect(wrapper.find('.cv-form').exists()).toBe(true);
    const labels = fieldLabels(wrapper);
    expect(labels.some((l) => l.includes('API key'))).toBe(true);
    // 默认类型下不该出现其他类型的字段
    expect(labels.some((l) => l.includes('Username'))).toBe(false);
  });

  it('switches visible fields with the credential type', async () => {
    const wrapper = await openCreateForm();
    await wrapper.find('.cv-form select').setValue('username_password');
    await flushPromises();

    const labels = fieldLabels(wrapper);
    expect(labels.some((l) => l.includes('Username'))).toBe(true);
    expect(labels.some((l) => l.includes('Password'))).toBe(true);
    expect(labels.some((l) => l.includes('API key'))).toBe(false);
  });

  it('refuses to submit without an agent name', async () => {
    const wrapper = await openCreateForm();
    await wrapper.findAll('button').find((b) => b.text() === 'Create').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Agent name is required');
  });

  it('refuses to submit when the type-required secret is empty', async () => {
    const wrapper = await openCreateForm();
    const inputs = wrapper.findAll('.cv-form input');
    // 第 1 个是 agent_name，第 2 个是 name，第 3 个是 api_key
    await inputs[0].setValue('codex');
    await wrapper.findAll('button').find((b) => b.text() === 'Create').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('API key is required');
  });

  it('posts only the fields relevant to the chosen type', async () => {
    const wrapper = await openCreateForm();
    const inputs = wrapper.findAll('.cv-form input');
    await inputs[0].setValue('codex');
    await inputs[1].setValue('primary');
    await inputs[2].setValue('sk-abc');
    mockPost.mockResolvedValue({ status: 'ok' });

    await wrapper.findAll('button').find((b) => b.text() === 'Create').trigger('click');
    await flushPromises();

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/api/credential-vault/credentials');
    expect(body).toEqual({
      credential_type: 'api_key',
      agent_name: 'codex',
      name: 'primary',
      api_key: 'sk-abc',
    });
    // 不能把无关类型的空字段一并塞给后端
    expect(body).not.toHaveProperty('username');
    expect(body).not.toHaveProperty('cookie_string');
  });

  it('closes the form and reloads after a successful create', async () => {
    const wrapper = await openCreateForm();
    const inputs = wrapper.findAll('.cv-form input');
    await inputs[0].setValue('codex');
    await inputs[2].setValue('sk-abc');
    mockPost.mockResolvedValue({ status: 'ok' });

    await wrapper.findAll('button').find((b) => b.text() === 'Create').trigger('click');
    await flushPromises();

    expect(wrapper.find('.cv-form').exists()).toBe(false);
    expect(wrapper.text()).toContain('Credential created');
    // 1 次初始加载 + 1 次创建后重载
    expect(mockGet).toHaveBeenCalledTimes(2);
  });
});
