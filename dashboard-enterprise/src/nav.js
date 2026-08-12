/**
 * Single source of truth for navigation + page header metadata.
 *
 * Both the sidebar (App.vue) and every page header (PageHeader.vue) read from
 * this list, so the sidebar icon/label and the page title/icon/subtitle are
 * guaranteed to stay in sync. Add a new page here and it automatically gets a
 * matching sidebar entry AND a consistent page header (icon + title + subtitle).
 *
 * `subtitle` is an i18n key rendered as the one-line description under the
 * page title. Keep these keys in src/i18n/index.js (coreMessages).
 */
export const nav = [
  // ── 迭代 A (RFC-001): 按用户旅程重排。合并 Control+Chat→/run,
  // Evolve+EvolutionHistory→/evolve。旧路由在 router/index.js 做 301 重定向。
  { section: 'nav.workbench' },
  { to: '/', label: 'nav.overview', icon: 'overview', subtitle: 'nav.overview.subtitle' },
  { to: '/monitor', label: 'nav.monitor', icon: 'activity', subtitle: 'nav.monitor.subtitle' },

  { section: 'nav.build' },
  { to: '/run', label: 'nav.run', icon: 'play', subtitle: 'nav.run.subtitle' },
  { to: '/agents', label: 'nav.agents', icon: 'bot', subtitle: 'nav.agents.subtitle' },
  { to: '/evolve', label: 'nav.evolve', icon: 'sparkles', subtitle: 'nav.evolve.subtitle', matchPaths: ['/evolution-history'] },

  { section: 'nav.assets' },
  { to: '/memory', label: 'nav.memory', icon: 'brain', subtitle: 'nav.memory.subtitle' },
  { to: '/knowledge-graph', label: 'nav.knowledgeGraph', icon: 'network', subtitle: 'nav.knowledgeGraph.subtitle' },
  { to: '/search', label: 'nav.search', icon: 'search', subtitle: 'nav.search.subtitle' },
  { to: '/vector', label: 'nav.vector', icon: 'box', subtitle: 'nav.vector.subtitle' },
  { to: '/tools', label: 'nav.tools', icon: 'wrench', subtitle: 'nav.tools.subtitle' },

  { section: 'nav.observe' },
  { to: '/observability', label: 'nav.observability', icon: 'activity', subtitle: 'nav.observability.subtitle' },
  { to: '/logs', label: 'nav.logs', icon: 'scroll', subtitle: 'nav.logs.subtitle' },
  { to: '/cost', label: 'nav.cost', icon: 'dollar', subtitle: 'nav.cost.subtitle' },

  { section: 'nav.govern' },
  { to: '/models', label: 'nav.models', icon: 'gauge', subtitle: 'nav.models.subtitle' },
  { to: '/audit', label: 'nav.audit', icon: 'clipboard', subtitle: 'nav.audit.subtitle', enterprise: true },
  { to: '/rbac', label: 'nav.rbac', icon: 'shield', subtitle: 'nav.rbac.subtitle', enterprise: true },
  { to: '/tenants', label: 'nav.tenants', icon: 'building', subtitle: 'nav.tenants.subtitle', enterprise: true },
  { to: '/users', label: 'nav.users', icon: 'user', subtitle: 'nav.users.subtitle', enterprise: true },

  { section: 'nav.system' },
  { to: '/settings', label: 'nav.settings', icon: 'gear', subtitle: 'nav.settings.subtitle' },
  { to: '/docs', label: 'nav.docs', icon: 'book-open', subtitle: 'nav.docs.subtitle' },
];

/**
 * nav 过滤: 个人版隐藏 enterprise:true 的项 (audit/rbac/tenants/users)。
 * 原行为: 菜单照常显示, 点击后路由守卫重定向到 '/'.
 * 问题: 用户看到菜单却在点击时被弹走, 反馈是"跳出来的东西不太正常"。
 * 新行为: 个人版根本不显示这些菜单——所见即所得, 不会有的点了没反应。
 *
 * 过滤在渲染层做(App.vue 模板), nav.js 保持单一事实源不变。
 *
 * @param {Array} navList - 原始 nav 数组
 * @param {string} edition - 'enterprise' | 'personal'
 * @returns {Array} 过滤后的 nav 数组, section 标题若组内为空也会被隐藏
 */
export function filterNavByEdition(navList, edition) {
  if (edition === 'enterprise') return navList;
  const filtered = navList.filter((item) => !item.enterprise);
  // 隐藏 enterprise 组特有的 section (nav.govern 只剩 models 一个非 enterprise 项,
  // 但 audit/rbac/tenants/users 全没了 → section 标题也应隐藏)
  const result = [];
  for (const item of filtered) {
    if (item.section) {
      // section 可见: 只有当其后至少有一个非 section 项时才显示
      const idx = filtered.indexOf(item);
      const hasVisibleChild = filtered.slice(idx + 1).some((n) => !n.section && !n.enterprise);
      if (hasVisibleChild) result.push(item);
    } else {
      result.push(item);
    }
  }
  return result;
}

/**
 * Resolve page metadata from a route path.
 *
 * `/` matches exactly. Every other entry matches by EXACT path first, then by
 * path prefix (top-level segments are unique, so no conflict in practice).
 *
 * IMPORTANT: we must NOT match `/` as a prefix (`'/chat'.startsWith('/')` is
 * true), otherwise every page would resolve to the Overview entry. Exact match
 * is always tried before prefix match to guarantee the icon/title stay unique
 * per page.
 */
export function getPageMeta(path) {
  if (!path) return null;
  if (path === '/') return nav.find((n) => n.to === '/') || null;
  const exact = nav.find((n) => n.to === path);
  if (exact) return exact;
  return nav.find((n) => n.to && n.to !== '/' && path.startsWith(n.to)) || null;
}
