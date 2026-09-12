export const messages = {
  en: {
    'view.analysis.title': 'Analysis',
    'view.analysis.sub': 'Data analysis dashboards and insights.',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.analysis.tab.summary': 'Summary',
    'view.analysis.tab.agents': 'Agent Efficiency',
    'view.analysis.tab.trends': 'Task Trends',
    'view.analysis.tab.resources': 'Resources',
    'view.analysis.tab.cost': 'Cost',
    'view.analysis.tab.bottlenecks': 'Bottlenecks',

    // ── Date range ───────────────────────────────────────────────
    'view.analysis.range.7d': '7d',
    'view.analysis.range.30d': '30d',
    'view.analysis.range.90d': '90d',

    // ── Summary ──────────────────────────────────────────────────
    'view.analysis.summary.title': 'KPI Summary',
    'view.analysis.summary.empty': 'No summary data available',
    'view.analysis.summary.emptyHint': 'Summary appears after tasks have been executed.',
    'view.analysis.summary.failedLoad': 'Failed to load summary',
    'view.analysis.summary.totalTasks': 'Total Tasks',
    'view.analysis.summary.successRate': 'Success Rate',
    'view.analysis.summary.totalCost': 'Total Cost',
    'view.analysis.summary.avgLatency': 'Avg Latency',
    'view.analysis.summary.activeAgents': 'Active Agents',
    'view.analysis.summary.tokens': 'Tokens',

    // ── Agent efficiency ────────────────────────────────────────
    'view.analysis.agents.title': 'Agent Efficiency',
    'view.analysis.agents.empty': 'No agent data',
    'view.analysis.agents.emptyHint': 'Agent metrics appear after delegations occur.',
    'view.analysis.agents.failedLoad': 'Failed to load agent efficiency',
    'view.analysis.agents.col.agent': 'Agent',
    'view.analysis.agents.col.tasks': 'Tasks',
    'view.analysis.agents.col.successRate': 'Success',
    'view.analysis.agents.col.tokens': 'Tokens',
    'view.analysis.agents.col.cost': 'Cost (USD)',
    'view.analysis.agents.col.latency': 'Latency',
    'view.analysis.agents.col.tasksPerUsd': 'Tasks/$',
    'view.analysis.agents.col.circuit': 'Circuit',

    // ── Task trends ─────────────────────────────────────────────
    'view.analysis.trends.title': 'Task Trends',
    'view.analysis.trends.empty': 'No trend data',
    'view.analysis.trends.emptyHint': 'Trends appear after audit events are recorded.',
    'view.analysis.trends.failedLoad': 'Failed to load task trends',
    'view.analysis.trends.granularity': 'Granularity',
    'view.analysis.trends.granularity.hour': 'Hour',
    'view.analysis.trends.granularity.day': 'Day',
    'view.analysis.trends.granularity.week': 'Week',
    'view.analysis.trends.col.bucket': 'Bucket',
    'view.analysis.trends.col.success': 'Success',
    'view.analysis.trends.col.failure': 'Failure',
    'view.analysis.trends.col.timeout': 'Timeout',
    'view.analysis.trends.col.total': 'Total',
    'view.analysis.trends.col.successRate': 'Success Rate',
    'view.analysis.trends.chart': 'Success / Failure / Timeout',

    // ── Resources ───────────────────────────────────────────────
    'view.analysis.resources.title': 'Resource Utilization',
    'view.analysis.resources.empty': 'No resource data',
    'view.analysis.resources.emptyHint': 'Resource metrics appear after monitoring starts.',
    'view.analysis.resources.failedLoad': 'Failed to load resource utilization',
    'view.analysis.resources.cpu': 'CPU (%)',
    'view.analysis.resources.memory': 'Memory (MB)',
    'view.analysis.resources.dbPool': 'DB Pool',
    'view.analysis.resources.cacheHit': 'Cache Hit Rate',

    // ── Cost breakdown ──────────────────────────────────────────
    'view.analysis.cost.title': 'Cost Breakdown',
    'view.analysis.cost.empty': 'No cost data',
    'view.analysis.cost.emptyHint': 'Cost breakdown appears after token usage is recorded.',
    'view.analysis.cost.failedLoad': 'Failed to load cost breakdown',
    'view.analysis.cost.col.dimension': 'Dimension',
    'view.analysis.cost.col.value': 'Value',
    'view.analysis.cost.col.tokens': 'Tokens',
    'view.analysis.cost.col.calls': 'Calls',
    'view.analysis.cost.total': 'Total Cost',

    // ── Bottlenecks ─────────────────────────────────────────────
    'view.analysis.bottlenecks.title': 'Performance Bottlenecks',
    'view.analysis.bottlenecks.empty': 'No bottleneck data',
    'view.analysis.bottlenecks.emptyHint': 'Bottlenecks appear after sufficient execution data is collected.',
    'view.analysis.bottlenecks.failedLoad': 'Failed to load bottlenecks',
    'view.analysis.bottlenecks.slowEndpoints': 'Slowest Endpoints',
    'view.analysis.bottlenecks.expensiveAgents': 'Most Expensive Agents',
    'view.analysis.bottlenecks.memoryConsumers': 'Top Memory Consumers',
    'view.analysis.bottlenecks.col.name': 'Name',
    'view.analysis.bottlenecks.col.latency': 'Latency (ms)',
    'view.analysis.bottlenecks.col.cost': 'Cost (USD)',
    'view.analysis.bottlenecks.col.memory': 'Memory (MB)',

    // ── Common ───────────────────────────────────────────────────
    'view.analysis.note': 'Note',
    'view.analysis.generatedAt': 'Generated at',
  },

  zh: {
    'view.analysis.title': '数据分析',
    'view.analysis.sub': '数据分析仪表盘与洞察。',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.analysis.tab.summary': '摘要',
    'view.analysis.tab.agents': '智能体效率',
    'view.analysis.tab.trends': '任务趋势',
    'view.analysis.tab.resources': '资源',
    'view.analysis.tab.cost': '成本',
    'view.analysis.tab.bottlenecks': '瓶颈',

    // ── 日期范围 ──────────────────────────────────────────────────
    'view.analysis.range.7d': '7天',
    'view.analysis.range.30d': '30天',
    'view.analysis.range.90d': '90天',

    // ── 摘要 ──────────────────────────────────────────────────────
    'view.analysis.summary.title': 'KPI 摘要',
    'view.analysis.summary.empty': '暂无摘要数据',
    'view.analysis.summary.emptyHint': '任务执行后将显示摘要数据。',
    'view.analysis.summary.failedLoad': '加载摘要失败',
    'view.analysis.summary.totalTasks': '总任务数',
    'view.analysis.summary.successRate': '成功率',
    'view.analysis.summary.totalCost': '总成本',
    'view.analysis.summary.avgLatency': '平均延迟',
    'view.analysis.summary.activeAgents': '活跃智能体',
    'view.analysis.summary.tokens': 'Token 数',

    // ── 智能体效率 ────────────────────────────────────────────────
    'view.analysis.agents.title': '智能体效率',
    'view.analysis.agents.empty': '暂无智能体数据',
    'view.analysis.agents.emptyHint': '智能体委派发生后将显示指标。',
    'view.analysis.agents.failedLoad': '加载智能体效率失败',
    'view.analysis.agents.col.agent': '智能体',
    'view.analysis.agents.col.tasks': '任务数',
    'view.analysis.agents.col.successRate': '成功率',
    'view.analysis.agents.col.tokens': 'Token',
    'view.analysis.agents.col.cost': '成本（美元）',
    'view.analysis.agents.col.latency': '延迟',
    'view.analysis.agents.col.tasksPerUsd': '任务/$',
    'view.analysis.agents.col.circuit': '熔断器',

    // ── 任务趋势 ──────────────────────────────────────────────────
    'view.analysis.trends.title': '任务趋势',
    'view.analysis.trends.empty': '暂无趋势数据',
    'view.analysis.trends.emptyHint': '审计事件记录后将显示趋势。',
    'view.analysis.trends.failedLoad': '加载任务趋势失败',
    'view.analysis.trends.granularity': '粒度',
    'view.analysis.trends.granularity.hour': '小时',
    'view.analysis.trends.granularity.day': '天',
    'view.analysis.trends.granularity.week': '周',
    'view.analysis.trends.col.bucket': '时间桶',
    'view.analysis.trends.col.success': '成功',
    'view.analysis.trends.col.failure': '失败',
    'view.analysis.trends.col.timeout': '超时',
    'view.analysis.trends.col.total': '总数',
    'view.analysis.trends.col.successRate': '成功率',
    'view.analysis.trends.chart': '成功 / 失败 / 超时',

    // ── 资源 ──────────────────────────────────────────────────────
    'view.analysis.resources.title': '资源利用率',
    'view.analysis.resources.empty': '暂无资源数据',
    'view.analysis.resources.emptyHint': '监控启动后将显示资源指标。',
    'view.analysis.resources.failedLoad': '加载资源利用率失败',
    'view.analysis.resources.cpu': 'CPU（%）',
    'view.analysis.resources.memory': '内存（MB）',
    'view.analysis.resources.dbPool': '数据库连接池',
    'view.analysis.resources.cacheHit': '缓存命中率',

    // ── 成本分解 ──────────────────────────────────────────────────
    'view.analysis.cost.title': '成本分解',
    'view.analysis.cost.empty': '暂无成本数据',
    'view.analysis.cost.emptyHint': 'Token 用量记录后将显示成本分解。',
    'view.analysis.cost.failedLoad': '加载成本分解失败',
    'view.analysis.cost.col.dimension': '维度',
    'view.analysis.cost.col.value': '数值',
    'view.analysis.cost.col.tokens': 'Token',
    'view.analysis.cost.col.calls': '调用次数',
    'view.analysis.cost.total': '总成本',

    // ── 瓶颈 ──────────────────────────────────────────────────────
    'view.analysis.bottlenecks.title': '性能瓶颈',
    'view.analysis.bottlenecks.empty': '暂无瓶颈数据',
    'view.analysis.bottlenecks.emptyHint': '收集足够执行数据后将显示瓶颈。',
    'view.analysis.bottlenecks.failedLoad': '加载瓶颈失败',
    'view.analysis.bottlenecks.slowEndpoints': '最慢端点',
    'view.analysis.bottlenecks.expensiveAgents': '最贵智能体',
    'view.analysis.bottlenecks.memoryConsumers': '内存占用 Top',
    'view.analysis.bottlenecks.col.name': '名称',
    'view.analysis.bottlenecks.col.latency': '延迟（毫秒）',
    'view.analysis.bottlenecks.col.cost': '成本（美元）',
    'view.analysis.bottlenecks.col.memory': '内存（MB）',

    // ── 通用 ──────────────────────────────────────────────────────
    'view.analysis.note': '备注',
    'view.analysis.generatedAt': '生成时间',
  },
};