export const messages = {
  en: {

    // ── Tabs ──────────────────────────────────────────────────────
    'view.agentProxy.tab.adapters': 'Adapters',
    'view.agentProxy.tab.health': 'Health',
    'view.agentProxy.tab.call': 'Call',

    // ── Adapters ──────────────────────────────────────────────────
    'view.agentProxy.adapters.title': 'Bridge Adapters',
    'view.agentProxy.adapters.empty': 'No adapters registered',
    'view.agentProxy.adapters.emptyHint': 'Register an adapter in the backend to expose it here.',
    'view.agentProxy.adapters.failedLoad': 'Failed to load adapters',
    'view.agentProxy.adapters.refresh': 'Refresh',

    // ── Columns ───────────────────────────────────────────────────
    'view.agentProxy.col.name': 'Name',
    'view.agentProxy.col.status': 'Status',
    'view.agentProxy.col.latency': 'Latency',
    'view.agentProxy.col.calls': 'Calls',
    'view.agentProxy.col.actions': 'Actions',

    // ── Status ────────────────────────────────────────────────────
    'view.agentProxy.status.healthy': 'Healthy',
    'view.agentProxy.status.unhealthy': 'Unhealthy',
    'view.agentProxy.status.unknown': 'Unknown',

    // ── Health ────────────────────────────────────────────────────
    'view.agentProxy.health.title': 'Health Check',
    'view.agentProxy.health.empty': 'No health data',
    'view.agentProxy.health.emptyHint': 'Run a health check to inspect adapter statuses.',
    'view.agentProxy.health.failedLoad': 'Failed to load health data',
    'view.agentProxy.health.runBtn': 'Run Health Check',
    'view.agentProxy.health.running': 'Checking…',
    'view.agentProxy.health.healthyCount': 'Healthy',
    'view.agentProxy.health.unhealthyCount': 'Unhealthy',
    'view.agentProxy.health.totalCount': 'Total',
    'view.agentProxy.health.lastCheck': 'Last check',

    // ── Call ──────────────────────────────────────────────────────
    'view.agentProxy.call.title': 'Proxy Call',
    'view.agentProxy.call.hint': 'Proxy a task through a bridge adapter to the agent backend.',
    'view.agentProxy.call.fieldAdapter': 'Adapter',
    'view.agentProxy.call.fieldAdapterHint': 'Select a bridge adapter',
    'view.agentProxy.call.fieldTask': 'Task',
    'view.agentProxy.call.fieldTaskHint': 'Task to proxy through the adapter',
    'view.agentProxy.call.fieldKwargs': 'Keyword arguments (JSON)',
    'view.agentProxy.call.fieldKwargsHint': '{}',
    'view.agentProxy.call.submit': 'Send',
    'view.agentProxy.call.sending': 'Sending…',
    'view.agentProxy.call.validateAdapter': 'Adapter is required',
    'view.agentProxy.call.validateTask': 'Task is required',
    'view.agentProxy.call.validateKwargs': 'Invalid JSON',
    'view.agentProxy.call.success': 'Call succeeded',
    'view.agentProxy.call.failed': 'Call failed',
    'view.agentProxy.call.result': 'Result',

    // ── Sync config ───────────────────────────────────────────────
    'view.agentProxy.sync.title': 'Sync Configuration',
    'view.agentProxy.sync.hint': 'Push configuration to a bridge adapter.',
    'view.agentProxy.sync.fieldConfigHint': '{}',
    'view.agentProxy.sync.submit': 'Sync',
    'view.agentProxy.sync.syncing': 'Syncing…',
    'view.agentProxy.sync.validateConfig': 'Invalid JSON',
    'view.agentProxy.sync.success': 'Configuration synced',
    'view.agentProxy.sync.failed': 'Failed to sync configuration',

    // ── Actions ───────────────────────────────────────────────────
    'view.agentProxy.action.sync': 'Sync Config',
    'view.agentProxy.action.call': 'Call',
  },

  zh: {

    // ── 标签页 ────────────────────────────────────────────────────
    'view.agentProxy.tab.adapters': '适配器',
    'view.agentProxy.tab.health': '健康',
    'view.agentProxy.tab.call': '调用',

    // ── 适配器 ────────────────────────────────────────────────────
    'view.agentProxy.adapters.title': '桥接适配器',
    'view.agentProxy.adapters.empty': '暂无已注册适配器',
    'view.agentProxy.adapters.emptyHint': '在后端注册一个适配器即可在此显示。',
    'view.agentProxy.adapters.failedLoad': '加载适配器失败',
    'view.agentProxy.adapters.refresh': '刷新',

    // ── 列 ────────────────────────────────────────────────────────
    'view.agentProxy.col.name': '名称',
    'view.agentProxy.col.status': '状态',
    'view.agentProxy.col.latency': '延迟',
    'view.agentProxy.col.calls': '调用次数',
    'view.agentProxy.col.actions': '操作',

    // ── 状态 ──────────────────────────────────────────────────────
    'view.agentProxy.status.healthy': '健康',
    'view.agentProxy.status.unhealthy': '不健康',
    'view.agentProxy.status.unknown': '未知',

    // ── 健康 ──────────────────────────────────────────────────────
    'view.agentProxy.health.title': '健康检查',
    'view.agentProxy.health.empty': '暂无健康数据',
    'view.agentProxy.health.emptyHint': '运行健康检查以查看适配器状态。',
    'view.agentProxy.health.failedLoad': '加载健康数据失败',
    'view.agentProxy.health.runBtn': '运行健康检查',
    'view.agentProxy.health.running': '检查中…',
    'view.agentProxy.health.healthyCount': '健康',
    'view.agentProxy.health.unhealthyCount': '不健康',
    'view.agentProxy.health.totalCount': '总计',
    'view.agentProxy.health.lastCheck': '上次检查',

    // ── 调用 ──────────────────────────────────────────────────────
    'view.agentProxy.call.title': '代理调用',
    'view.agentProxy.call.hint': '通过桥接适配器将任务代理到智能体后端。',
    'view.agentProxy.call.fieldAdapter': '适配器',
    'view.agentProxy.call.fieldAdapterHint': '选择一个桥接适配器',
    'view.agentProxy.call.fieldTask': '任务',
    'view.agentProxy.call.fieldTaskHint': '要通过适配器代理的任务',
    'view.agentProxy.call.fieldKwargs': '关键字参数（JSON）',
    'view.agentProxy.call.fieldKwargsHint': '{}',
    'view.agentProxy.call.submit': '发送',
    'view.agentProxy.call.sending': '发送中…',
    'view.agentProxy.call.validateAdapter': '适配器为必填项',
    'view.agentProxy.call.validateTask': '任务为必填项',
    'view.agentProxy.call.validateKwargs': 'JSON 无效',
    'view.agentProxy.call.success': '调用成功',
    'view.agentProxy.call.failed': '调用失败',
    'view.agentProxy.call.result': '结果',

    // ── 同步配置 ──────────────────────────────────────────────────
    'view.agentProxy.sync.title': '同步配置',
    'view.agentProxy.sync.hint': '将配置推送到桥接适配器。',
    'view.agentProxy.sync.fieldConfigHint': '{}',
    'view.agentProxy.sync.submit': '同步',
    'view.agentProxy.sync.syncing': '同步中…',
    'view.agentProxy.sync.validateConfig': 'JSON 无效',
    'view.agentProxy.sync.success': '配置已同步',
    'view.agentProxy.sync.failed': '同步配置失败',

    // ── 操作 ──────────────────────────────────────────────────────
    'view.agentProxy.action.sync': '同步配置',
    'view.agentProxy.action.call': '调用',
  },
};