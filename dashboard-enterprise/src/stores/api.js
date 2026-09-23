import { defineStore } from 'pinia';
import { useI18n } from '../i18n/index.js';

// M6 fix: token 从 localStorage 迁移到 httpOnly cookie（由后端 Set-Cookie 设置）。
// httpOnly cookie 无法被 JavaScript 读取，避免 XSS 攻击窃取 token。
// 前端不再直接接触 token，所有请求通过 withCredentials: true 自动携带 cookie。
// 保留 USER_KEY 用于存储非敏感的用户名信息（UI 显示登录状态）。
const USER_KEY = 'maop_user';

// L3 fix: 提取硬编码的 API 路径为命名常量，便于统一维护与路径变更。
const API_ENDPOINTS = {
  AUTH_REFRESH: '/api/auth/refresh',
  AUTH_LOGOUT: '/api/auth/logout',
};

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
 * 把 HTTP 错误映射为**已本地化**的提示。
 *
 * 背景（2026-09-23 截图验证发现）：原先各处直接
 * ``throw new Error(errBody.error || `API ${url}: ${res.status}`)``，
 * 抛的是**后端原始英文串**（如 "Admin role required"）。视图把它当作错误
 * 详情渲染，于是中文界面里也会出现英文 —— 实测在 Tasks / Overview / Agents
 * 三个视图都能复现。
 *
 * 策略：常见状态码走 i18n 文案（用户看得懂），并保留原始 error 串作为
 * 补充信息（便于排查）；非预期状态码则回退原始串，不吞信息。
 *
 * @param {object} errBody 已解析的响应体（可能为空对象）
 * @param {string} url 请求路径
 * @param {number} status HTTP 状态码
 * @returns {string} 用于 Error.message 的文案
 */
