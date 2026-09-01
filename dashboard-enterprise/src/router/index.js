import { createRouter, createWebHistory } from 'vue-router';
import { useEditionStore } from '../stores/edition.js';

const routes = [
  // ═══ 2026-09-01 信息架构重设计：5 组任务流导航（一号用户驱动）═══
  // 每个顶层路径挂载现有 View（零 View 改动），旧路径全部 301 重定向。
  // 设计原则：用户任务流（看状态/做事/记忆/能力/运维）而非后端模块罗列。

  // ── 首页 ──
  { path: '/home', name: 'overview', component: () => import('../views/Overview.vue') },
  { path: '/home/tasks', name: 'tasks', component: () => import('../views/Tasks.vue') },

  // ── 执行 ──
  { path: '/run', name: 'run', component: () => import('../views/Run.vue') },
  { path: '/run/agents', name: 'dispatch', component: () => import('../views/ControlPanel.vue') },

  // ── 记忆 ──
  { path: '/memory', name: 'memory', component: () => import('../views/ThreeLayerMemory.vue') },
  { path: '/memory/search', name: 'search', component: () => import('../views/Search.vue') },
  { path: '/memory/graph', name: 'knowledge-graph', component: () => import('../views/KnowledgeGraph.vue') },

  // ── 能力 ──
  { path: '/capability/agents', name: 'agents', component: () => import('../views/Agents.vue') },
  { path: '/capability/skills', name: 'skills', component: () => import('../views/Tools.vue') },
  { path: '/capability/market', name: 'skill-market', component: () => import('../views/SkillMarket.vue') },
  { path: '/capability/models', name: 'models', component: () => import('../views/Models.vue') },

  // ── 运维 ──
  { path: '/operate', name: 'monitor', component: () => import('../views/Monitor.vue') },
  { path: '/operate/logs', name: 'logs', component: () => import('../views/Logs.vue') },
  { path: '/operate/tracing', name: 'observability', component: () => import('../views/Observability.vue') },
  { path: '/operate/cost', name: 'cost', component: () => import('../views/Cost.vue') },

  // ── 管理（企业版专属，守卫保持不变）──
  { path: '/audit', name: 'audit', component: () => import('../views/Audit.vue'), meta: { requiresEnterprise: true } },
  { path: '/rbac', name: 'rbac', component: () => import('../views/RBAC.vue'), meta: { requiresEnterprise: true } },
  { path: '/tenants', name: 'tenants', component: () => import('../views/Tenants.vue'), meta: { requiresEnterprise: true } },
  { path: '/licenses', name: 'licenses', component: () => import('../views/Licenses.vue'), meta: { requiresEnterprise: true } },
  { path: '/sso', name: 'sso', component: () => import('../views/SsoProviders.vue'), meta: { requiresEnterprise: true } },
  { path: '/quotas', name: 'quotas', component: () => import('../views/Quotas.vue'), meta: { requiresEnterprise: true } },
  { path: '/apikeys', name: 'apikeys', component: () => import('../views/ApiKeys.vue'), meta: { requiresEnterprise: true } },
  { path: '/users', name: 'users', component: () => import('../views/Users.vue'), meta: { requiresEnterprise: true } },

  // ── 设置 ──
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/notifications', name: 'notifications', component: () => import('../views/Notifications.vue') },
  { path: '/docs', name: 'docs', component: () => import('../views/Docs.vue') },

  // ═══ 旧路径 301 重定向（书签/深链/外部链接兼容，零断链）═══
  { path: '/', redirect: '/home' },
  { path: '/control', redirect: { path: '/run', query: { tab: 'structured' } } },
  { path: '/chat', redirect: { path: '/run', query: { tab: 'chat' } } },
  { path: '/agents', redirect: '/capability/agents' },
  { path: '/tasks', redirect: '/home/tasks' },
  { path: '/search', redirect: '/memory/search' },
  { path: '/vector', redirect: '/memory/search' },
  { path: '/knowledge-graph', redirect: '/memory/graph' },
  { path: '/tools', redirect: '/capability/skills' },
  { path: '/models', redirect: '/capability/models' },
  { path: '/monitor', redirect: '/operate' },
  { path: '/logs', redirect: '/operate/logs' },
  { path: '/observability', redirect: '/operate/tracing' },
  { path: '/cost', redirect: '/operate/cost' },
  { path: '/skill-market', redirect: '/capability/market' },
  { path: '/skill-editor', redirect: '/capability/skills' },
  // 暂降级入口：workflow-editor 并入执行组的高编排模式（原页面保留可达）
  { path: '/workflow-editor', name: 'workflow-editor', component: () => import('../views/WorkflowEditor.vue') },
  // 自演化并入记忆组（原页面保留可达，导航不再罗列）
  { path: '/evolve', name: 'evolve', component: () => import('../views/Evolve.vue') },
  { path: '/evolution-history', redirect: { path: '/evolve', query: { tab: 'history' } } },

  // P2-10 fix: catch-all 404 route — redirect unknown paths to home
  { path: '/:pathMatch(.*)*', redirect: '/home' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;

// P1-4: 高频路由预加载策略。
// 当用户进入任意页面时，在浏览器空闲时段（requestIdleCallback）预取
// 高频访问路由的 chunk，缩短后续跳转的首屏耗时。
// 仅在支持 requestIdleCallback 的浏览器启用，避免在不支持的环境抛错。
// 一号用户实测修复（2026-08-31）：原集合含 'chat'，但 /chat 是无 name 的
// redirect 路由（重定向到 /run）——router.resolve({name:'chat'}) 每次页面
// 加载都抛 Uncaught Error（vendor-vue resolve 报错）。改为预取真实存在的
// overview + run（迭代 A 合并后 Chat 被吸收进 Run）。
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
      const route = router.resolve({ name });
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
