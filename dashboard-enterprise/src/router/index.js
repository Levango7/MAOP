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
  { path: '/audit', name: 'audit', component: () => import('../views/Audit.vue') },
  { path: '/rbac', name: 'rbac', component: () => import('../views/RBAC.vue') },
  { path: '/tenants', name: 'tenants', component: () => import('../views/Tenants.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
