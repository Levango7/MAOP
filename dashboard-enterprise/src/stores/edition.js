import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { withAuth, handleUnauthorized, fetchWithTimeout } from './api.js';

// L3 fix: 提取硬编码的 API 路径为命名常量，便于统一维护与路径变更。
const API_ENDPOINT_EDITION = '/api/info/edition';

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

/**
 * useEditionStore — 运行时 edition 状态（personal / enterprise）。
 *
 * F1: 统一为 Pinia Setup Store 风格（与 ui.js / realtime.js 一致）：
 * state 用 ref，getters 用 computed，actions 用函数。
 */
export const useEditionStore = defineStore('edition', () => {
  // ---- state ----
  const edition = ref(loadInitialEdition());
  const features = ref({});
  const backends = ref({});
  const degradations = ref([]);
  const loading = ref(false);
  const switching = ref(false);
  const switchError = ref('');

  // ---- getters ----
  const isEnterprise = computed(() => edition.value === 'enterprise');
  const isPersonal = computed(() => edition.value === 'personal');
  const hasFeature = computed(() => (name) => !!features.value[name]);
  const hasDegradations = computed(() => degradations.value.length > 0);

  // ---- actions ----
  async function fetchEdition() {
    loading.value = true;
    try {
      // Inject Bearer token (aligned with dashboard/js/app-core.js)
      // M1 fix: 使用 fetchWithTimeout 添加超时保护，防止后端无响应时请求永久挂起。
      // M3 fix: 401 处理与 api store 行为统一——先 handleUnauthorized()（内部尝试
      // refresh token），refresh 成功后重试一次；重试仍 401 才放弃。
      let res = await fetchWithTimeout(API_ENDPOINT_EDITION, withAuth({}, {}));
      if (res.status === 401) {
        await handleUnauthorized();
        res = await fetchWithTimeout(API_ENDPOINT_EDITION, withAuth({}, {}));
        // M1 fix: 重试仍 401 时设置 switchError，让调用方可感知鉴权失败，
        // 而非静默 return 导致调用方误以为 fetch 成功但未更新状态。
        if (res.status === 401) {
          switchError.value = 'Authentication required';
          return;
        }
      }
      if (!res.ok) { console.error('Failed to fetch edition info: HTTP', res.status); return; }
      const data = await res.json();
      // P1-H1: 后端未返回有效 edition 时 fallback 'personal'（安全失败）
      edition.value = data.edition || 'personal';
      features.value = data.features || {};
      backends.value = data.backends || {};
      degradations.value = data.degradations || [];
      persistEdition(edition.value, features.value, backends.value, degradations.value);
    } catch (e) {
      console.error('Failed to fetch edition info:', e);
    } finally {
      loading.value = false;
    }
  }

  /**
   * 切换运行时 edition（需 admin 权限）。
   * @param {string} targetEdition 'personal' | 'enterprise'
   * @returns {Promise<object>} 后端返回的切换结果 {status, edition, previous, ...}
   * @throws {Error} 切换失败时抛错（含 HTTP 状态码或后端错误信息）
   */
  async function switchEdition(targetEdition) {
    switching.value = true;
    switchError.value = '';
    try {
      // M2 fix: 使用 fetchWithTimeout 添加超时保护，防止后端无响应时请求永久挂起。
      // M3 fix: 401 处理与 api store 行为统一——先 handleUnauthorized()（内部尝试
      // refresh token），refresh 成功后重试一次；重试仍 401 才抛错。
      let res = await fetchWithTimeout(API_ENDPOINT_EDITION, withAuth(
        { method: 'POST', body: JSON.stringify({ edition: targetEdition }) },
        { 'Content-Type': 'application/json' }
      ));
      if (res.status === 401) {
        await handleUnauthorized();
        res = await fetchWithTimeout(API_ENDPOINT_EDITION, withAuth(
          { method: 'POST', body: JSON.stringify({ edition: targetEdition }) },
          { 'Content-Type': 'application/json' }
        ));
        if (res.status === 401) { throw new Error('401 Unauthorized'); }
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data.error || data.detail || `Switch failed: HTTP ${res.status}`;
        switchError.value = msg;
        throw new Error(msg);
      }
      // 刷新完整 edition 信息（features/backends/degradations 可能已变化）
      await fetchEdition();
      // L1 fix: 移除冗余的 persistEdition() 调用——fetchEdition() 内部成功
      // 获取数据后已调用 persistEdition() 持久化最新状态，此处重复调用
      // 不仅冗余，还在 fetchEdition 失败时用旧数据覆盖可能已部分更新的状态。
      return data;
    } catch (e) {
      switchError.value = e.message || String(e);
      throw e;
    } finally {
      switching.value = false;
    }
  }

  return {
    // state
    edition,
    features,
    backends,
    degradations,
    loading,
    switching,
    switchError,
    // getters
    isEnterprise,
    isPersonal,
    hasFeature,
    hasDegradations,
    // actions
    fetchEdition,
    switchEdition,
  };
});
