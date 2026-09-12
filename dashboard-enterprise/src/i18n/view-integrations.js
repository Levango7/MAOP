/**
 * i18n messages for Integrations page (src/views/Integrations.vue).
 *
 * Keys are namespaced under `view.integrations.*`. Auto-collected by
 * src/i18n/index.js via import.meta.glob('./view-*.js').
 *
 * Integrations page manages third-party integrations, primarily n8n:
 * - Connection health check
 * - List n8n workflows
 * - Trigger a workflow
 * - Inspect execution status
 * - Webhook receiver info
 */
export const messages = {
  en: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.integrations': 'Integrations',
    'nav.integrations.subtitle': 'N8N and external system integrations',
    'view.integrations.subtitle': 'Manage n8n workflow integrations: check connectivity, browse workflows, trigger runs and inspect executions.',

    // ── Tabs ─────────────────────────────────────────────────────
    'view.integrations.tab.workflows': 'Workflows',
    'view.integrations.tab.executions': 'Executions',
    'view.integrations.tab.health': 'Health',

    // ── Health ───────────────────────────────────────────────────
    'view.integrations.health.title': 'n8n Connectivity',
    'view.integrations.health.check': 'Run Health Check',
    'view.integrations.health.checking': 'Checking…',
    'view.integrations.health.reachable': 'Reachable',
    'view.integrations.health.unreachable': 'Unreachable',
    'view.integrations.health.baseUrl': 'Base URL',
    'view.integrations.health.lastCheck': 'Last Check',
    'view.integrations.health.failed': 'Health check failed',
    'view.integrations.health.hint': 'Checks whether the n8n service is reachable from MAOP. Requires N8N_INTEGRATION feature flag and admin credentials.',

    // ── Workflows ────────────────────────────────────────────────
    'view.integrations.workflows.title': 'n8n Workflows',
    'view.integrations.workflows.empty': 'No workflows available',
    'view.integrations.workflows.emptyHint': 'Check n8n connectivity or create workflows in n8n first.',
    'view.integrations.workflows.failedLoad': 'Failed to load workflows',
    'view.integrations.workflows.refresh': 'Refresh',
    'view.integrations.workflows.trigger': 'Trigger',
    'view.integrations.workflows.triggering': 'Triggering…',
    'view.integrations.workflows.triggerSuccess': 'Workflow triggered (execution {id})',
    'view.integrations.workflows.triggerFailed': 'Failed to trigger workflow',
    'view.integrations.workflows.viewExec': 'View Execution',

    // ── Workflow table columns ───────────────────────────────────
    'view.integrations.col.id': 'ID',
    'view.integrations.col.name': 'Name',
    'view.integrations.col.active': 'Active',
    'view.integrations.col.nodes': 'Nodes',
    'view.integrations.col.lastExec': 'Last Execution',
    'view.integrations.col.status': 'Status',
    'view.integrations.col.actions': 'Actions',

    // ── Trigger dialog ───────────────────────────────────────────
    'view.integrations.trigger.title': 'Trigger Workflow',
    'view.integrations.trigger.workflow': 'Workflow',
    'view.integrations.trigger.data': 'Input Data (JSON)',
    'view.integrations.trigger.dataPlaceholder': '{"key": "value"}',
    'view.integrations.trigger.wait': 'Wait for completion',
    'view.integrations.trigger.submit': 'Trigger',
    'view.integrations.trigger.cancel': 'Cancel',
    'view.integrations.trigger.validateData': 'Input data must be valid JSON',

    // ── Executions ───────────────────────────────────────────────
    'view.integrations.executions.title': 'Recent Executions',
    'view.integrations.executions.empty': 'No executions yet',
    'view.integrations.executions.emptyHint': 'Trigger a workflow from the Workflows tab to see executions here.',
    'view.integrations.executions.failedLoad': 'Failed to load execution',
    'view.integrations.executions.lookup': 'Lookup Execution',
    'view.integrations.executions.idPlaceholder': 'Execution ID',
    'view.integrations.executions.col.id': 'Execution ID',
    'view.integrations.executions.col.workflow': 'Workflow',
    'view.integrations.executions.col.status': 'Status',
    'view.integrations.executions.col.started': 'Started',
    'view.integrations.executions.col.finished': 'Finished',
    'view.integrations.executions.col.duration': 'Duration',
    'view.integrations.executions.col.mode': 'Mode',

    // ── Execution status ─────────────────────────────────────────
    'view.integrations.exec.status.running': 'Running',
    'view.integrations.exec.status.success': 'Success',
    'view.integrations.exec.status.failed': 'Failed',
    'view.integrations.exec.status.waiting': 'Waiting',
    'view.integrations.exec.status.unknown': 'Unknown',

    // ── Webhook info ─────────────────────────────────────────────
    'view.integrations.webhook.title': 'Webhook Endpoint',
    'view.integrations.webhook.url': 'URL',
    'view.integrations.webhook.method': 'Method',
    'view.integrations.webhook.signature': 'Signature Header',
    'view.integrations.webhook.hint': 'Configure n8n to POST to this endpoint with an X-N8N-Signature header (HMAC-SHA256 of the body with N8N_WEBHOOK_SECRET).',

    // ── Feature flag ─────────────────────────────────────────────
    'view.integrations.feature.disabled': 'n8n integration is not enabled on this instance',
    'view.integrations.feature.hint': 'Set the N8N_INTEGRATION feature flag and N8N_BASE_URL/N8N_API_KEY environment variables to enable.',

    // ── Stats overview ───────────────────────────────────────────
    'view.integrations.stats.title': 'Integration Statistics',
    'view.integrations.stats.totalWorkflows': 'Total Workflows',
    'view.integrations.stats.activeWorkflows': 'Active Workflows',
    'view.integrations.stats.recentExecutions': 'Recent Executions',
    'view.integrations.stats.successRate': 'Success Rate',
    'view.integrations.stats.empty': 'No statistics available',
  },

  zh: {
    // ── 页头与导航 ───────────────────────────────────────────────
    'nav.integrations': '集成',
    'nav.integrations.subtitle': 'N8N 与外部系统集成',
    'view.integrations.subtitle': '管理 n8n 工作流集成：检查连通性、浏览工作流、触发运行并查看执行状态。',

    // ── 标签页 ───────────────────────────────────────────────────
    'view.integrations.tab.workflows': '工作流',
    'view.integrations.tab.executions': '执行',
    'view.integrations.tab.health': '健康',

    // ── 健康 ─────────────────────────────────────────────────────
    'view.integrations.health.title': 'n8n 连通性',
    'view.integrations.health.check': '执行健康检查',
    'view.integrations.health.checking': '检查中…',
    'view.integrations.health.reachable': '可达',
    'view.integrations.health.unreachable': '不可达',
    'view.integrations.health.baseUrl': '基础 URL',
    'view.integrations.health.lastCheck': '最近检查',
    'view.integrations.health.failed': '健康检查失败',
    'view.integrations.health.hint': '检查 MAOP 是否可访问 n8n 服务。需要 N8N_INTEGRATION 特性开关与管理员凭据。',

    // ── 工作流 ───────────────────────────────────────────────────
    'view.integrations.workflows.title': 'n8n 工作流',
    'view.integrations.workflows.empty': '暂无可用工作流',
    'view.integrations.workflows.emptyHint': '请检查 n8n 连通性或先在 n8n 中创建工作流。',
    'view.integrations.workflows.failedLoad': '加载工作流失败',
    'view.integrations.workflows.refresh': '刷新',
    'view.integrations.workflows.trigger': '触发',
    'view.integrations.workflows.triggering': '触发中…',
    'view.integrations.workflows.triggerSuccess': '工作流已触发（执行 {id}）',
    'view.integrations.workflows.triggerFailed': '触发工作流失败',
    'view.integrations.workflows.viewExec': '查看执行',

    // ── 工作流表格列 ─────────────────────────────────────────────
    'view.integrations.col.id': 'ID',
    'view.integrations.col.name': '名称',
    'view.integrations.col.active': '活跃',
    'view.integrations.col.nodes': '节点数',
    'view.integrations.col.lastExec': '最近执行',
    'view.integrations.col.status': '状态',
    'view.integrations.col.actions': '操作',

    // ── 触发对话框 ───────────────────────────────────────────────
    'view.integrations.trigger.title': '触发工作流',
    'view.integrations.trigger.workflow': '工作流',
    'view.integrations.trigger.data': '输入数据（JSON）',
    'view.integrations.trigger.dataPlaceholder': '{"key": "value"}',
    'view.integrations.trigger.wait': '等待完成',
    'view.integrations.trigger.submit': '触发',
    'view.integrations.trigger.cancel': '取消',
    'view.integrations.trigger.validateData': '输入数据必须是合法的 JSON',

    // ── 执行 ─────────────────────────────────────────────────────
    'view.integrations.executions.title': '近期执行',
    'view.integrations.executions.empty': '暂无执行记录',
    'view.integrations.executions.emptyHint': '在「工作流」标签页触发工作流即可在此查看执行。',
    'view.integrations.executions.failedLoad': '加载执行失败',
    'view.integrations.executions.lookup': '查询执行',
    'view.integrations.executions.idPlaceholder': '执行 ID',
    'view.integrations.executions.col.id': '执行 ID',
    'view.integrations.executions.col.workflow': '工作流',
    'view.integrations.executions.col.status': '状态',
    'view.integrations.executions.col.started': '开始',
    'view.integrations.executions.col.finished': '结束',
    'view.integrations.executions.col.duration': '耗时',
    'view.integrations.executions.col.mode': '模式',

    // ── 执行状态 ─────────────────────────────────────────────────
    'view.integrations.exec.status.running': '运行中',
    'view.integrations.exec.status.success': '成功',
    'view.integrations.exec.status.failed': '失败',
    'view.integrations.exec.status.waiting': '等待中',
    'view.integrations.exec.status.unknown': '未知',

    // ── Webhook 信息 ─────────────────────────────────────────────
    'view.integrations.webhook.title': 'Webhook 端点',
    'view.integrations.webhook.url': 'URL',
    'view.integrations.webhook.method': '方法',
    'view.integrations.webhook.signature': '签名头',
    'view.integrations.webhook.hint': '配置 n8n 向此端点 POST，并携带 X-N8N-Signature 头（以 N8N_WEBHOOK_SECRET 对 body 计算 HMAC-SHA256）。',

    // ── 特性开关 ─────────────────────────────────────────────────
    'view.integrations.feature.disabled': '本实例未启用 n8n 集成',
    'view.integrations.feature.hint': '设置 N8N_INTEGRATION 特性开关以及 N8N_BASE_URL/N8N_API_KEY 环境变量以启用。',

    // ── 统计概览 ─────────────────────────────────────────────────
    'view.integrations.stats.title': '集成统计',
    'view.integrations.stats.totalWorkflows': '工作流总数',
    'view.integrations.stats.activeWorkflows': '活跃工作流',
    'view.integrations.stats.recentExecutions': '近期执行',
    'view.integrations.stats.successRate': '成功率',
    'view.integrations.stats.empty': '暂无统计数据',
  },
};