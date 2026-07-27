import { defineStore } from 'pinia';
import { withAuth, handleUnauthorized } from './api.js';

export const useEditionStore = defineStore('edition', {
  state: () => ({
    edition: 'enterprise',
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
        this.edition = data.edition || 'enterprise';
        this.features = data.features || {};
        this.backends = data.backends || {};
        this.degradations = data.degradations || [];
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