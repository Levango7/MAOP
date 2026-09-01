/**
 * Single source of truth for navigation + page header metadata.
 *
 * ── 2026-09-01 信息架构重设计（一号用户驱动，5 组任务流导航）─────────
 * 旧结构按后端模块罗列 26 个菜单（6 个 section），用户承担"发现负担"。
 * 新结构按用户任务流分 5 组，每组 = 一个 section 标题 + 2~4 个子项：
 *
 *   首页        看状态 + 任务历史
 *   执行        让 MAOP 做事（对话/结构化任务/Agent 委派）
 *   记忆        MAOP 认识你（三层记忆/搜索/知识图谱）
 *   能力        MAOP 能用谁（Agents/技能/市场/模型）
 *   运维        跑得怎样（监控/日志/链路/成本）
 *
 * 设置齿轮在 TopBar 右侧（不在侧边栏）；企业专属能力（RBAC/SSO/审计/
 * 租户/许可证/用户/配额/API密钥）保持原有独立路由，由 enterprise:true
 * 过滤隐藏（个人版所见即所得），企业版可见于"管理"组。
 *
 * 全部旧路由保留 301 重定向（router/index.js），深链不断。
 */

export const nav = [
  // ── 首页 ─────────────────────────────────────────────────────
  { section: 'nav.group.home' },
  { to: '/home', label: 'nav.overview', icon: 'overview', subtitle: 'nav.overview.subtitle' },
  { to: '/home/tasks', label: 'nav.tasks', icon: 'scroll', subtitle: 'nav.tasks.subtitle' },

  // ── 执行 ─────────────────────────────────────────────────────
  { section: 'nav.group.run' },
  { to: '/run', label: 'nav.run', icon: 'play', subtitle: 'nav.run.subtitle', matchPaths: ['/control', '/chat'] },
  { to: '/run/agents', label: 'nav.dispatch', icon: 'route', subtitle: 'nav.dispatch.subtitle', matchPaths: ['/agents'] },

  // ── 记忆 ─────────────────────────────────────────────────────
  { section: 'nav.group.memory' },
  { to: '/memory', label: 'nav.memory', icon: 'brain', subtitle: 'nav.memory.subtitle' },
  { to: '/memory/search', label: 'nav.search', icon: 'search', subtitle: 'nav.search.subtitle', matchPaths: ['/vector'] },
  { to: '/memory/graph', label: 'nav.knowledgeGraph', icon: 'network', subtitle: 'nav.knowledgeGraph.subtitle', matchPaths: ['/knowledge-graph'] },

  // ── 能力 ─────────────────────────────────────────────────────
  { section: 'nav.group.capability' },
  { to: '/capability/agents', label: 'nav.agents', icon: 'bot', subtitle: 'nav.agents.subtitle' },
  { to: '/capability/skills', label: 'nav.skills', icon: 'beaker', subtitle: 'nav.skills.subtitle', matchPaths: ['/tools', '/skill-editor'] },
  { to: '/capability/market', label: 'nav.skillMarket', icon: 'archive', subtitle: 'nav.skillMarket.subtitle', matchPaths: ['/skill-market'] },
  { to: '/capability/models', label: 'nav.models', icon: 'gauge', subtitle: 'nav.models.subtitle', matchPaths: ['/models'] },

  // ── 运维 ─────────────────────────────────────────────────────
  { section: 'nav.group.operate' },
  { to: '/operate', label: 'nav.monitor', icon: 'activity', subtitle: 'nav.monitor.subtitle' },
  { to: '/operate/logs', label: 'nav.logs', icon: 'scroll', subtitle: 'nav.logs.subtitle' },
  { to: '/operate/tracing', label: 'nav.observability', icon: 'zap', subtitle: 'nav.observability.subtitle', matchPaths: ['/observability'] },
  { to: '/operate/cost', label: 'nav.cost', icon: 'dollar', subtitle: 'nav.cost.subtitle' },

  // ── 管理（企业版；个人版整组隐藏）─────────────────────────────
  { section: 'nav.group.admin', enterprise: true },
  { to: '/users', label: 'nav.users', icon: 'user', subtitle: 'nav.users.subtitle', enterprise: true },
  { to: '/audit', label: 'nav.audit', icon: 'clipboard', subtitle: 'nav.audit.subtitle', enterprise: true },
  { to: '/rbac', label: 'nav.rbac', icon: 'shield', subtitle: 'nav.rbac.subtitle', enterprise: true },
  { to: '/tenants', label: 'nav.tenants', icon: 'building', subtitle: 'nav.tenants.subtitle', enterprise: true },
  { to: '/quotas', label: 'nav.quotas', icon: 'gauge', subtitle: 'nav.quotas.subtitle', enterprise: true },
  { to: '/apikeys', label: 'nav.apikeys', icon: 'link', subtitle: 'nav.apikeys.subtitle', enterprise: true },
  { to: '/sso', label: 'nav.sso', icon: 'plug', subtitle: 'nav.sso.subtitle', enterprise: true },
  { to: '/licenses', label: 'nav.licenses', icon: 'shield', subtitle: 'nav.licenses.subtitle', enterprise: true },

  // ── 设置（TopBar 齿轮直达；侧边栏保留入口方便直达）────────────
  { section: 'nav.group.system' },
  { to: '/settings', label: 'nav.settings', icon: 'gear', subtitle: 'nav.settings.subtitle' },
  { to: '/notifications', label: 'nav.notifications', icon: 'message-square', subtitle: 'nav.notifications.subtitle' },
  { to: '/docs', label: 'nav.docs', icon: 'book-open', subtitle: 'nav.docs.subtitle' },
];

/**
 * nav 过滤: 个人版隐藏 enterprise:true 的项（含整组 section）。
 * 新结构里企业项集中在"管理"组，个人版整组消失。
 */
export function filterNavByEdition(navList, edition) {
  if (edition === 'enterprise') return navList;
  const filtered = navList.filter((item) => !item.enterprise);
  const result = [];
  for (let i = 0; i < filtered.length; i++) {
    const item = filtered[i];
    if (item.section) {
      const hasVisibleChild = filtered.slice(i + 1).some((n) => !n.section);
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
 * Exact match first, then prefix. `/` (legacy overview) maps to /home via
 * router redirect, so we match both here for safety.
 */
export function getPageMeta(path) {
  if (!path) return null;
  if (path === '/' || path === '/home') return nav.find((n) => n.to === '/home') || null;
  const exact = nav.find((n) => n.to === path);
  if (exact) return exact;
  return nav.find((n) => n.to && n.to !== '/home' && path.startsWith(n.to)) || null;
}
