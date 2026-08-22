import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useEditionStore } from '../stores/edition.js';
import { useApiStore } from '../stores/api.js';

describe('useEditionStore', () => {
  let originalFetch;
  beforeEach(() => {
    setActivePinia(createPinia());
    try { localStorage.clear(); } catch { /* ignore */ }
    originalFetch = global.fetch;
  });
  afterEach(() => {
    if (originalFetch !== undefined) global.fetch = originalFetch;
  });

  it('has correct defaults', () => {
    const store = useEditionStore();
    // P1-H1: cold-load fail-safe default is 'personal' (no enterprise bypass)
    expect(store.edition).toBe('personal');
    expect(store.features).toEqual({});
    expect(store.degradations).toEqual([]);
    expect(store.loading).toBe(false);
  });

  it('isEnterprise getter works', () => {
    const store = useEditionStore();
    // P1-H1: default 'personal' → isEnterprise false, isPersonal true
    expect(store.isEnterprise).toBe(false);
    expect(store.isPersonal).toBe(true);
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
    // P1-H1: on fetch failure edition stays at fail-safe default 'personal'
    expect(store.edition).toBe('personal');
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
    try { localStorage.clear(); } catch { /* ignore */ }
    originalFetch = global.fetch;
    // Mark vitest env so handleUnauthorized won't dispatch CustomEvent
    global.__VITEST__ = true;
    if (typeof window !== 'undefined') window.__VITEST__ = true;
  });
  afterEach(() => {
    global.fetch = originalFetch;
    delete global.__VITEST__;
    if (typeof window !== 'undefined') delete window.__VITEST__;
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it('get uses credentials: include for cookie-based auth', async () => {
    // M6 fix: token 从 localStorage 迁移到 httpOnly cookie，
    // 请求通过 credentials: 'include' 自动携带 cookie，不再设置 Authorization header。
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
        credentials: 'include',
      })
    );
    // M6 fix: 不应设置 Authorization header（token 由 httpOnly cookie 携带）
    const callArgs = global.fetch.mock.calls[0];
    expect(callArgs[1].headers.Authorization).toBeUndefined();
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

  it('get clears user info and throws on 401', async () => {
    // M6 fix: token 由 httpOnly cookie 管理，前端通过 user 信息判断登录状态。
    // 401 时清除 user 信息（而非 token）。
    localStorage.setItem('maop_user', 'admin');
    const store = useApiStore();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
    });
    await expect(store.get('/api/protected')).rejects.toThrow('401 Unauthorized');
    // M6 fix: user 信息应被清除（token 不再存储在 localStorage）
    expect(localStorage.getItem('maop_user')).toBeNull();
  });

  it('post sends JSON body with cookie credentials', async () => {
    // M6 fix: token 由 httpOnly cookie 携带，不再设置 Authorization header。
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
        credentials: 'include',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
        }),
      })
    );
    // M6 fix: 不应设置 Authorization header
    const callArgs = global.fetch.mock.calls[0];
    expect(callArgs[1].headers.Authorization).toBeUndefined();
  });

  it('setAuthToken / clearAuthToken manage user info (M6 cookie auth)', async () => {
    // M6 fix: token 由后端 httpOnly cookie 管理，setAuthToken 只存储 user 信息。
    // authToken() 始终返回空字符串（httpOnly cookie 不可读）。
    const store = useApiStore();
    store.setAuthToken('abc', 'admin');
    // M6 fix: authToken() 返回空字符串（token 在 httpOnly cookie 中不可读）
    expect(store.authToken()).toBe('');
    expect(localStorage.getItem('maop_user')).toBe('admin');
    // isLoggedIn() 通过 user 信息判断登录状态
    expect(store.isLoggedIn()).toBe(true);
    // clearAuthToken 是 async（需 await fetch logout），必须 await
    await store.clearAuthToken();
    expect(store.authToken()).toBe('');
    expect(localStorage.getItem('maop_user')).toBeNull();
    expect(store.isLoggedIn()).toBe(false);
  });
});