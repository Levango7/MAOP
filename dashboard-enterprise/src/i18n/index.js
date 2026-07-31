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
    'nav.core': 'Core',
    'nav.searchTools': 'Search & Tools',
    'nav.ops': 'Ops',
    'nav.enterprise': 'Enterprise',
    'nav.overview': 'Overview',
    'nav.control': 'Control',
    'nav.chat': 'Chat',
    'nav.agents': 'Agents',
    'nav.memory': 'Memory',
    'nav.evolve': 'Evolution',
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
    'nav.settings': 'Settings',
    // ── Page subtitles (one-line description under each page title) ──
    'nav.overview.subtitle': 'Platform-wide metrics and health at a glance',
    'nav.control.subtitle': 'Start, stop and maintain platform services',
    'nav.chat.subtitle': 'Converse with agents and inspect sessions',
    'nav.agents.subtitle': 'Discovered and registered agent CLIs',
    'nav.memory.subtitle': 'Three-layer memory: working, short-term, long-term',
    'nav.evolve.subtitle': 'Self-improvement, learning and change logs',
    'nav.search.subtitle': 'One-stop search across memory, vectors, graph, logs and agents',
    'nav.vector.subtitle': 'Vector index management: stats, browse and similarity debugging',
    'nav.tools.subtitle': 'Built-in, imported and custom skills',
    'nav.models.subtitle': 'Model registry, providers and routing',
    'nav.logs.subtitle': 'System and agent logs',
    'nav.monitor.subtitle': 'Live metrics, traces and SSE streams',
    'nav.cost.subtitle': 'Token usage and spend analytics',
    'nav.audit.subtitle': 'Security and compliance event trail',
    'nav.rbac.subtitle': 'Roles, permissions and access control',
    'nav.tenants.subtitle': 'Multi-tenant isolation and quotas',
    'nav.settings.subtitle': 'Appearance, edition and backend configuration',
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

    // ── Common reused words ─────────────────────────────────────
    'common.refresh': 'Refresh',
    'common.loading': 'Loading…',
    'common.retry': 'Retry',
    'common.live': 'Live',
    'common.offline': 'Offline',
    'common.actions': 'Actions',
    'common.status': 'Status',
    'common.model': 'Model',
    'common.driver': 'Driver',
    'common.caps': 'Caps',
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

    // ── TopBar / User module ─────────────────────────────────────
    'topbar.refreshTime': 'Last refresh',
    'topbar.systemName': 'MAOP',
    'topbar.systemNameEn': 'Multi-Agent Orchestration',
    'topbar.systemNameZh': '多智能体编排平台',
    'topbar.density': 'Density',
    'topbar.theme': 'Theme',
    'topbar.role.admin': 'Administrator',
    'topbar.role.superadmin': 'Super Admin',
    'topbar.role.operator': 'Operator',
    'topbar.role.viewer': 'Viewer',
    'topbar.role.guest': 'Guest',
    'nav.users': 'Users',
    'nav.users.subtitle': 'User account management',
    'users.title': 'User Management',
    'users.registerUser': 'Register User',
    'users.deregisterUser': 'Deregister User',
    'users.updateProfile': 'Update Profile',
    'users.username': 'Username',
    'users.password': 'Password',
    'users.roles': 'Roles',
    'users.role': 'Role',
    'users.created': 'Created',
    'users.lastLogin': 'Last login',
    'users.confirmDelete': 'Confirm deregister this user?',
    'users.noUsers': 'No users',
    'users.welcome': 'Welcome, {name}',
  },

  zh: {
    'nav.core': '核心',
    'nav.searchTools': '搜索与工具',
    'nav.ops': '运维',
    'nav.enterprise': '企业',
    'nav.overview': '概览',
    'nav.control': '控制台',
    'nav.chat': '对话',
    'nav.agents': '智能体',
    'nav.memory': '记忆',
    'nav.evolve': '演进',
    'nav.search': '搜索',
    'nav.vector': '向量',
    'nav.tools': '工具',
    'nav.models': '模型',
    'nav.logs': '日志',
    'nav.monitor': '监控',
    'nav.cost': '成本',
    'nav.audit': '审计',
    'nav.rbac': '权限',
    'nav.tenants': '租户',
    'nav.settings': '设置',
    // ── 页面副标题（页面标题下方的一行说明） ──
    'nav.overview.subtitle': '平台整体指标与健康状态一览',
    'nav.control.subtitle': '启停与维护平台服务',
    'nav.chat.subtitle': '与智能体对话并查看会话',
    'nav.agents.subtitle': '已发现与已注册的智能体 CLI',
    'nav.memory.subtitle': '三层记忆：工作 / 短期 / 长期',
    'nav.evolve.subtitle': '自我进化、学习与变更记录',
    'nav.search.subtitle': '统一检索：跨记忆、向量、图谱、日志与 Agent 的一站式查询',
    'nav.vector.subtitle': '向量索引管理：索引统计、浏览与相似度检索调试',
    'nav.tools.subtitle': '系统内置、已导入与自定义技能',
    'nav.models.subtitle': '模型注册表、供应商与路由',
    'nav.logs.subtitle': '系统与智能体日志',
    'nav.monitor.subtitle': '实时指标、链路与 SSE 流',
    'nav.cost.subtitle': 'Token 用量与费用分析',
    'nav.audit.subtitle': '安全与合规事件审计',
    'nav.rbac.subtitle': '角色、权限与访问控制',
    'nav.tenants.subtitle': '多租户隔离与配额',
    'nav.settings.subtitle': '外观、版本与后端配置',
    'nav.edition': '版本',
    'nav.editionLocked': '切换版本需要管理员权限',
    'nav.editionToEnterprise': '切换到企业版？将启用 SSO / RBAC / 审计等企业级功能。',
    'nav.editionToPersonal': '切换到个人版？将关闭 SSO / RBAC / 审计等企业级功能。',

    'status.live': '在线',
    'status.offline': '离线',
    'action.collapseSidebar': '收起侧栏',
    'action.expandSidebar': '展开侧栏',
    'action.toggleTheme': '切换主题',
    'action.logout': '退出登录',
    'auth.signIn': '登录',
    'auth.sessionExpired': '会话已过期',
    'auth.signingIn': '登录中…',
    'auth.username': '用户名',
    'auth.password': '密码',
    'auth.loginFailed': '登录失败',
    'auth.networkError': '网络错误',

    'footer.tagline': '多智能体编排平台',
    'footer.online': '在线',
    'footer.offline': '离线',
    'footer.copyright': '© 2026 MAOP · 保留所有权利',
    'footer.edition': '版本',

    'settings.appearance': '外观',
    'settings.appearanceSub': '即时作用于整个控制台',
    'settings.edition': '版本',
    'settings.backends': '后端',
    'settings.server': '服务器',
    'settings.rateLimit': '限流',
    'settings.featureFlags': '功能开关',
    'settings.dataPaths': '数据路径',
    'settings.about': '关于',
    'settings.theme': '主题',
    'settings.density': '密度',
    'settings.sidebar': '侧栏',
    'settings.language': '语言',
    'settings.light': '浅色',
    'settings.dark': '深色',
    'settings.comfortable': '舒适',
    'settings.compact': '紧凑',
    'settings.expanded': '展开',
    'settings.collapsed': '收起',
    'settings.zh': '中文',
    'settings.en': 'English',

    'common.refresh': '刷新',
    'common.loading': '加载中…',
    'common.retry': '重试',
    'common.live': '在线',
    'common.offline': '离线',
    'common.actions': '操作',
    'common.status': '状态',
    'common.model': '模型',
    'common.driver': '驱动',
    'common.caps': '能力',
    'common.latency': '延迟',
    'common.configuration': '配置',
    'common.capabilities': '能力',
    'common.close': '关闭',
    'common.noData': '暂无数据',
    'common.all': '全部',
    'common.grid': '网格',
    'common.table': '表格',
    'common.search': '搜索',
    'common.save': '保存',
    'common.cancel': '取消',
    'common.submit': '提交',
    'common.add': '添加',
    'common.edit': '编辑',
    'common.delete': '删除',
    'common.enable': '启用',
    'common.disable': '禁用',
    'error.somethingWrong': '页面渲染出错',
    'error.reload': '重新加载',
    'common.on': '开',
    'common.off': '关',
    'common.details': '详情',
    'common.empty': '空',
    'common.none': '无',
    'common.version': '版本',
    'common.uptime': '运行时长',
    'common.platform': '平台',
    'common.name': '名称',
    'common.type': '类型',
    'common.state': '状态',

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
      return val.replace(/\{(\w+)\}/g, (_, k) => (params[k] != null ? String(params[k]) : `{${k}}`));
    }
    return val;
  }

  return { t, locale: ui.locale, setLocale: ui.setLocale };
}
