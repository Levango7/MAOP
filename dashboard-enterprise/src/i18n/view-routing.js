export const messages = {
  en: {

    // ── Tabs ──────────────────────────────────────────────────────
    'view.routing.tab.recent': 'Recent Decisions',
    'view.routing.tab.stats': 'Statistics',

    // ── Recent decisions ──────────────────────────────────────────
    'view.routing.recent.title': 'Recent Routing Decisions',
    'view.routing.recent.empty': 'No routing decisions recorded',
    'view.routing.recent.emptyHint': 'Decisions appear here once agents are dispatched.',
    'view.routing.recent.failedLoad': 'Failed to load recent decisions',
    'view.routing.recent.filterStage': 'All stages',
    'view.routing.recent.viewTrace': 'View trace chain',

    // ── Stats ─────────────────────────────────────────────────────
    'view.routing.stats.title': 'Decision Statistics',
    'view.routing.stats.failedLoad': 'Failed to load statistics',
    'view.routing.stats.empty': 'No statistics available',
    'view.routing.stats.emptyHint': 'Statistics will appear after routing decisions are recorded.',
    'view.routing.stats.total': 'Total Decisions',
    'view.routing.stats.last24h': 'Last 24 Hours',
    'view.routing.stats.byStage': 'By Stage',
    'view.routing.stats.distribution': 'Stage Distribution',

    // ── Trace detail ──────────────────────────────────────────────
    'view.routing.trace.title': 'Decision Chain',
    'view.routing.trace.empty': 'No decisions for this trace',
    'view.routing.trace.failedLoad': 'Failed to load trace chain',
    'view.routing.trace.close': 'Close',

    // ── Columns / common ──────────────────────────────────────────
    'view.routing.col.traceId': 'Trace ID',
    'view.routing.col.stage': 'Stage',
    'view.routing.col.agent': 'Agent',
    'view.routing.col.score': 'Score',
    'view.routing.col.actions': 'Actions',

    // ── Stages ────────────────────────────────────────────────────
    'view.routing.stage.route_scorer': 'Route Scorer',
    'view.routing.stage.load_balancer': 'Load Balancer',
    'view.routing.stage.model_selector': 'Model Selector',
    'view.routing.stage.dispatcher': 'Dispatcher',
  },

  zh: {

    // ── 标签页 ────────────────────────────────────────────────────
    'view.routing.tab.recent': '最近决策',
    'view.routing.tab.stats': '统计',

    // ── 最近决策 ──────────────────────────────────────────────────
    'view.routing.recent.title': '最近路由决策',
    'view.routing.recent.empty': '暂无路由决策记录',
    'view.routing.recent.emptyHint': '智能体被调度后，决策将显示在此处。',
    'view.routing.recent.failedLoad': '加载最近决策失败',
    'view.routing.recent.filterStage': '全部阶段',
    'view.routing.recent.viewTrace': '查看决策链',

    // ── 统计 ──────────────────────────────────────────────────────
    'view.routing.stats.title': '决策统计',
    'view.routing.stats.failedLoad': '加载统计失败',
    'view.routing.stats.empty': '暂无统计数据',
    'view.routing.stats.emptyHint': '路由决策被记录后，统计数据将显示在此处。',
    'view.routing.stats.total': '决策总数',
    'view.routing.stats.last24h': '最近 24 小时',
    'view.routing.stats.byStage': '按阶段',
    'view.routing.stats.distribution': '阶段分布',

    // ── 决策链详情 ────────────────────────────────────────────────
    'view.routing.trace.title': '决策链',
    'view.routing.trace.empty': '该追踪无决策记录',
    'view.routing.trace.failedLoad': '加载决策链失败',
    'view.routing.trace.close': '关闭',

    // ── 列 / 通用 ─────────────────────────────────────────────────
    'view.routing.col.traceId': '追踪 ID',
    'view.routing.col.stage': '阶段',
    'view.routing.col.agent': '智能体',
    'view.routing.col.score': '得分',
    'view.routing.col.actions': '操作',

    // ── 阶段 ──────────────────────────────────────────────────────
    'view.routing.stage.route_scorer': '路由评分',
    'view.routing.stage.load_balancer': '负载均衡',
    'view.routing.stage.model_selector': '模型选择',
    'view.routing.stage.dispatcher': '调度器',
  },
};