function apiErrorMessage(errBody, url, status) {
  const raw = (errBody && errBody.error) || '';
  const KEY_BY_STATUS = {
    401: 'error.unauthorized',
    403: 'error.forbidden',
    404: 'error.notFound',
    429: 'error.rateLimited',
  };
  let key = KEY_BY_STATUS[status];
  if (!key && status >= 500) key = 'error.server';

  // 技术细节：URL + 状态码。始终保留 —— 这是运维排查的第一手线索，
  // 也是既有测试断言的一部分（如 'API /api/audit/events: 500'）。
  // 去掉查询串：路径才是定位问题的关键，而带全量 query 会撑成一行
  // 70+ 字符的文本，在错误卡片的小字里严重换行（实测 /api/sessions 那条）。
  const technical = `API ${url.split('?')[0]}: ${status}`;

  if (key) {
    const { t } = useI18n();
    const localized = t(key);
    // t() 在键缺失时返回键本身 —— 此时不要把它当文案用。
    if (localized && localized !== key) {
      return `${localized} (${technical})`;
    }
  }
  return raw || technical;
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
      // M6 fix: 不再从 localStorage 读取 token，依赖 httpOnly cookie 自动携带。
      if (!isLoggedIn()) return false;
      const res = await fetchWithTimeout(API_ENDPOINTS.AUTH_REFRESH, {
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

/**
 * F1: 统一为 Pinia Setup Store 风格（与 edition.js / realtime.js / ui.js 一致）：
 * state 用 ref，getters 用 computed，actions 用函数。
 * 本 store 无 state 和 getters，仅包含 actions（转为普通函数并在 return 中暴露）。
 */
export const useApiStore = defineStore('api', () => {
  /**
   * GET 请求，自动注入 Bearer token。
   * @param {string} url
   * @param {object} [opts] { headers } 可选额外 headers
   */
  async function get(url, opts) {
    let res = await fetchWithTimeout(url, withAuth({}, (opts && opts.headers) || {}));
    if (res.status === 401) {
      await handleUnauthorized();
      // Retry once if refresh succeeded (new token is now in localStorage)
      res = await fetchWithTimeout(url, withAuth({}, (opts && opts.headers) || {}));
      // H3 fix: 消除重复调用 handleUnauthorized()。首次 401 已调用过一次
      // （内部会尝试 refresh，失败则清除登录态并触发 maop:unauthorized 事件）。
      // 重试仍 401 说明 token 确实无效，直接抛错即可，避免事件重复触发。
      if (res.status === 401) {
        throw new Error(apiErrorMessage({}, url, 401));
      }
    }
    if (!res.ok) {
      // P1-4 fix: 与 post/put/del 保持一致，先尝试解析 errBody.error，
      // 给出更具体的错误信息而非仅 HTTP 状态码。
      const errBody = await res.json().catch(() => ({}));
      throw new Error(apiErrorMessage(errBody, url, res.status));
    }
    return res.json();
  }

  /**
   * POST 请求，自动注入 Bearer token 与 Content-Type。
   * @param {string} url
   * @param {object} body JSON body
   * @param {object} [opts] { headers } 可选额外 headers
   */
  async function post(url, body, opts) {
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
      // H3 fix: 消除重复调用 handleUnauthorized()，避免 maop:unauthorized 事件重复触发。
      if (res.status === 401) {
        throw new Error(apiErrorMessage({}, url, 401));
      }
    }
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(apiErrorMessage(errBody, url, res.status));
    }
    return res.json();
  }

  /**
   * PUT 请求，自动注入 Bearer token。
   * @param {string} url
   * @param {object} body JSON body
   * @param {object} [opts] { headers } 可选额外 headers
   */
  async function put(url, body, opts) {
    // F3: Content-Type 只在 withAuth 的 headers 参数中设置一次。
    // withAuth 实现中 init.headers = h 会覆盖 extra.headers，故 extra 内
    // 不再冗余设置 headers（原代码 extra.headers 是被丢弃的死代码）。
    // P1-5 fix: 接受 opts 参数，与 get/post 保持一致。
    const putHeaders = Object.assign(
      { 'Content-Type': 'application/json' },
      (opts && opts.headers) || {}
    );
    let res = await fetchWithTimeout(url, withAuth(
      { method: 'PUT', body: JSON.stringify(body || {}) },
      putHeaders
    ));
    if (res.status === 401) {
      await handleUnauthorized();
      res = await fetchWithTimeout(url, withAuth(
        { method: 'PUT', body: JSON.stringify(body || {}) },
        putHeaders
      ));
      // H3 fix: 消除重复调用 handleUnauthorized()，避免 maop:unauthorized 事件重复触发。
      // 401 走 apiErrorMessage() 本地化（原先硬编码英文 "401 Unauthorized"）。
      if (res.status === 401) { throw new Error(apiErrorMessage({}, url, 401)); }
    }
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(apiErrorMessage(errBody, url, res.status));
    }
    return res.json();
  }

  /**
   * DELETE 请求，自动注入 Bearer token。
   * @param {string} url
   * @param {object} [opts] { headers } 可选额外 headers
   */
  async function del(url, opts) {
    // P1-5 fix: 接受 opts 参数，与 get/post/put 保持一致。
    const delHeaders = (opts && opts.headers) || {};
    let res = await fetchWithTimeout(url, withAuth({ method: 'DELETE' }, delHeaders));
    if (res.status === 401) {
      await handleUnauthorized();
      res = await fetchWithTimeout(url, withAuth({ method: 'DELETE' }, delHeaders));
      // H3 fix: 消除重复调用 handleUnauthorized()，避免 maop:unauthorized 事件重复触发。
      // 401 走 apiErrorMessage() 本地化（原先硬编码英文 "401 Unauthorized"）。
      if (res.status === 401) { throw new Error(apiErrorMessage({}, url, 401)); }
    }
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(apiErrorMessage(errBody, url, res.status));
    }
    return res.json();
  }

  /**
   * 暴露给组件直接使用的工具方法：返回当前 token（便于 UI 显示登录状态）。
   * M6 fix: token 现由 httpOnly cookie 管理，前端无法读取，始终返回空字符串。
   * 登录状态请使用 isLoggedIn() 判断。
   * L2 fix: 标记为废弃，保留仅为向后兼容现有调用方，不应在新代码中使用。
   */
  /* Deprecated: always returns '' after M6 fix — use isLoggedIn() instead */
  function authToken() {
    return getAuthToken();
  }

  /**
   * 判断当前是否已登录。
   * M6 fix: 通过 user 信息存在性判断（token 在 httpOnly cookie 中不可读）。
   */
  function isLoggedInAction() {
    return isLoggedIn();
  }

  /**
   * 设置登录态（登录成功后调用）。
   * M6 fix: token 由后端 Set-Cookie httpOnly 管理，前端不接触 token。
   * 仅存储非敏感的 user 信息用于 UI 登录状态显示。
   */
  function setAuthToken(token, user) {
    // token 参数保留以兼容现有调用方，但不存储到 localStorage。
    // token 由后端通过 Set-Cookie: maop_token=...; HttpOnly; Secure; SameSite=Strict 设置。
    try {
      if (user) localStorage.setItem(USER_KEY, user);
      else localStorage.removeItem(USER_KEY);
    } catch { /* ignore */ }
  }

  /**
   * 清除登录态（登出）。P1 fix: 通知后端撤销 JWT token。
   * M6 fix: token 由后端 httpOnly cookie 管理，前端只需清除 user 信息。
   */
  async function clearAuthToken() {
    // Notify backend to revoke the token before clearing locally
    try {
      await fetchWithTimeout(API_ENDPOINTS.AUTH_LOGOUT, withAuth({ method: 'POST' }, {}));
    } catch { /* best-effort — clear locally anyway */ }
    // M6 fix: 后端会通过 Set-Cookie 清除 httpOnly cookie，前端只需清除 user 信息。
    try {
      localStorage.removeItem(USER_KEY);
    } catch { /* ignore */ }
  }

  return {
    // actions
    get,
    post,
    put,
    delete: del,
    authToken,
    isLoggedIn: isLoggedInAction,
    setAuthToken,
    clearAuthToken,
  };
});

// 模块级导出（便于非 Pinia 上下文使用，如 App.vue 直接 import）
// R7 fix: getAuthToken 为内部实现细节（始终返回空字符串，token 由 httpOnly cookie 管理），
// 不再对外导出。登录状态应通过 isLoggedIn() 判断。
export { withAuth, handleUnauthorized, isLoggedIn, fetchWithTimeout };
