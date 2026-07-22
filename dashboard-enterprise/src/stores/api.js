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
  return init;
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

export const useApiStore = defineStore('api', {
  actions: {
    /**
     * GET 请求，自动注入 Bearer token。
     * @param {string} url
     * @param {object} [opts] { headers } 可选额外 headers
     */
    async get(url, opts) {
      const res = await fetch(url, withAuth({}, (opts && opts.headers) || {}));
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
      const res = await fetch(url, withAuth(
        { method: 'POST', body: JSON.stringify(body || {}) },
        headers
      ));
      if (res.status === 401) {
        handleUnauthorized();
        throw new Error(`API ${url}: 401 Unauthorized`);
      }
      if (!res.ok) throw new Error(`API ${url}: ${res.status}`);
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
     * 清除 token（登出）。
     */
    clearAuthToken() {
      try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      } catch (e) { /* ignore */ }
    },
  },
});

// 模块级导出（便于非 Pinia 上下文使用，如 App.vue 直接 import）
export { getAuthToken, withAuth, handleUnauthorized };
