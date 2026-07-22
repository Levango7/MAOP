import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useEditionStore } from '../stores/edition.js';
import { useApiStore } from '../stores/api.js';

describe('useEditionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    try { localStorage.clear(); } catch {}
  });

  it('has correct defaults', () => {
    const store = useEditionStore();
    expect(store.edition).toBe('enterprise');
    expect(store.features).toEqual({});
    expect(store.degradations).toEqual([]);
    expect(store.loading).toBe(false);
  });

  it('isEnterprise getter works', () => {
    const store = useEditionStore();
    expect(store.isEnterprise).toBe(true);
    expect(store.isPersonal).toBe(false);
  });

  it('hasFeature getter works', () => {
    const store = useEditionStore();
    expect(store.hasFeature('rbac')).toBe(false);
    store.features = { rbac: true };
    expect(store.hasFeature('rbac')).toBe(true);
  });

  it('hasDegradations getter works', () => {
    const store = useEditionStore();
    expect(store.hasDegradations).toBe(false);
    store.degradations = [{ module: 'storage', from: 'postgresql', to: 'memory' }];
    expect(store.hasDegradations).toBe(true);
  });

  it('fetchEdition updates state on success', async () => {
    const store = useEditionStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({
        edition: 'personal',
        features: { cache: true },
        backends: { storage: 'sqlite' },
        degradations: [],
      }),
    });
    await store.fetchEdition();
    expect(store.edition).toBe('personal');
    expect(store.features).toEqual({ cache: true });
    expect(store.loading).toBe(false);
  });

  it('fetchEdition handles errors gracefully', async () => {
    const store = useEditionStore();
    global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
    await store.fetchEdition();
    expect(store.loading).toBe(false);
    expect(store.edition).toBe('enterprise');
  });
});

describe('useApiStore auth header injection', () => {
  let originalFetch;
  beforeEach(() => {
    setActivePinia(createPinia());
    try { localStorage.clear(); } catch {}
    originalFetch = global.fetch;
    // Mark vitest env so handleUnauthorized won't dispatch CustomEvent
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });
  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    try { localStorage.clear(); } catch {}
  });

  it('get injects Authorization header when token present', async () => {
    localStorage.setItem('maop_token', 'test-jwt-abc');
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'ok' }),
    });
    await store.get('/api/health');
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer test-jwt-abc' }),
      })
    );
  });

  it('get omits Authorization header when no token', async () => {
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'ok' }),
    });
    await store.get('/api/health');
    const callArgs = global.fetch.mock.calls[0];
    expect(callArgs[1].headers.Authorization).toBeUndefined();
  });

  it('get returns JSON on success', async () => {
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'ok' }),
    });
    const data = await store.get('/api/health');
    expect(data).toEqual({ status: 'ok' });
  });

  it('get throws on error status', async () => {
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });
    await expect(store.get('/api/fail')).rejects.toThrow('API /api/fail: 500');
  });

  it('get clears token and throws on 401', async () => {
    localStorage.setItem('maop_token', 'expired-jwt');
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    });
    await expect(store.get('/api/protected')).rejects.toThrow('401 Unauthorized');
    expect(localStorage.getItem('maop_token')).toBeNull();
  });

  it('post sends JSON body with Authorization header', async () => {
    localStorage.setItem('maop_token', 'post-jwt');
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: 1 }),
    });
    const data = await store.post('/api/agents', { name: 'test' });
    expect(data).toEqual({ id: 1 });
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/agents',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'test' }),
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          Authorization: 'Bearer post-jwt',
        }),
      })
    );
  });

  it('setAuthToken / clearAuthToken manage localStorage', () => {
    const store = useApiStore();
    store.setAuthToken('abc', 'admin');
    expect(store.authToken()).toBe('abc');
    expect(localStorage.getItem('maop_user')).toBe('admin');
    store.clearAuthToken();
    expect(store.authToken()).toBe('');
    expect(localStorage.getItem('maop_user')).toBeNull();
  });
});