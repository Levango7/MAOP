import { defineStore } from 'pinia';

// Auth token key — keep in sync with dashboard/js/app-core.js (零构建版)
const TOKEN_KEY = 'maop_token';
const USER_KEY = 'maop_user';

/**
 * 读取 localStorage 中的 JWT token。
 * 与零构建版 dashboard/js/app-core.js 共享同一 key，跨版本登录态一致。
 */
function getAuthToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    // localStorage 在某些隐私模式下不可用
    return '';
  }
}

/**
 * 构造带 Authorization 头的请求配置。
 * @param {RequestInit} [extra] 额外 fetch 配置（method/body 等）
 * @param {object} [headers] 额外 headers（如 Content-Type）
 */
function withAuth(extra, headers) {
  const init = extra || {};
  const h = Object.assign({}, headers || {});
  const token = getAuthToken();
  if (token) h['Authorization'] = 'Bearer ' + token;
  init.headers = h;
  init.credentials = 'include'; // #4 fix: send httpOnly cookie
  return init;
}

/**
 * P2-11 fix: 统一 30s 超时控制，防止后端无响应时前端请求永久挂起。
 * 使用 AbortController 在超时后中止请求。
 */
const API_TIMEOUT_MS = 30000;

function fetchWithTimeout(url, init) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  return fetch(url, Object.assign({}, init, { signal: controller.signal }))
    .finally(() => clearTimeout(timeoutId));
}

/**
 * Token refresh state — prevents concurrent refresh requests.
 */
let _refreshPromise = null;

/**
 * Attempt to refresh the current JWT token via /api/auth/refresh.
 * Returns true if refresh succeeded and token was updated, false otherwise.
 */
async function tryRefreshToken() {
  // Prevent multiple simultaneous refresh attempts
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    try {
      const token = getAuthToken();
      if (!token) return false;
      const res = await fetchWithTimeout('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (data.status === 'ok' && data.token) {
        try { localStorage.setItem(TOKEN_KEY, data.token); } catch { /* ignore */ }
        return true;
      }
      return false;
    } catch {
      return false;
    } finally {
      _refreshPromise = null;
    }
  })();
  return _refreshPromise;
}

/**
 * 统一处理 401：先尝试 refresh token，失败才清除登录态。
 * 仅在企业版前端环境中触发（避免在 Vitest 中触发路由跳转）。
 */
async function handleUnauthorized() {
  // L2: Try token refresh before giving up
  const refreshed = await tryRefreshToken();
  if (refreshed) return;  // Caller should retry the original request

  // Refresh failed — clear auth state
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch { /* ignore */ }
  // 仅在浏览器环境且非测试环境触发重定向，避免 vitest 中 jsdom 缺少路由
  if (typeof window !== 'undefined' && !window.__VITEST__ && window.location) {
    // 触发一个自定义事件，让 App.vue 决定如何展示登录态（不强制刷新整页）
    window.dispatchEvent(new CustomEvent('maop:unauthorized'));
  }
}

export const useApiStore = defineStore('api', {
  actions: {
    /**
     * GET 请求，自动注入 Bearer token。
     * @param {string} url
     * @param {object} [opts] { headers } 可选额外 headers
     */
    async get(url, opts) {
      let res = await fetchWithTimeout(url, withAuth({}, (opts && opts.headers) || {}));
      if (res.status === 401) {
        await handleUnauthorized();
        // Retry once if refresh succeeded (new token is now in localStorage)
        res = await fetchWithTimeout(url, withAuth({}, (opts && opts.headers) || {}));
        if (res.status === 401) {
          handleUnauthorized();  // refresh didn't help or no token
          throw new Error(`API ${url}: 401 Unauthorized`);
        }
      }
      if (!res.ok) throw new Error(`API ${url}: ${res.status}`);
      return res.json();
    },
    /**
     * POST 请求，自动注入 Bearer token 与 Content-Type。
     * @param {string} url
     * @param {object} body JSON body
     * @param {object} [opts] { headers } 可选额外 headers
     */
    async post(url, body, opts) {
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        (opts && opts.headers) || {}
      );
      let res = await fetchWithTimeout(url, withAuth(
        { method: 'POST', body: JSON.stringify(body || {}) },
        headers
      ));
      if (res.status === 401) {
        await handleUnauthorized();
        res = await fetchWithTimeout(url, withAuth(
          { method: 'POST', body: JSON.stringify(body || {}) },
          headers
        ));
        if (res.status === 401) {
          handleUnauthorized();
          throw new Error(`API ${url}: 401 Unauthorized`);
        }
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /** PUT 请求，自动注入 Bearer token */
    async put(url, body) {
      let res = await fetchWithTimeout(url, withAuth(
        { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) },
        { 'Content-Type': 'application/json' }
      ));
      if (res.status === 401) {
        await handleUnauthorized();
        res = await fetchWithTimeout(url, withAuth(
          { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) },
          { 'Content-Type': 'application/json' }
        ));
        if (res.status === 401) { handleUnauthorized(); throw new Error(`API ${url}: 401`); }
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /** DELETE 请求，自动注入 Bearer token */
    async delete(url) {
      let res = await fetchWithTimeout(url, withAuth({ method: 'DELETE' }, {}));
      if (res.status === 401) {
        await handleUnauthorized();
        res = await fetchWithTimeout(url, withAuth({ method: 'DELETE' }, {}));
        if (res.status === 401) { handleUnauthorized(); throw new Error(`API ${url}: 401`); }
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /**
     * 暴露给组件直接使用的工具方法：返回当前 token（便于 UI 显示登录状态）。
     */
    authToken() {
      return getAuthToken();
    },
    /**
     * 设置 token（登录成功后调用）。
     */
    setAuthToken(token, user) {
      try {
        if (token) localStorage.setItem(TOKEN_KEY, token);
        else localStorage.removeItem(TOKEN_KEY);
        if (user) localStorage.setItem(USER_KEY, user);
        else localStorage.removeItem(USER_KEY);
      } catch { /* ignore */ }
    },
    /**
     * 清除 token（登出）。P1 fix: 通知后端撤销 JWT token。
     */
    async clearAuthToken() {
      // Notify backend to revoke the token before clearing locally
      try {
        await fetchWithTimeout('/api/auth/logout', withAuth({ method: 'POST' }, {}));
      } catch { /* best-effort — clear locally anyway */ }
      try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      } catch { /* ignore */ }
    },
  },
});

// 模块级导出（便于非 Pinia 上下文使用，如 App.vue 直接 import）
export { getAuthToken, withAuth, handleUnauthorized };
