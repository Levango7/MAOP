import { createRouter, createWebHistory } from 'vue-router';
import { useEditionStore } from '../stores/edition.js';

const routes = [
  { path: '/', name: 'overview', component: () => import('../views/Overview.vue') },
  { path: '/control', name: 'control', component: () => import('../views/ControlPanel.vue') },
  { path: '/chat', name: 'chat', component: () => import('../views/Chat.vue') },
  { path: '/agents', name: 'agents', component: () => import('../views/Agents.vue') },
  { path: '/memory', name: 'memory', component: () => import('../views/ThreeLayerMemory.vue') },
  { path: '/evolve', name: 'evolve', component: () => import('../views/Evolve.vue') },
  { path: '/search', name: 'search', component: () => import('../views/Search.vue') },
  { path: '/vector', name: 'vector', component: () => import('../views/VectorSearch.vue') },
  { path: '/models', name: 'models', component: () => import('../views/Models.vue') },
  { path: '/tools', name: 'tools', component: () => import('../views/Tools.vue') },
  { path: '/logs', name: 'logs', component: () => import('../views/Logs.vue') },
  { path: '/monitor', name: 'monitor', component: () => import('../views/Monitor.vue') },
  { path: '/cost', name: 'cost', component: () => import('../views/Cost.vue') },
  { path: '/audit', name: 'audit', component: () => import('../views/Audit.vue'), meta: { requiresEnterprise: true } },
  { path: '/rbac', name: 'rbac', component: () => import('../views/RBAC.vue'), meta: { requiresEnterprise: true } },
  { path: '/tenants', name: 'tenants', component: () => import('../views/Tenants.vue'), meta: { requiresEnterprise: true } },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/users', name: 'users', component: () => import('../views/Users.vue') },
  { path: '/docs', name: 'docs', component: () => import('../views/Docs.vue') },
  // P2-10 fix: catch-all 404 route — redirect unknown paths to overview
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;

// P2-21: Enterprise edition route guard.
// Reads the live edition store (the single source of truth). Falls back to
// a persisted localStorage snapshot for the cold-load case where navigation
// runs before the async fetch completes.
router.beforeEach((to) => {
  if (!to.meta.requiresEnterprise) return true;
  let editionVal = 'enterprise';
  try {
    const st = useEditionStore();
    if (st && st.edition) editionVal = st.edition;
    else {
      const snap = JSON.parse(localStorage.getItem('maop_edition') || '{}');
      editionVal = snap.edition || 'enterprise';
    }
  } catch {}
  if (editionVal !== 'enterprise') return '/';
  return true;
});
