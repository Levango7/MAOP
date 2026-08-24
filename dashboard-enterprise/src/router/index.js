import { createRouter, createWebHistory } from 'vue-router';
import { useEditionStore } from '../stores/edition.js';

// P1-4 fix: 每个懒加载路由显式声明 chunk 名（Vite 自动按文件路径命名，
// 显式注释让 chunk 命名稳定可控，便于预加载策略与构建产物分析）。
const routes = [
  { path: '/', name: 'overview', component: () => import(/* webpackChunkName: "overview" */ '../views/Overview.vue') },
  // 迭代 A (RFC-001): /run 合并 Control + Chat; /evolve 吸收 evolution-history
  { path: '/run', name: 'run', component: () => import(/* webpackChunkName: "run" */ '../views/Run.vue') },
  // 旧深链 301 重定向,保留书签/外部链接可用
  { path: '/control', redirect: { path: '/run', query: { tab: 'structured' } } },
  { path: '/chat', redirect: { path: '/run', query: { tab: 'chat' } } },
  { path: '/evolution-history', redirect: { path: '/evolve', query: { tab: 'history' } } },
  { path: '/agents', name: 'agents', component: () => import(/* webpackChunkName: "agents" */ '../views/Agents.vue') },
  // P1-3: 任务历史页 — 搜索/过滤/分页/重跑
  { path: '/tasks', name: 'tasks', component: () => import(/* webpackChunkName: "tasks" */ '../views/Tasks.vue') },
  { path: '/memory', name: 'memory', component: () => import(/* webpackChunkName: "memory" */ '../views/ThreeLayerMemory.vue') },
  // P1-B: 漏斗记忆面板 — L0 证据 / L1 原子事实 / 任务状态图
  { path: '/funnel-memory', name: 'funnel-memory', component: () => import(/* webpackChunkName: "funnel-memory" */ '../views/FunnelMemory.vue'), meta: { title: '漏斗记忆', icon: 'database' } },
  { path: '/evolve', name: 'evolve', component: () => import(/* webpackChunkName: "evolve" */ '../views/Evolve.vue') },
  { path: '/search', name: 'search', component: () => import(/* webpackChunkName: "search" */ '../views/Search.vue') },
  { path: '/vector', name: 'vector', component: () => import(/* webpackChunkName: "vector" */ '../views/VectorSearch.vue') },
  { path: '/models', name: 'models', component: () => import(/* webpackChunkName: "models" */ '../views/Models.vue') },
  { path: '/tools', name: 'tools', component: () => import(/* webpackChunkName: "tools" */ '../views/Tools.vue') },
  { path: '/logs', name: 'logs', component: () => import(/* webpackChunkName: "logs" */ '../views/Logs.vue') },
  { path: '/monitor', name: 'monitor', component: () => import(/* webpackChunkName: "monitor" */ '../views/Monitor.vue') },
  { path: '/observability', name: 'observability', component: () => import(/* webpackChunkName: "observability" */ '../views/Observability.vue') },
  { path: '/cost', name: 'cost', component: () => import(/* webpackChunkName: "cost" */ '../views/Cost.vue') },
  { path: '/audit', name: 'audit', component: () => import(/* webpackChunkName: "audit" */ '../views/Audit.vue'), meta: { requiresEnterprise: true } },
  { path: '/rbac', name: 'rbac', component: () => import(/* webpackChunkName: "rbac" */ '../views/RBAC.vue'), meta: { requiresEnterprise: true } },
  { path: '/tenants', name: 'tenants', component: () => import(/* webpackChunkName: "tenants" */ '../views/Tenants.vue'), meta: { requiresEnterprise: true } },
  // v4.6.0 企业版新功能路由（懒加载 + requiresEnterprise 守卫，personal 版重定向到 '/'）
  { path: '/licenses', name: 'licenses', component: () => import(/* webpackChunkName: "licenses" */ '../views/Licenses.vue'), meta: { requiresEnterprise: true } },
  { path: '/sso', name: 'sso', component: () => import(/* webpackChunkName: "sso" */ '../views/SsoProviders.vue'), meta: { requiresEnterprise: true } },
  { path: '/quotas', name: 'quotas', component: () => import(/* webpackChunkName: "quotas" */ '../views/Quotas.vue'), meta: { requiresEnterprise: true } },
  { path: '/apikeys', name: 'apikeys', component: () => import(/* webpackChunkName: "apikeys" */ '../views/ApiKeys.vue'), meta: { requiresEnterprise: true } },
  { path: '/settings', name: 'settings', component: () => import(/* webpackChunkName: "settings" */ '../views/Settings.vue') },
  // v4.6.0 通知中心（通用功能，不限定企业版）
  { path: '/notifications', name: 'notifications', component: () => import(/* webpackChunkName: "notifications" */ '../views/Notifications.vue') },
  { path: '/users', name: 'users', component: () => import(/* webpackChunkName: "users" */ '../views/Users.vue'), meta: { requiresEnterprise: true } },
  { path: '/docs', name: 'docs', component: () => import(/* webpackChunkName: "docs" */ '../views/Docs.vue') },
  // v4.5.0: Knowledge graph visualization (general-availability, no enterprise guard)
  { path: '/knowledge-graph', name: 'knowledge-graph', component: () => import(/* webpackChunkName: "knowledge-graph" */ '../views/KnowledgeGraph.vue') },
  // v5.1.0: Workflow editor / Skill editor / Skill market (general-availability)
  { path: '/workflow-editor', name: 'workflow-editor', component: () => import(/* webpackChunkName: "workflow-editor" */ '../views/WorkflowEditor.vue') },
  { path: '/skill-editor', name: 'skill-editor', component: () => import(/* webpackChunkName: "skill-editor" */ '../views/SkillEditor.vue') },
  { path: '/skill-market', name: 'skill-market', component: () => import(/* webpackChunkName: "skill-market" */ '../views/SkillMarket.vue') },
  // P2-10 fix: catch-all 404 route — redirect unknown paths to overview
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;

// P1-4: 高频路由预加载策略。
// 当用户进入任意页面时，在浏览器空闲时段（requestIdleCallback）预取
// Overview / Run 两个高频访问路由的 chunk，缩短后续跳转的首屏耗时。
// 仅在支持 requestIdleCallback 的浏览器启用，避免在不支持的环境抛错。
//
// P1 fix: 原配置包含 'chat'，但 /chat 已被重定向到 /run，不存在名为 'chat'
// 的命名路由，导致 router.resolve({ name: 'chat' }) 抛错使整个预加载循环
// 中断。改为预加载实际存在的 'run' 路由，并为每个路由解析加 try/catch
// 防御，避免单一路由解析失败影响其他路由预加载。
const PREFETCH_ROUTES = new Set(['overview', 'run']);
const prefetched = new Set();
function prefetchHighFrequencyRoutes() {
  if (typeof window === 'undefined') return;
  const schedule =
    window.requestIdleCallback ||
    ((cb) => setTimeout(cb, 1200));
  schedule(() => {
    for (const name of PREFETCH_ROUTES) {
      if (prefetched.has(name)) continue;
      let route;
      try {
        route = router.resolve({ name });
      } catch {
        // 命名路由不存在或解析失败 — 跳过，不影响其他路由预加载
        continue;
      }
      if (!route || !route.matched.length) continue;
      // 触发懒加载组件的 import()，将 chunk 加入浏览器缓存
      const components = route.matched.map((m) => m.components?.default);
      Promise.all(
        components
          .filter((c) => typeof c === 'function')
          .map((c) => Promise.resolve(c()).catch(() => {})),
      ).then(() => prefetched.add(name));
    }
  });
}

// P1-4 fix: vue-router 4.x 移除了 onReady（3.x API），改用 isReady().then()
router.isReady().then(() => {
  prefetchHighFrequencyRoutes();
});

// P2-21 / P1-H1: Enterprise edition route guard.
// Reads the live edition store (the single source of truth). Falls back to
// a persisted localStorage snapshot for the cold-load case where navigation
// runs before the async fetch completes.
//
// P1-H1 安全修复: 冷加载默认改为 'personal'（安全失败模式）。后端未就绪时
// 绝不对企业版路由放行，个人版用户无法绕过企业版路由守卫。后端
// /api/info/config 就绪后由 hydrateEditionFromConfig() 异步 hydrate store，
// 后续 SPA 导航使用真实 edition。
const VALID_EDITIONS = new Set(['enterprise', 'personal']);

let editionConfigHydrated = false;
// 异步从后端 /api/info/config 获取 edition 并 hydrate store。fire-and-forget：
// 不阻塞当前导航，完成后影响后续 SPA 导航。冷加载守卫不等后端，直接用
// store/localStorage/personal-default 同步决策。
function hydrateEditionFromConfig() {
  if (editionConfigHydrated) return;
  editionConfigHydrated = true;
  fetch('/api/info/config')
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data || !VALID_EDITIONS.has(data.edition)) return;
      try {
        const st = useEditionStore();
        if (st) {
          st.edition = data.edition;
          // 同步持久化快照，供下次冷加载读取
          localStorage.setItem('maop_edition', JSON.stringify({
            edition: st.edition,
            features: st.features,
            backends: st.backends,
            degradations: st.degradations,
          }));
        }
      } catch { /* ignore */ }
    })
    .catch(() => {});
}

router.beforeEach((to) => {
  if (!to.meta.requiresEnterprise) return true;
  // 安全默认: 'personal' —— 冷加载/后端未就绪时不放行企业版路由
  let editionVal = 'personal';
  try {
    const st = useEditionStore();
    if (st && st.edition && VALID_EDITIONS.has(st.edition)) editionVal = st.edition;
    else {
      const snap = JSON.parse(localStorage.getItem('maop_edition') || '{}');
      editionVal = VALID_EDITIONS.has(snap.edition) ? snap.edition : 'personal';
    }
  } catch { /* ignore */ }
  // 触发后端 config hydrate（不阻塞本次导航），供后续 SPA 导航使用真实 edition
  hydrateEditionFromConfig();
  if (editionVal !== 'enterprise') return '/';
  return true;
});

// P1-4: 路由解析完成后再次触发预加载（覆盖 onReady 未触发的场景）
router.afterEach(() => {
  prefetchHighFrequencyRoutes();
});
