import { defineStore } from 'pinia';

// M6 fix: token 从 localStorage 迁移到 httpOnly cookie（由后端 Set-Cookie 设置）。
// httpOnly cookie 无法被 JavaScript 读取，避免 XSS 攻击窃取 token。
// 前端不再直接接触 token，所有请求通过 withCredentials: true 自动携带 cookie。
// 保留 USER_KEY 用于存储非敏感的用户名信息（UI 显示登录状态）。
const USER_KEY = 'maop_user';

/**
 * M6 fix: token 现由 httpOnly cookie 管理，前端无法读取。
 * 保留函数签名以兼容现有调用方，但始终返回空字符串。
 * 登录状态应通过 isLoggedIn() 或 user 信息判断，而非 token 是否存在。
 */
function getAuthToken() {
  return '';
}

/**
 * 判断当前是否已登录（通过 user 信息存在性判断）。
 * @returns {boolean}
 */
function isLoggedIn() {
  try {
    return !!localStorage.getItem(USER_KEY);
  } catch {
    return false;
  }
}

/**
 * 构造带认证的请求配置。
 * M6 fix: 不再手动设置 Authorization header，依赖 httpOnly cookie 自动携带。
 * @param {RequestInit} [extra] 额外 fetch 配置（method/body 等）
 * @param {object} [headers] 额外 headers（如 Content-Type）
 */
function withAuth(extra, headers) {
  const init = extra || {};
  const h = Object.assign({}, headers || {});
  // M6 fix: 不再设置 Authorization header，依赖 cookie 自动携带。
  init.headers = h;
  init.credentials = 'include'; // 携带 httpOnly cookie
  return init;
}

/**
 * P2-11 fix: 统一 30s 超时控制，防止后端无响应时前端请求永久挂起。
 * 使用 AbortController 在超时后中止请求。
 *
 * P1 fix: 参数化 timeout，让调用方（如 edition store）可按需指定更短/更长超时。
 * 默认 30s 保持向后兼容；导出供其他模块复用同一套超时机制。
 * @param {string} url
 * @param {RequestInit} [init]
 * @param {number} [timeoutMs=API_TIMEOUT_MS] 可选超时（毫秒）
 */
const API_TIMEOUT_MS = 30000;

function fetchWithTimeout(url, init, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
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
      // M6 fix: 不再从 localStorage 读取 token，依赖 httpOnly cookie 自动携带。
      if (!isLoggedIn()) return false;
      const res = await fetchWithTimeout('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include', // 携带 httpOnly cookie
      });
      if (!res.ok) return false;
      const data = await res.json();
      // M6 fix: 后端会通过 Set-Cookie 更新 httpOnly cookie，前端不需要处理 token。
      if (data.status === 'ok') {
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
  // M6 fix: token 由后端 httpOnly cookie 管理，前端只需清除 user 信息。
  try {
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
          // P1 fix: await handleUnauthorized 确保未授权处理（清登录态/跳登录页）
          // 在抛错前完成，避免后续代码先执行导致竞态。
          await handleUnauthorized();
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
          // P1 fix: await handleUnauthorized 确保未授权处理先完成
          await handleUnauthorized();
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
        if (res.status === 401) {
          // P1 fix: await handleUnauthorized 确保未授权处理先完成
          await handleUnauthorized();
          throw new Error(`API ${url}: 401`);
        }
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
        if (res.status === 401) {
          // P1 fix: await handleUnauthorized 确保未授权处理先完成
          await handleUnauthorized();
          throw new Error(`API ${url}: 401`);
        }
      }
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `API ${url}: ${res.status}`);
      }
      return res.json();
    },
    /**
     * 暴露给组件直接使用的工具方法：返回当前 token（便于 UI 显示登录状态）。
     * M6 fix: token 现由 httpOnly cookie 管理，前端无法读取，始终返回空字符串。
     * 登录状态请使用 isLoggedIn() 判断。
     */
    authToken() {
      return getAuthToken();
    },
    /**
     * 判断当前是否已登录。
     * M6 fix: 通过 user 信息存在性判断（token 在 httpOnly cookie 中不可读）。
     */
    isLoggedIn() {
      return isLoggedIn();
    },
    /**
     * 设置登录态（登录成功后调用）。
     * M6 fix: token 由后端 Set-Cookie httpOnly 管理，前端不接触 token。
     * 仅存储非敏感的 user 信息用于 UI 登录状态显示。
     */
    setAuthToken(token, user) {
      // token 参数保留以兼容现有调用方，但不存储到 localStorage。
      // token 由后端通过 Set-Cookie: maop_token=...; HttpOnly; Secure; SameSite=Strict 设置。
      try {
        if (user) localStorage.setItem(USER_KEY, user);
        else localStorage.removeItem(USER_KEY);
      } catch { /* ignore */ }
    },
    /**
     * 清除登录态（登出）。P1 fix: 通知后端撤销 JWT token。
     * M6 fix: token 由后端 httpOnly cookie 管理，前端只需清除 user 信息。
     */
    async clearAuthToken() {
      // Notify backend to revoke the token before clearing locally
      try {
        await fetchWithTimeout('/api/auth/logout', withAuth({ method: 'POST' }, {}));
      } catch { /* best-effort — clear locally anyway */ }
      // M6 fix: 后端会通过 Set-Cookie 清除 httpOnly cookie，前端只需清除 user 信息。
      try {
        localStorage.removeItem(USER_KEY);
      } catch { /* ignore */ }
    },
  },
});

// 模块级导出（便于非 Pinia 上下文使用，如 App.vue 直接 import）
// P1 fix: 导出 fetchWithTimeout 供 edition store 等复用同一套超时机制。
export { getAuthToken, withAuth, handleUnauthorized, isLoggedIn, fetchWithTimeout };
