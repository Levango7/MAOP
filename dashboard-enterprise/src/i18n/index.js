/**
 * Lightweight i18n — no external dependency, kept deliberately small so the
 * console stays dependency-free (same philosophy as the vendored AppIcon set).
 *
 * Dictionary layout:
 *  - `coreMessages` (below) holds the shell strings that are already migrated:
 *    nav, status, auth, footer, settings, plus a small set of `common.*` words
 *    (Refresh, Loading, Retry, Live, …) reused across many views.
 *  - Each view owns its remaining strings in `src/i18n/view-<name>.js`, which
 *    exports `{ en, zh }`. They are auto-collected via import.meta.glob so a
 *    view can add keys without touching this file (no edit contention, and the
 *    rollout stays incremental).
 *
 * `en` is the source-of-truth baseline. Every UI string wrapped with t() exists
 * in `en`; untranslated `zh` keys fall back to `en`, so the UI never shows raw
 * keys or breaks mid-migration. Locale lives in the shared ui store (persisted
 * to localStorage + mirrored to <html data-lang>), so toggling language
 * re-renders every t() call reactively.
 */

import { useUiStore } from '../stores/ui.js';

export const coreMessages = {
  en: {
    // ── Sidebar navigation ──────────────────────────────────────
    // 2026-08-12 (RFC-001 迭代 A): 6-group journey-based IA
    'nav.workbench': 'Workbench',
    'nav.build': 'Build',
    'nav.assets': 'Data Assets',
    'nav.observe': 'Observe',
    'nav.govern': 'Govern',
    'nav.system': 'System',
    'nav.run': 'Run',
    // ── 2026-09-01 IA redesign: 5 task-flow groups ──
    'nav.group.home': 'Home',
    'nav.group.run': 'Run',
    'nav.group.memory': 'Memory',
    'nav.group.capability': 'Capability',
    'nav.group.operate': 'Operate',
    'nav.group.admin': 'Administration',
    'nav.group.system': 'System',
    'nav.dispatch': 'Agent Dispatch',
    'nav.dispatch.subtitle': 'Dispatch a task to a specific CLI agent',
    'nav.skills': 'Skills & Tools',
    'nav.skills.subtitle': 'Installed tools and skill composer',
    // Legacy keys kept — old deep links still resolve via router redirects
    'nav.core': 'Core',
    'nav.searchTools': 'Search & Tools',
    'nav.ops': 'Ops',
    'nav.enterprise': 'Enterprise',
    'nav.overview': 'Overview',
    'nav.control': 'Control',
    'nav.chat': 'Chat',
    'nav.agents': 'Agents',
    // P1-3: 任务历史页导航标签
    'nav.tasks': 'Tasks',
    'nav.tasks.subtitle': 'Task history: search, filter, rerun',
    'nav.memory': 'Memory',
    'nav.evolve': 'Auto-Tuning',
    'nav.evolutionHistory': 'Tuning History',
    // v5.1.0: Workflow orchestration + Skill composer/market
    'nav.workflowEditor': 'Workflow Editor',
    'nav.skillEditor': 'Skill Editor',
    'nav.skillMarket': 'Skill Market',
    'nav.search': 'Search',
    'nav.vector': 'Vector',
    'nav.tools': 'Tools',
    'nav.models': 'Models',
    'nav.logs': 'Logs',
    'nav.monitor': 'Monitor',
    'nav.cost': 'Cost',
    'nav.audit': 'Audit',
    'nav.rbac': 'RBAC',
    'nav.tenants': 'Tenants',
    // v4.6.0 企业版新功能导航标签
    'nav.licenses': 'Licenses',
    'nav.sso': 'SSO',
    'nav.quotas': 'Quotas',
    'nav.apikeys': 'API Keys',
    'nav.notifications': 'Notifications',
    'nav.settings': 'Settings',
    // ── Page subtitles (one-line description under each page title) ──
    'nav.overview.subtitle': 'Platform-wide metrics and health at a glance',
    'nav.run.subtitle': 'Run tasks and converse with agents',
    'view.run.tabStructured': 'Structured',
    'view.run.tabChat': 'Chat',
    'nav.control.subtitle': 'Start, stop and maintain platform services',
    'nav.chat.subtitle': 'Converse with agents and inspect sessions',
    'nav.agents.subtitle': 'Discovered and registered agent CLIs',
    'nav.memory.subtitle': 'Three-layer memory: working, short-term, long-term',
    'nav.evolve.subtitle': 'Rule-driven auto-tuning and change logs',
    'nav.evolutionHistory.subtitle': 'Performance-driven rule-based auto-tuning closed loop',
    'nav.workflowEditor.subtitle': 'Compose agent workflows as a draggable DAG',
    'nav.skillEditor.subtitle': 'Compose atomic skills into reusable multi-step workflows',
    'nav.skillMarket.subtitle': 'Browse, import and publish reusable skills',
    'nav.search.subtitle': 'One-stop search across memory, vectors, graph, logs and agents',
    'nav.vector.subtitle': 'Vector index management: stats, browse and similarity debugging',
    'nav.tools.subtitle': 'Built-in, imported and custom skills',
    'nav.models.subtitle': 'Model registry, providers and routing',
    'nav.logs.subtitle': 'System and agent logs',
    'nav.monitor.subtitle': 'Live metrics, traces and SSE streams',
    'nav.observability': 'Observability',
    'nav.observability.subtitle': 'OTel tracing, Prometheus metrics and pipeline health',
    'nav.cost.subtitle': 'Token usage and spend analytics',
    'nav.audit.subtitle': 'Security and compliance event trail',
    'nav.rbac.subtitle': 'Roles, permissions and access control',
    'nav.tenants.subtitle': 'Multi-tenant isolation and quotas',
    // v4.6.0 企业版新功能页面副标题
    'nav.licenses.subtitle': 'License issuance, activation and compliance',
    'nav.sso.subtitle': 'Single sign-on providers and federation',
    'nav.quotas.subtitle': 'Resource quotas and usage limits',
    'nav.apikeys.subtitle': 'API key creation, rotation and access scoping',
    'nav.notifications.subtitle': 'Platform alerts and notification center',
    'nav.settings.subtitle': 'Appearance, edition and backend configuration',
    'nav.help': 'Help',
    'nav.docs': 'Documentation',
    'nav.docs.subtitle': 'Guides, API reference and architecture decisions',
    'nav.edition': 'Edition',
    'nav.editionLocked': 'Admin permission required to switch edition',
    'nav.editionToEnterprise': 'Switch to Enterprise edition? This enables SSO / RBAC / Audit and other enterprise features.',
    'nav.editionToPersonal': 'Switch to Personal edition? Enterprise features (SSO / RBAC / Audit) will be disabled.',

    // ── Status / actions (sidebar footer, auth overlay) ──────────
    'status.live': 'Live',
    'status.offline': 'Offline',
    'action.collapseSidebar': 'Collapse sidebar',
    'action.expandSidebar': 'Expand sidebar',
    'action.toggleTheme': 'Switch theme',
    'action.logout': 'Sign out',
    'auth.signIn': 'Sign In',
    'auth.sessionExpired': 'Session Expired',
    'auth.signingIn': 'Signing in…',
    'auth.username': 'Username',
    'auth.password': 'Password',
    'auth.loginFailed': 'Login failed',
    'auth.networkError': 'Network error',

    // ── Footer ──────────────────────────────────────────────────
    'footer.tagline': 'Multi-Agent Orchestration Platform',
    'footer.online': 'Online',
    'footer.offline': 'Offline',
    'footer.copyright': '© 2026 MAOP · All rights reserved',
    'footer.edition': 'Edition',

    // ── Settings ────────────────────────────────────────────────
    'settings.appearance': 'Appearance',
    'settings.appearanceSub': 'Applies instantly across the whole console',
    'settings.edition': 'Edition',
    'settings.backends': 'Backends',
    'settings.server': 'Server',
    'settings.rateLimit': 'Rate Limiting',
    'settings.featureFlags': 'Feature Flags',
    'settings.dataPaths': 'Data Paths',
    'settings.about': 'About',
    'settings.theme': 'Theme',
    'settings.density': 'Density',
    'settings.sidebar': 'Sidebar',
    'settings.language': 'Language',
    'settings.light': 'Light',
    'settings.dark': 'Dark',
    'settings.comfortable': 'Comfort',
    'settings.compact': 'Compact',
    'settings.expanded': 'Expanded',
    'settings.collapsed': 'Collapsed',
    'settings.zh': '中文',
    'settings.en': 'English',

    // ── Docs view ────────────────────────────────────────────────
    'view.docs.gettingStarted': 'Getting Started',
    'view.docs.guides': 'Guides',
    'view.docs.enterprise': 'Enterprise',
    'view.docs.integrations': 'Integrations',

    // ── Common reused words ─────────────────────────────────────
    'common.refresh': 'Refresh',
    'common.loading': 'Loading…',
    'common.retry': 'Retry',
    'common.live': 'Live',
    'common.offline': 'Offline',
    // Coach marks (iteration B2)
    'coach.actions.title': 'Quick actions',
    'coach.actions.body': 'These four tiles cover the most common starting points: run a task, start a chat, browse agents, or view logs.',
    'coach.nav.title': 'Navigation',
    'coach.nav.body': 'The sidebar groups pages by workflow — build, data, observe, govern. Collapse it anytime with the top-left button.',
    'coach.topbar.title': 'Live status',
    'coach.topbar.body': 'Edition, connection state and the refresh/monitoring controls live here, always within reach.',
    'coach.evolve.title': 'Rule-driven auto-tuning',
    'coach.evolve.body': 'Switch between the tuning console and its history timeline — MAOP tunes its own agents via rule-based optimization, and you can watch it happen.',
    'action.skip': 'Skip',
    'action.next': 'Next',
    'action.done': 'Done',
    'action.close': 'Close',
    'palette.placeholder': 'Type a command or page name…',
    'palette.noResults': 'No matches — try a page name or command',
    'common.actions': 'Actions',
    'common.status': 'Status',
    'common.model': 'Model',
    'common.driver': 'Driver',
    'common.caps': 'Capabilities',
    'common.latency': 'Latency',
    'common.configuration': 'Configuration',
    'common.capabilities': 'Capabilities',
    'common.close': 'Close',
    'common.noData': 'No data',
    'common.all': 'All',
    'common.grid': 'Grid',
    'common.table': 'Table',
    'common.search': 'Search',
    'common.save': 'Save',
    'common.cancel': 'Cancel',
    'common.confirm': 'Confirm',
    'common.submit': 'Submit',
    'common.add': 'Add',
    'common.edit': 'Edit',
    'error.somethingWrong': 'Page failed to render',
    'error.reload': 'Reload',
    'common.delete': 'Delete',
    'common.enable': 'Enable',
    'common.disable': 'Disable',
    'common.on': 'On',
    'common.off': 'Off',
    'common.details': 'Details',
    'common.empty': 'Empty',
    'common.none': 'None',
    'common.version': 'Version',
    'common.uptime': 'Uptime',
    'common.platform': 'Platform',
    'common.name': 'Name',
    'common.type': 'Type',
    'common.state': 'State',
    'common.provider': 'Provider',
    'common.disabled': 'Disabled',
    'common.required': 'Required',
    // ── Relative time (P2-3: replace hardcoded English) ──────────
    'common.justNow': 'just now',
    'common.secondsAgo': '{n}s ago',
    'common.minutesAgo': '{n}m ago',
    'common.hoursAgo': '{n}h ago',
    'common.daysAgo': '{n}d ago',

    // ── 顶栏 / 用户模块 ────────────────────────────────────────
    'topbar.refreshTime': '最后刷新',
    'topbar.systemName': 'MAOP',
    'topbar.systemNameEn': 'Multi-Agent Orchestration',
    'topbar.systemNameZh': '多智能体编排平台',
    'topbar.density': '布局',
    'topbar.theme': '主题',
    'topbar.role.admin': '管理员',
    'topbar.role.superadmin': '超级管理员',
    'topbar.role.operator': '操作员',
    'topbar.role.viewer': '访客',
    'topbar.role.guest': '游客',
    'nav.users': '用户',
    'nav.users.subtitle': '用户账户管理',
    'users.title': '用户管理',
    'users.registerUser': '注册用户',
    'users.deregisterUser': '注销用户',
    'users.updateProfile': '更新信息',
    'users.username': '用户名',
    'users.password': '密码',
    'users.roles': '角色',
    'users.role': '角色',
    'users.created': '创建时间',
    'users.lastLogin': '最后登录',
    'users.confirmDelete': '确认注销该用户？',
    'users.noUsers': '暂无用户',
    'users.welcome': '欢迎，{name}',

    // ── 可访问性标签 (a11y) ───────────────────────────────────────
    'a11y.toggleNavigation': '切换导航菜单',
    'a11y.mainNavigation': '主导航',
    'a11y.footerNavigation': '页脚导航',
    'a11y.loginDialog': '登录对话框',
    'a11y.searchCommands': '搜索命令',
    'a11y.commandPalette': '命令面板',
    'a11y.userProfile': '用户资料',
    'a11y.densityControl': '布局密度控件',
    'a11y.themeControl': '主题控件',
    'a11y.guideStep': '引导第 {n} 步',
    'a11y.send': '发送',
    'a11y.stop': '停止',
    'a11y.attachImage': '附加图片',
    'a11y.newSession': '新建会话',
    'a11y.deleteSession': '删除会话',
    'a11y.refresh': '刷新',
    'a11y.openCommandPalette': '打开命令面板 (Ctrl+K)',
    'a11y.sortBy': '按 {column} 排序',
    'a11y.yes': '是',
    'a11y.no': '否',
    'a11y.search': '搜索',
  },
  zh: {
    // ── 相对时间 (P2-3: 替换硬编码英文) ──────────
    'common.justNow': '刚刚',
    'common.secondsAgo': '{n}秒前',
    'common.minutesAgo': '{n}分钟前',
    'common.hoursAgo': '{n}小时前',
    'common.daysAgo': '{n}天前',
    // ── 确认对话框 ──────────
    'common.confirm': '确认',
    'common.cancel': '取消',
  },
};

