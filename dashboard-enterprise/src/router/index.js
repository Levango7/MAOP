import { createRouter, createWebHistory } from 'vue-router';

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
  // P2-10 fix: catch-all 404 route — redirect unknown paths to overview
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;

// P2-21: Enterprise edition route guard
router.beforeEach((to, from, next) => {
  if (to.meta.requiresEnterprise) {
    try {
      const edition = JSON.parse(localStorage.getItem('maop_edition') || '{}');
      if (edition && edition.edition && edition.edition !== 'enterprise') {
        next('/');
        return;
      }
    } catch {}
  }
  next();
});
