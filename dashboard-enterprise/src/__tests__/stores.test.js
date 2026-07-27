import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useEditionStore } from '../stores/edition.js';
import { useApiStore } from '../stores/api.js';

describe('useEditionStore', () => {
  let originalFetch;
  beforeEach(() => {
    setActivePinia(createPinia());
    try { localStorage.clear(); } catch {}
    originalFetch = global.fetch;
  });
  afterEach(() => {
    if (originalFetch !== undefined) global.fetch = originalFetch;
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

  // ── switchEdition action 测试 ──
  it('switchEdition has correct default state', () => {
    const store = useEditionStore();
    expect(store.switching).toBe(false);
    expect(store.switchError).toBe('');
  });

  it('switchEdition updates state on success', async () => {
    const store = useEditionStore();
    // mock fetch：POST 返回切换结果，GET（fetchEdition）返回新状态
    global.fetch = vi.fn().mockImplementation((url, init) => {
      if (init && init.method === 'POST') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({
            status: 'ok',
            edition: 'personal',
            previous: 'enterprise',
            requested: 'personal',
            degraded: false,
          }),
        });
      }
      // GET (fetchEdition refresh)
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({
          edition: 'personal',
          features: { cache: true },
          backends: { storage: 'sqlite' },
          degradations: [],
        }),
      });
    });
    const result = await store.switchEdition('personal');
    expect(result.edition).toBe('personal');
    expect(result.previous).toBe('enterprise');
    expect(store.switching).toBe(false);
    expect(store.switchError).toBe('');
    // fetchEdition 应已被调用，edition 已刷新
    expect(store.edition).toBe('personal');
    expect(store.features).toEqual({ cache: true });
  });

  it('switchEdition throws on error status and sets switchError', async () => {
    const store = useEditionStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: 'Admin role required' }),
    });
    await expect(store.switchEdition('enterprise')).rejects.toThrow('Admin role required');
    expect(store.switching).toBe(false);
    expect(store.switchError).toBe('Admin role required');
  });

  it('switchEdition handles 401 unauthorized', async () => {
    const store = useEditionStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    });
    await expect(store.switchEdition('personal')).rejects.toThrow('401 Unauthorized');
    expect(store.switching).toBe(false);
  });

  it('switchEdition sets switching flag during operation', async () => {
    const store = useEditionStore();
    let resolveFn;
    global.fetch = vi.fn().mockReturnValue(new Promise(resolve => { resolveFn = resolve; }));
    const promise = store.switchEdition('personal');
    // 进行中时 switching 应为 true
    expect(store.switching).toBe(true);
    resolveFn({
      ok: true,
      status: 200,
      json: () => Promise.resolve({
        status: 'ok',
        edition: 'personal',
        previous: 'enterprise',
        requested: 'personal',
        degraded: false,
      }),
    });
    await promise;
    // 完成后 switching 应为 false
    expect(store.switching).toBe(false);
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

  it('setAuthToken / clearAuthToken manage localStorage', async () => {
    const store = useApiStore();
    store.setAuthToken('abc', 'admin');
    expect(store.authToken()).toBe('abc');
    expect(localStorage.getItem('maop_user')).toBe('admin');
    // clearAuthToken 是 async（需 await fetch logout），必须 await
    await store.clearAuthToken();
    expect(store.authToken()).toBe('');
    expect(localStorage.getItem('maop_user')).toBeNull();
  });
});