// Auto-collect every view-level dictionary: src/i18n/view-*.js
const viewModules = import.meta.glob('./view-*.js', { eager: true });
const viewMessages = { en: {}, zh: {} };
for (const mod of Object.values(viewModules)) {
  const m = mod.messages || (mod.default && mod.default.messages);
  if (m && m.en) Object.assign(viewMessages.en, m.en);
  if (m && m.zh) Object.assign(viewMessages.zh, m.zh);
}

export const messages = {
  en: { ...coreMessages.en, ...viewMessages.en },
  zh: { ...coreMessages.zh, ...viewMessages.zh },
};

/**
 * useI18n — returns a reactive `t()` plus the current locale and setter.
 * `t(key)` returns zh when available, else en, else the key itself, so the
 * UI degrades gracefully during a phased translation rollout.
 */
export function useI18n() {
  const ui = useUiStore();

  function t(key, params) {
    const dict = messages[ui.locale] || messages.en;
    let val = dict[key];
    if (val === null || val === undefined) val = messages.en[key];
    if (val === null || val === undefined) return key;
    if (params && typeof val === 'string') {
      return val.replace(/\{(\w+)\}/g, (_, k) => (params[k] !== null && params[k] !== undefined ? String(params[k]) : `{${k}}`));
    }
    return val;
  }

  return { t, locale: ui.locale, setLocale: ui.setLocale };
}
