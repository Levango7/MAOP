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
  // L11 fix: en 与 zh 的键顺序可能不一致（zh 按 F34 补全时重新分组排序）。
  // 这不影响功能——t() 按 key 查找而非按顺序索引，键顺序仅影响可读性。
  // 如需对齐顺序，应作为独立重构任务处理，避免与翻译补全混在一起增加 review 负担。
  en: {
    // ── Sidebar navigation ──────────────────────────────────────
    // 2026-08-12 (RFC-001 迭代 A): 6-group journey-based IA
    'nav.run': 'Run',
    // ── 2026-09-01 IA redesign: 5 task-flow groups ──
    'nav.group.home': 'Home',
    'nav.group.run': 'Run',
    'nav.group.memory': 'Memory',
    'nav.group.capability': 'Capability',
    'nav.group.operate': 'Operate',
    'nav.group.admin': 'Administration',
    'nav.group.system': 'System',
    // ── IA 扩展：新增分析与反馈组 ──
    'nav.group.analysis': 'Analysis',
    'nav.group.feedback': 'Feedback',
    'nav.dispatch': 'Agent Dispatch',
    'nav.dispatch.subtitle': 'Dispatch a task to a specific CLI agent',
    'nav.skills': 'Skills & Tools',
    'nav.skills.subtitle': 'Installed tools and skill composer',
    // Legacy keys kept — old deep links still resolve via router redirects
    'nav.overview': 'Overview',
    'nav.agents': 'Agents',
    // P1-3: 任务历史页导航标签
    'nav.tasks': 'Tasks',
    'nav.tasks.subtitle': 'Task history: search, filter, rerun',
    'nav.memory': 'Memory',
    'nav.evolve': 'Auto-Tuning',
    // v5.1.0: Workflow orchestration + Skill composer/market
    'nav.skillMarket': 'Skill Market',
    'nav.search': 'Search',
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
    'nav.agents.subtitle': 'Discovered and registered agent CLIs',
    'nav.memory.subtitle': 'Three-layer memory: working, short-term, long-term',
    'nav.evolve.subtitle': 'Rule-driven auto-tuning and change logs',
    'nav.skillMarket.subtitle': 'Browse, import and publish reusable skills',
    'nav.search.subtitle': 'One-stop search across memory, vectors, graph, logs and agents',
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
    'nav.docs': 'Documentation',
    'nav.docs.subtitle': 'Guides, API reference and architecture decisions',
    // ── IA 扩展：13 新页面 + workflow/evolve 归组 + analysis/feedback ──
    'nav.workflow': 'Workflow',
    'nav.workflow.subtitle': 'Compose agent workflows as a draggable DAG',
    'nav.mcp': 'MCP Tools',
    'nav.mcp.subtitle': 'Model Context Protocol tool registry and sandboxing',
    'nav.plugins': 'Plugins',
    'nav.plugins.subtitle': 'Install, configure and manage runtime plugins',
    'nav.subagents': 'Subagents',
    'nav.subagents.subtitle': 'Child agent lifecycle and delegation scope',
    'nav.routing': 'Routing Rules',
    'nav.routing.subtitle': 'Agent and request routing policies',
    'nav.scheduling': 'Scheduling',
    'nav.scheduling.subtitle': 'Task scheduling and concurrency policies',
    'nav.protocols': 'Protocols',
    'nav.protocols.subtitle': 'Communication protocols and transport config',
    'nav.worktrees': 'Worktrees',
    'nav.worktrees.subtitle': 'Git worktree isolation for parallel agent runs',
    'nav.agentProxy': 'Agent Proxy',
    'nav.agentProxy.subtitle': 'Reverse proxy and multiplexing for agent backends',
    'nav.debate': 'Debate',
    'nav.debate.subtitle': 'Multi-agent discussion and consensus',
    'nav.blackboard': 'Blackboard',
    'nav.blackboard.subtitle': 'Shared blackboard for agent collaboration',
    'nav.alertRules': 'Alert Rules',
    'nav.alertRules.subtitle': 'Threshold and anomaly alert rule editor',
    'nav.webhooks': 'Webhooks',
    'nav.webhooks.subtitle': 'Outbound webhook endpoints and delivery logs',
    'nav.integrations': 'Integrations',
    'nav.integrations.subtitle': 'N8N and external system integrations',
    'nav.analysis': 'Analysis',
    'nav.analysis.subtitle': 'Data analysis dashboards and insights',
    'nav.feedback': 'Feedback',
    'nav.feedback.subtitle': 'User feedback collection and evaluation',
    'nav.editionLocked': 'Admin permission required to switch edition',

    // ── Status / actions (sidebar footer, auth overlay) ──────────
    'status.live': 'Live',
    'status.offline': 'Offline',
    'action.collapseSidebar': 'Collapse sidebar',
    'action.expandSidebar': 'Expand sidebar',
    'action.logout': 'Sign out',
    'auth.signIn': 'Sign In',
    'auth.sessionExpired': 'Session Expired',
    'auth.signingIn': 'Signing in…',
    'auth.username': 'Username',
    'auth.password': 'Password',
    'auth.loginFailed': 'Login failed',
    'auth.networkError': 'Network error',
    // P1-9 fix: edition store switchError 使用 i18n key 替代硬编码英文。
    'auth.authenticationRequired': 'Authentication required',
    // P1-3 fix: edition store 401 Unauthorized 使用 i18n key 替代硬编码英文。
    'auth.unauthorized': 'Unauthorized',
    'edition.refreshFailed': 'Failed to refresh edition info after switch',
    // P2-12 fix: edition store switchFailed 使用 i18n key 替代硬编码英文。
    'edition.switchFailed': 'Switch failed: HTTP {status}',

    // ── Footer ──────────────────────────────────────────────────
    'footer.online': 'Online',
    'footer.offline': 'Offline',
    'footer.copyright': '© 2026 MAOP · All rights reserved',

    // ── Settings ────────────────────────────────────────────────
    'settings.appearance': 'Appearance',
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
    'common.me': 'me',
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
    // P2-11 fix: 删除重复键 common.caps，统一使用 common.capabilities。
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
    'common.version': 'Version',
    'common.uptime': 'Uptime',
    'common.platform': 'Platform',
    'common.name': 'Name',
    'common.type': 'Type',
    'common.provider': 'Provider',
    'common.disabled': 'Disabled',
    'common.required': 'Required',
    'common.id': 'ID',
    'common.default': 'default',
    'common.severityMedium': 'MEDIUM',
    // ── Relative time (P2-3: replace hardcoded English) ──────────
    'common.justNow': 'just now',
    'common.secondsAgo': '{n}s ago',
    'common.minutesAgo': '{n}m ago',
    'common.hoursAgo': '{n}h ago',
    'common.daysAgo': '{n}d ago',

    // ── 顶栏 / 用户模块 ────────────────────────────────────────
    'topbar.systemName': 'MAOP',
    'topbar.systemNameEn': 'Multi-Agent Orchestration',
    'topbar.systemNameZh': 'Multi-Agent Orchestration Platform',
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
    'users.created': 'Created',
    'users.lastLogin': 'Last Login',
    'users.confirmDelete': 'Confirm deregistration of this user?',
    'users.noUsers': 'No users',

    // ── 可访问性标签 (a11y) ───────────────────────────────────────
    'a11y.toggleNavigation': 'Toggle navigation menu',
    'a11y.mainNavigation': 'Main navigation',
    'a11y.footerNavigation': 'Footer navigation',
    'a11y.loginDialog': 'Login dialog',
    'a11y.searchCommands': 'Search commands',
    'a11y.commandPalette': 'Command palette',
    'a11y.userProfile': 'User profile',
    'a11y.guideStep': 'Guide step {n}',
    'a11y.attachImage': 'Attach image',
    'a11y.deleteSession': 'Delete session',
    'a11y.sortBy': 'Sort by {column}',
    'a11y.yes': 'Yes',
    'a11y.no': 'No',
    'a11y.search': 'Search',
    'a11y.closeNotification': 'Close notification',
    'a11y.loading': 'Loading',

    // ── Observability view (P3: 替换 Observability.vue 中硬编码英文字符串) ──
    'view.observability.enterprise': 'Enterprise',
    'view.observability.personal': 'Personal',
    'view.observability.tracingOn': 'Tracing ON',
    'view.observability.tracingOff': 'Tracing OFF',
    'view.observability.pipelineTitle': 'Observability Pipeline',
    'view.observability.configTitle': 'Configuration',
    'view.observability.canonicalMetricsTitle': 'Canonical Metrics (F1-04)',
    'view.observability.healthChecksTitle': 'Pipeline Health Checks',
    'view.observability.distributedTracingTitle': 'Distributed Tracing',
    'view.observability.statusOk': 'OK',
    'view.observability.statusOff': 'OFF',
    'view.observability.noHealthData': 'No health data',
    'view.observability.noHealthHint': 'Click refresh to run pipeline checks.',
    'view.observability.tracingActive': 'Tracing active — spans exported via OTLP to the Collector.',
    'view.observability.tracingDisabled': 'Tracing disabled ({hint})',
    'view.observability.requestsTotal': 'Requests Total',
    'view.observability.errorsTotal': 'Errors Total',
    'view.observability.activeSpans': 'Active Spans',
    'view.observability.traceExports': 'Trace Exports',
    'view.observability.structuredLogging': 'Structured Logging',
    'view.observability.traceCorrelation': 'Trace Correlation',
    'view.observability.otelTracing': 'OTel Tracing',
    'view.observability.prometheusMetrics': 'Prometheus Metrics',
    'view.observability.enterpriseMode': 'Enterprise Mode',
    'view.observability.otelLinked': 'OTel linked',
    'view.observability.noOtel': 'no OTel',
    'view.observability.configEdition': 'Edition',
    'view.observability.configOtelEnabled': 'OTel Enabled',
    'view.observability.configOtelExporter': 'OTel Exporter',
    'view.observability.configOtelEndpoint': 'OTel Endpoint',
    'view.observability.configServiceName': 'Service Name',
    'view.observability.configScrapePath': 'Scrape Path',
    'view.observability.configGrafanaUid': 'Grafana UID',
    'view.observability.yes': 'yes',
    'view.observability.no': 'no',
    'view.observability.available': 'available',
    'view.observability.missing': 'missing',
    'view.observability.endpointUnavailable': 'endpoint unavailable',
    // ── DagGraph 组件 (R4审查补充) ──────────────────────────
    'dag.emptyHint': 'Enter an execution ID to subscribe to DAG progress.',
    'dag.connecting': 'Connecting to execution {id}…',
    'dag.connected': 'Connected',
    'dag.disconnected': 'Disconnected',
  },
  zh: {
    // ── F34 修复: core 键中文翻译补全 (nav/status/action/footer/settings/common/coach/palette/error) ──

    // ── 侧边栏导航 ──────────────────────────────────────
    'nav.run': '运行',
    'nav.group.home': '首页',
    'nav.group.run': '运行',
    'nav.group.memory': '记忆',
    'nav.group.capability': '能力',
    'nav.group.operate': '运维',
    'nav.group.admin': '管理',
    'nav.group.system': '系统',
    // ── IA 扩展：新增分析与反馈组 ──
    'nav.group.analysis': '分析',
    'nav.group.feedback': '反馈',
    'nav.dispatch': '智能体调度',
    'nav.dispatch.subtitle': '向指定 CLI 智能体分发任务',
    'nav.skills': '技能与工具',
    'nav.skills.subtitle': '已安装工具与技能编排器',
    'nav.overview': '概览',
    'nav.agents': '智能体',
    'nav.tasks': '任务',
    'nav.tasks.subtitle': '任务历史：搜索、筛选、重跑',
    'nav.memory': '记忆',
    'nav.evolve': '自动调优',
    'nav.skillMarket': '技能市场',
    'nav.search': '搜索',
    'nav.models': '模型',
    'nav.logs': '日志',
    'nav.monitor': '监控',
    'nav.cost': '成本',
    'nav.audit': '审计',
    'nav.rbac': 'RBAC',
    'nav.tenants': '租户',
    'nav.licenses': '许可证',
    'nav.sso': 'SSO',
    'nav.quotas': '配额',
    'nav.apikeys': 'API 密钥',
    'nav.notifications': '通知',
    'nav.settings': '设置',
    // ── 页面副标题 ──
    'nav.overview.subtitle': '平台全局指标与健康状态一览',
    'nav.run.subtitle': '运行任务并与智能体对话',
    'view.run.tabStructured': '结构化',
    'view.run.tabChat': '对话',
    'nav.agents.subtitle': '已发现和注册的智能体 CLI',
    'nav.memory.subtitle': '三层记忆：工作、短期、长期',
    'nav.evolve.subtitle': '规则驱动的自动调优与变更日志',
    'nav.skillMarket.subtitle': '浏览、导入和发布可复用技能',
    'nav.search.subtitle': '跨记忆、向量、图、日志和智能体的一站式搜索',
    'nav.models.subtitle': '模型注册表、提供商和路由',
    'nav.logs.subtitle': '系统和智能体日志',
    'nav.monitor.subtitle': '实时指标、追踪和 SSE 流',
    'nav.observability': '可观测性',
    'nav.observability.subtitle': 'OTel 链路追踪、Prometheus 指标和管道健康',
    'nav.cost.subtitle': 'Token 用量与消费分析',
    'nav.audit.subtitle': '安全与合规事件追踪',
    'nav.rbac.subtitle': '角色、权限和访问控制',
    'nav.tenants.subtitle': '多租户隔离与配额',
    'nav.licenses.subtitle': '许可证发放、激活与合规',
    'nav.sso.subtitle': '单点登录提供商与联邦',
    'nav.quotas.subtitle': '资源配额与使用限制',
    'nav.apikeys.subtitle': 'API 密钥创建、轮换和访问范围',
    'nav.notifications.subtitle': '平台告警与通知中心',
    'nav.settings.subtitle': '外观、版本和后端配置',
    'nav.docs': '文档',
    'nav.docs.subtitle': '指南、API 参考和架构决策',
    // ── IA 扩展：13 新页面 + workflow/evolve 归组 + analysis/feedback ──
    'nav.workflow': '工作流',
    'nav.workflow.subtitle': '可拖拽 DAG 编排智能体工作流',
    'nav.mcp': 'MCP 工具',
    'nav.mcp.subtitle': '模型上下文协议工具注册与沙箱管理',
    'nav.plugins': '插件',
    'nav.plugins.subtitle': '安装、配置和管理运行时插件',
    'nav.subagents': '子代理',
    'nav.subagents.subtitle': '子代理生命周期与委派范围',
    'nav.routing': '路由策略',
    'nav.routing.subtitle': '智能体与请求路由规则',
    'nav.scheduling': '调度策略',
    'nav.scheduling.subtitle': '任务调度与并发策略',
    'nav.protocols': '协议管理',
    'nav.protocols.subtitle': '通信协议与传输配置',
    'nav.worktrees': '工作树',
    'nav.worktrees.subtitle': 'Git 工作树隔离，支持并行智能体运行',
    'nav.agentProxy': '代理代理',
    'nav.agentProxy.subtitle': '智能体后端反向代理与多路复用',
    'nav.debate': '辩论',
    'nav.debate.subtitle': '多智能体讨论与共识达成',
    'nav.blackboard': '黑板',
    'nav.blackboard.subtitle': '智能体协作共享黑板',
    'nav.alertRules': '告警规则',
    'nav.alertRules.subtitle': '阈值与异常告警规则编辑器',
    'nav.webhooks': 'Webhook',
    'nav.webhooks.subtitle': '出站 Webhook 端点与投递日志',
    'nav.integrations': '集成',
    'nav.integrations.subtitle': 'N8N 与外部系统集成',
    'nav.analysis': '分析',
    'nav.analysis.subtitle': '数据分析仪表盘与洞察',
    'nav.feedback': '反馈',
    'nav.feedback.subtitle': '用户反馈收集与评价',
    'nav.editionLocked': '切换版本需要管理员权限',

    // ── 状态 / 操作 ──────────────────────────────────────
    'status.live': '在线',
    'status.offline': '离线',
    'action.collapseSidebar': '收起侧边栏',
    'action.expandSidebar': '展开侧边栏',
    'action.logout': '退出登录',
    'action.skip': '跳过',
    'action.next': '下一步',
    'action.done': '完成',
    'action.close': '关闭',

    // ── 认证 ──────────────────────────────────────────────
    'auth.signIn': '登录',
    'auth.sessionExpired': '会话已过期',
    'auth.signingIn': '登录中…',
    'auth.username': '用户名',
    'auth.password': '密码',
    'auth.networkError': '网络错误',

    // ── 页脚 ──────────────────────────────────────────────
    'footer.online': '在线',
    'footer.offline': '离线',
    'footer.copyright': '© 2026 MAOP · 保留所有权利',

    // ── 设置 ──────────────────────────────────────────────
    'settings.appearance': '外观',
    'settings.edition': '版本',
    'settings.backends': '后端',
    'settings.server': '服务器',
    'settings.rateLimit': '速率限制',
    'settings.featureFlags': '功能开关',
    'settings.dataPaths': '数据路径',
    'settings.about': '关于',
    'settings.theme': '主题',
    'settings.density': '密度',
    'settings.sidebar': '侧边栏',
    'settings.language': '语言',
    'settings.light': '浅色',
    'settings.dark': '深色',
    'settings.comfortable': '舒适',
    'settings.compact': '紧凑',
    'settings.expanded': '展开',
    'settings.collapsed': '收起',
    'settings.zh': '中文',
    'settings.en': 'English',

    // ── 文档视图 ──────────────────────────────────────────
    'view.docs.gettingStarted': '快速开始',
    'view.docs.guides': '指南',
    'view.docs.enterprise': '企业版',
    'view.docs.integrations': '集成',

    // ── 通用词汇 ──────────────────────────────────────────
    'common.refresh': '刷新',
    'common.loading': '加载中…',
    'common.retry': '重试',
    'common.live': '在线',
    'common.offline': '离线',
    'common.me': '我',
    'common.actions': '操作',
    'common.status': '状态',
    'common.model': '模型',
    'common.driver': '驱动',
    // P2-11 fix: 删除重复键 common.caps，统一使用 common.capabilities。
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
    'common.submit': '提交',
    'common.edit': '编辑',
    'common.delete': '删除',
    'common.enable': '启用',
    'common.disable': '禁用',
    'common.on': '开',
    'common.off': '关',
    'common.details': '详情',
    'common.empty': '空',
    'common.version': '版本',
    'common.uptime': '运行时间',
    'common.platform': '平台',
    'common.name': '名称',
    'common.type': '类型',
    'common.provider': '提供商',
    'common.disabled': '已禁用',
    'common.required': '必填',
    'common.id': 'ID',
    'common.default': '默认',
    'common.severityMedium': '中',

    // ── 引导标记 ──────────────────────────────────────────
    'coach.actions.title': '快捷操作',
    'coach.actions.body': '这四个卡片覆盖了最常见的起点：运行任务、开始对话、浏览智能体或查看日志。',
    'coach.nav.title': '导航',
    'coach.nav.body': '侧边栏按工作流对页面分组 — 构建、数据、观测、治理。可随时用左上角按钮收起。',
    'coach.topbar.title': '实时状态',
    'coach.topbar.body': '版本、连接状态和刷新/监控控件都在这里，始终触手可及。',
    'coach.evolve.title': '规则驱动的自动调优',
    'coach.evolve.body': '在调优控制台与其历史时间线之间切换 — MAOP 通过基于规则的优化自调优智能体，你可以实时观看。',

    // ── 命令面板 ──────────────────────────────────────────
    'palette.placeholder': '输入命令或页面名称…',
    'palette.noResults': '无匹配 — 尝试页面名称或命令',

    // ── 错误 ──────────────────────────────────────────────
    'error.somethingWrong': '页面渲染失败',
    'error.reload': '重新加载',

    // ── 相对时间 (P2-3: 替换硬编码英文) ──────────
    'common.justNow': '刚刚',
    'common.secondsAgo': '{n}秒前',
    'common.minutesAgo': '{n}分钟前',
    'common.hoursAgo': '{n}小时前',
    'common.daysAgo': '{n}天前',
    // ── 确认对话框 ──────────
    'common.confirm': '确认',
    'common.cancel': '取消',

    // ── 认证 (P3: 添加 auth.loginFailed 中文翻译, 用于 App.vue:242) ──
    'auth.loginFailed': '登录失败',
    // P1-9 fix: edition store switchError 使用 i18n key 替代硬编码英文。
    'auth.authenticationRequired': '需要认证',
    // P1-3 fix: edition store 401 Unauthorized 使用 i18n key 替代硬编码英文。
    'auth.unauthorized': '未授权',
    'edition.refreshFailed': '切换后刷新版本信息失败',
    // P2-12 fix: edition store switchFailed 使用 i18n key 替代硬编码英文。
    'edition.switchFailed': '切换失败: HTTP {status}',

    // ── F33 修复: 顶栏 / 用户模块中文翻译 ──
    // 品牌名保留英文（与 en 字典一致），英文副标题保留原文
    'topbar.systemName': 'MAOP',
    'topbar.systemNameEn': 'Multi-Agent Orchestration',
    'topbar.systemNameZh': '多智能体编排平台',
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
    'users.created': '创建时间',
    'users.lastLogin': '最后登录',
    'users.confirmDelete': '确认注销该用户？',
    'users.noUsers': '暂无用户',

    // ── F33 修复: 可访问性标签 (a11y) 中文翻译 ──
    'a11y.toggleNavigation': '切换导航菜单',
    'a11y.mainNavigation': '主导航',
    'a11y.footerNavigation': '页脚导航',
    'a11y.loginDialog': '登录对话框',
    'a11y.searchCommands': '搜索命令',
    'a11y.commandPalette': '命令面板',
    'a11y.userProfile': '用户资料',
    'a11y.guideStep': '引导第 {n} 步',
    'a11y.attachImage': '附加图片',
    'a11y.deleteSession': '删除会话',
    'a11y.sortBy': '按 {column} 排序',
    'a11y.yes': '是',
    'a11y.no': '否',
    'a11y.search': '搜索',
    'a11y.closeNotification': '关闭通知',
    'a11y.loading': '加载中',

    // ── Observability view (P3: Observability.vue 硬编码英文的中文翻译) ──
    'view.observability.enterprise': '企业版',
    'view.observability.personal': '个人版',
    'view.observability.tracingOn': '链路追踪开启',
    'view.observability.tracingOff': '链路追踪关闭',
    'view.observability.pipelineTitle': '可观测性管道',
    'view.observability.configTitle': '配置',
    'view.observability.canonicalMetricsTitle': '规范指标 (F1-04)',
    'view.observability.healthChecksTitle': '管道健康检查',
    'view.observability.distributedTracingTitle': '分布式链路追踪',
    'view.observability.statusOk': '正常',
    'view.observability.statusOff': '关闭',
    'view.observability.noHealthData': '暂无健康数据',
    'view.observability.noHealthHint': '点击刷新以运行管道检查。',
    'view.observability.tracingActive': '链路追踪已激活 — 通过 OTLP 导出到 Collector。',
    'view.observability.tracingDisabled': '链路追踪已禁用 ({hint})',
    'view.observability.requestsTotal': '请求总数',
    'view.observability.errorsTotal': '错误总数',
    'view.observability.activeSpans': '活跃 Span',
    'view.observability.traceExports': '追踪导出数',
    'view.observability.structuredLogging': '结构化日志',
    'view.observability.traceCorrelation': '链路关联',
    'view.observability.otelTracing': 'OTel 链路追踪',
    'view.observability.prometheusMetrics': 'Prometheus 指标',
    'view.observability.enterpriseMode': '企业模式',
    'view.observability.otelLinked': 'OTel 已关联',
    'view.observability.noOtel': '无 OTel',
    'view.observability.configEdition': '版本',
    'view.observability.configOtelEnabled': 'OTel 已启用',
    'view.observability.configOtelExporter': 'OTel 导出器',
    'view.observability.configOtelEndpoint': 'OTel 端点',
    'view.observability.configServiceName': '服务名称',
    'view.observability.configScrapePath': '采集路径',
    'view.observability.configGrafanaUid': 'Grafana UID',
    'view.observability.yes': '是',
    'view.observability.no': '否',
    'view.observability.available': '可用',
    'view.observability.missing': '缺失',
    'view.observability.endpointUnavailable': '端点不可用',
    // ── DagGraph 组件 (R4审查补充) ──────────────────────────
    'dag.emptyHint': '输入执行 ID 以订阅 DAG 进度。',
    'dag.connecting': '正在连接执行 {id}…',
    'dag.connected': '已连接',
    'dag.disconnected': '已断开',
  },
};

// Auto-collect every view-level dictionary: src/i18n/view-*.js
// L9 fix: 使用 eager: true 同步加载所有语言包，而非懒加载。原因：
//   1. i18n 字典必须在首屏渲染前就绪，否则 t() 在初次渲染时返回原始 key，导致 UI 闪烁。
//   2. 语言包总体积小（< 50KB gzipped），懒加载的 chunk 数量开销反而大于内容本身。
//   3. vue-i18n 的 createI18n 也默认同步加载 messages，此处保持一致语义。
// 若未来语言包体积显著增长，可改为按路由懒加载 + Suspense 边界处理首次空态。
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
