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
});
