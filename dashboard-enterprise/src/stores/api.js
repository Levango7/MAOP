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
  } catch (e) {
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
  init.headers = h;init.credentials = 'include';  // #4 fix: send httpOnly cookie
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
 * 统一处理 401：清除登录态并跳转登录页。
 * 仅在企业版前端环境中触发（避免在 Vitest 中触发路由跳转）。
 */
function handleUnauthorized() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch (e) { /* ignore */ }
  // 仅在浏览器环境且非测试环境触发重定向，避免 vitest 中 jsdom 缺少路由
  if (typeof window !== 'undefined' && !window.__VITEST__ && window.location) {
    // 触发一个自定义事件，让 App.vue 决定如何展示登录态（不强制刷新整页）
    window.dispatchEvent(new CustomEvent('maop:unauthorized'));
  }
}

/**
 * API versioning infrastructure (incremental migration).
 *
 * Migration plan:
 *   1. Current: v1Url() helper is ready but all calls still use unversioned /api/* paths
 *   2. Next: new code calls v1Url("/api/agents") to get "/api/v1/agents"
 *   3. Final: switch default to /api/v1 and remove old path aliases
 *
 * Exempt endpoints (no version prefix, for infrastructure compatibility):
 *   - /api/health    K8s/Docker liveness & readiness probes
 *   - /api/stream    SSE stream (token validated via query param)
 *   - /api/auth/*    authentication flow itself (login/logout/refresh)
 */
const API_V1_PREFIX = '/api/v1';

/**
 * Convert an /api/* path to the versioned /api/v1/* path.
 * Exempt endpoints (health/stream/auth) are returned unchanged.
 * @param {string} path - original path, e.g. "/api/agents"
 * @returns {string} versioned path, e.g. "/api/v1/agents"
 */
function v1Url(path) {
  if (
    path.startsWith('/api/') &&
    !path.startsWith('/api/health') &&
    !path.startsWith('/api/stream') &&
    !path.startsWith('/api/auth')
  ) {
    return API_V1_PREFIX + path.slice(4); // /api/agents -> /api/v1/agents
  }
  return path;
}

export const useApiStore = defineStore('api', {
  actions: {
    /**
     * GET 请求，自动注入 Bearer token。
     * @param {string} url
     * @param {object} [opts] { headers } 可选额外 headers
     */
    async get(url, opts) {
      const res = await fetchWithTimeout(url, withAuth({}, (opts && opts.headers) || {}));
      if (res.status === 401) {
        handleUnauthorized();
        throw new Error(`API ${url}: 401 Unauthorized`);
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
      const res = await fetchWithTimeout(url, withAuth(
        { method: 'POST', body: JSON.stringify(body || {}) },
        headers
      ));
      if (res.status === 401) {
        handleUnauthorized();
        throw new Error(`API ${url}: 401 Unauthorized`);
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /** PUT 请求，自动注入 Bearer token */
    async put(url, body) {
      const res = await fetchWithTimeout(url, withAuth(
        { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) },
        { 'Content-Type': 'application/json' }
      ));
      if (res.status === 401) { handleUnauthorized(); throw new Error(`API ${url}: 401`); }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /** DELETE 请求，自动注入 Bearer token */
    async delete(url) {
      const res = await fetchWithTimeout(url, withAuth({ method: 'DELETE' }, {}));
      if (res.status === 401) { handleUnauthorized(); throw new Error(`API ${url}: 401`); }
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
      } catch (e) { /* ignore */ }
    },
    /**
     * 清除 token（登出）。P1 fix: 通知后端撤销 JWT token。
     */
    async clearAuthToken() {
      // Notify backend to revoke the token before clearing locally
      try {
        await fetchWithTimeout('/api/auth/logout', withAuth({ method: 'POST' }, {}));
      } catch (e) { /* best-effort — clear locally anyway */ }
      try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      } catch (e) { /* ignore */ }
    },
  },
});

// 模块级导出（便于非 Pinia 上下文使用，如 App.vue 直接 import）
export { getAuthToken, withAuth, handleUnauthorized, v1Url };
