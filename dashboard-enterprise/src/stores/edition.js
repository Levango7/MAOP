import { defineStore } from 'pinia';
import { withAuth, handleUnauthorized } from './api.js';

export const useEditionStore = defineStore('edition', {
  state: () => ({
    edition: 'enterprise',
    features: {},
    backends: {},
    degradations: [],
    loading: false,
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
  },
  getters: {
    isEnterprise: (state) => state.edition === 'enterprise',
    isPersonal: (state) => state.edition === 'personal',
    hasFeature: (state) => (name) => !!state.features[name],
    hasDegradations: (state) => state.degradations.length > 0,
  },
});