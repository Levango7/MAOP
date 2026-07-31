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
  { section: 'nav.core' },
  { to: '/', label: 'nav.overview', icon: 'overview', subtitle: 'nav.overview.subtitle' },
  { to: '/control', label: 'nav.control', icon: 'play', subtitle: 'nav.control.subtitle' },
  { to: '/chat', label: 'nav.chat', icon: 'chat', subtitle: 'nav.chat.subtitle' },
  { to: '/agents', label: 'nav.agents', icon: 'bot', subtitle: 'nav.agents.subtitle' },
  { to: '/memory', label: 'nav.memory', icon: 'brain', subtitle: 'nav.memory.subtitle' },
  { to: '/evolve', label: 'nav.evolve', icon: 'sparkles', subtitle: 'nav.evolve.subtitle' },
  { section: 'nav.searchTools' },
  { to: '/search', label: 'nav.search', icon: 'search', subtitle: 'nav.search.subtitle' },
  { to: '/vector', label: 'nav.vector', icon: 'box', subtitle: 'nav.vector.subtitle' },
  { to: '/tools', label: 'nav.tools', icon: 'wrench', subtitle: 'nav.tools.subtitle' },
  { to: '/models', label: 'nav.models', icon: 'gauge', subtitle: 'nav.models.subtitle' },
  { section: 'nav.ops' },
  { to: '/logs', label: 'nav.logs', icon: 'scroll', subtitle: 'nav.logs.subtitle' },
  { to: '/monitor', label: 'nav.monitor', icon: 'activity', subtitle: 'nav.monitor.subtitle' },
  { to: '/cost', label: 'nav.cost', icon: 'dollar', subtitle: 'nav.cost.subtitle' },
  { section: 'nav.enterprise' },
  { to: '/audit', label: 'nav.audit', icon: 'clipboard', subtitle: 'nav.audit.subtitle' },
  { to: '/rbac', label: 'nav.rbac', icon: 'shield', subtitle: 'nav.rbac.subtitle' },
  { to: '/tenants', label: 'nav.tenants', icon: 'building', subtitle: 'nav.tenants.subtitle' },
  { to: '/users', label: 'nav.users', icon: 'user', subtitle: 'nav.users.subtitle' },
  { to: '/settings', label: 'nav.settings', icon: 'gear', subtitle: 'nav.settings.subtitle' },
];

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
