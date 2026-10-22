// Tests for the auth-scoped localStorage lifecycle (stores/api.js clearAuthKeys).
//
// The boundary being pinned: keys that describe *who is logged in* must die with
// the session, while device-level preferences survive. A leftover `maop_roles`
// or `maop_edition` renders the previous identity's permissions until the async
// hydrate lands, which is a stale-permission window rather than a cosmetic bug.

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useApiStore, clearAuthKeys, AUTH_SCOPED_KEYS } from '../stores/api.js';

function seed() {
  localStorage.clear();
  for (const key of AUTH_SCOPED_KEYS) localStorage.setItem(key, 'stale');
  // Device-level preferences: must survive an identity change.
  localStorage.setItem('maop_theme', 'dark');
  localStorage.setItem('maop_locale', 'zh');
}

function survivors() {
  return Object.keys(localStorage).filter((k) => k.startsWith('maop_'));
}

describe('auth-scoped storage lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    seed();
  });

  it('clearAuthKeys removes every identity-derived key', () => {
    clearAuthKeys();
    for (const key of AUTH_SCOPED_KEYS) {
      expect(localStorage.getItem(key), `${key} survived logout`).toBeNull();
    }
  });

  it('keeps device-level preferences', () => {
    clearAuthKeys();
    expect(localStorage.getItem('maop_theme')).toBe('dark');
    expect(localStorage.getItem('maop_locale')).toBe('zh');
  });

  it('logout clears the caches after notifying the backend', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    const api = useApiStore();
    await api.clearAuthToken();
    expect(fetchMock).toHaveBeenCalled();
    expect(localStorage.getItem('maop_roles')).toBeNull();
    expect(localStorage.getItem('maop_edition')).toBeNull();
    vi.unstubAllGlobals();
  });

  it('a failed refresh (401) also clears them, not just the user name', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    const { handleUnauthorized } = await import('../stores/api.js');
    await handleUnauthorized();
    expect(survivors().filter((k) => AUTH_SCOPED_KEYS.includes(k))).toEqual([]);
    expect(localStorage.getItem('maop_theme')).toBe('dark');
    vi.unstubAllGlobals();
  });
});
