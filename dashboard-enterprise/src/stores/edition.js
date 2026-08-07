import { defineStore } from 'pinia';
import { withAuth, handleUnauthorized } from './api.js';

function persistEdition(edition, features, backends, degradations) {
  try {
    localStorage.setItem('maop_edition', JSON.stringify({
      edition, features, backends, degradations,
    }));
  } catch { /* ignore */ }
}

// Cold-load helper: read the persisted localStorage snapshot so the pinia
// store is hydrated with the last known edition at construction time.
// This makes the very first router.beforeEach (which runs before App.vue
// onMounted / fetchEdition) observe the real edition rather than a hardcoded
// default, so the localStorage snapshot branch in the guard is no longer dead
// code on a cold page.goto. SSR-safe via try/catch.
//
// P1-H1: 安全失败默认改为 'personal'。冷加载时若无 localStorage 快照，绝不对
// 企业版路由放行——个人版用户无法绕过企业版路由守卫。后端 /api/info/config
// 就绪后由 fetchEdition() hydrate 真实 edition。
function loadInitialEdition() {
  try {
    const snap = JSON.parse(localStorage.getItem('maop_edition') || '{}');
    return snap.edition || 'personal';
  } catch {
    return 'personal';
  }
}

export const useEditionStore = defineStore('edition', {
  state: () => ({
    edition: loadInitialEdition(),
    features: {},
    backends: {},
    degradations: [],
    loading: false,
    switching: false,
    switchError: '',
  }),
  actions: {
    async fetchEdition() {
      this.loading = true;
      try {
        // Inject Bearer token (aligned with dashboard/js/app-core.js)
        const res = await fetch('/api/info/edition', withAuth({}, {}));
        if (res.status === 401) { handleUnauthorized(); return; }
        if (!res.ok) { console.error('Failed to fetch edition info: HTTP', res.status); return; }
        const data = await res.json();
        // P1-H1: 后端未返回有效 edition 时 fallback 'personal'（安全失败）
        this.edition = data.edition || 'personal';
        this.features = data.features || {};
        this.backends = data.backends || {};
        this.degradations = data.degradations || [];
        persistEdition(this.edition, this.features, this.backends, this.degradations);
      } catch (e) {
        console.error('Failed to fetch edition info:', e);
      } finally {
        this.loading = false;
      }
    },
    /**
     * 切换运行时 edition（需 admin 权限）。
     * @param {string} targetEdition 'personal' | 'enterprise'
     * @returns {Promise<object>} 后端返回的切换结果 {status, edition, previous, ...}
     * @throws {Error} 切换失败时抛错（含 HTTP 状态码或后端错误信息）
     */
    async switchEdition(targetEdition) {
      this.switching = true;
      this.switchError = '';
      try {
        const res = await fetch('/api/info/edition', withAuth(
          { method: 'POST', body: JSON.stringify({ edition: targetEdition }) },
          { 'Content-Type': 'application/json' }
        ));
        if (res.status === 401) { handleUnauthorized(); throw new Error('401 Unauthorized'); }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const msg = data.error || data.detail || `Switch failed: HTTP ${res.status}`;
          this.switchError = msg;
          throw new Error(msg);
        }
        // 刷新完整 edition 信息（features/backends/degradations 可能已变化）
        await this.fetchEdition();
        persistEdition(this.edition, this.features, this.backends, this.degradations);
        return data;
      } catch (e) {
        this.switchError = e.message || String(e);
        throw e;
      } finally {
        this.switching = false;
      }
    },
  },
  getters: {
    isEnterprise: (state) => state.edition === 'enterprise',
    isPersonal: (state) => state.edition === 'personal',
    hasFeature: (state) => (name) => !!state.features[name],
    hasDegradations: (state) => state.degradations.length > 0,
  },
});