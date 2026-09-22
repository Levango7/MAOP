/**
 * i18n messages for Webhook management page (src/views/Webhooks.vue).
 *
 * Keys are namespaced under `view.webhooks.*`. Auto-collected by
 * src/i18n/index.js via import.meta.glob('./view-*.js').
 *
 * Note: this is distinct from view-hooks.js (lifecycle Hook tab in Settings).
 * Webhooks.vue manages outbound webhooks: URL, event types, auth (HMAC/Bearer/None),
 * timeout, trigger history.
 */
export const messages = {
  en: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.webhooks': 'Webhooks',
    'nav.webhooks.subtitle': 'Outbound webhook endpoints and delivery history',
    'view.webhooks.enterprise': 'Enterprise',
    'view.webhooks.subtitle': 'Outbound webhook endpoints and delivery history',
    'view.webhooks.createBtn': 'New Webhook',
    'view.webhooks.refreshBtn': 'Refresh',
    'view.webhooks.hint': 'Webhooks POST to the configured URL when subscribed events fire. Use HMAC or Bearer auth to secure deliveries.',

    // ── Table columns ───────────────────────────────────────────
    'view.webhooks.colName': 'Name',
    'view.webhooks.colUrl': 'URL',
    'view.webhooks.colEvents': 'Events',
    'view.webhooks.colStatus': 'Status',
    'view.webhooks.colLastTrigger': 'Last Trigger',
    'view.webhooks.colActions': 'Actions',

    // ── Status ──────────────────────────────────────────────────
    'view.webhooks.statusEnabled': 'Enabled',
    'view.webhooks.statusDisabled': 'Disabled',

    // ── Auth methods ────────────────────────────────────────────
    'view.webhooks.authNone': 'None',
    'view.webhooks.authHmac': 'HMAC',
    'view.webhooks.authBearer': 'Bearer',

    // ── Actions ─────────────────────────────────────────────────
    'view.webhooks.actionEdit': 'Edit',
    'view.webhooks.actionDelete': 'Delete',
    'view.webhooks.actionTest': 'Test',
    'view.webhooks.actionHistory': 'History',
    'view.webhooks.deleteConfirm': 'Delete webhook "{name}"? This action cannot be undone.',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.webhooks.dialogTitleCreate': 'Create Webhook',
    'view.webhooks.dialogTitleEdit': 'Edit Webhook',
    'view.webhooks.fieldName': 'Webhook Name',
    'view.webhooks.fieldUrl': 'Webhook URL',
    'view.webhooks.fieldEvents': 'Event Types',
    'view.webhooks.fieldAuth': 'Authentication',
    'view.webhooks.fieldAuthSecret': 'HMAC Secret',
    'view.webhooks.fieldBearerToken': 'Bearer Token',
    'view.webhooks.fieldTimeout': 'Timeout (s)',
    'view.webhooks.fieldRetry': 'Retry Count',
    'view.webhooks.fieldEnabled': 'Enabled',
    'view.webhooks.btnSave': 'Save',
    'view.webhooks.btnCancel': 'Cancel',
    'view.webhooks.saving': 'Saving…',
    'view.webhooks.placeholderUrl': 'https://example.com/webhook',
    'view.webhooks.placeholderSecret': 'HMAC shared secret',
    'view.webhooks.placeholderToken': 'Bearer token',

    // ── Event types ─────────────────────────────────────────────
    'view.webhooks.eventAgentDispatch': 'Agent Dispatch',
    'view.webhooks.eventTaskComplete': 'Task Complete',
    'view.webhooks.eventAlertTriggered': 'Alert Triggered',
    'view.webhooks.eventCostThreshold': 'Cost Threshold',
    'view.webhooks.eventSystemHealth': 'System Health',
    'view.webhooks.eventAuditEvent': 'Audit Event',

    // ── Empty / error / loading ────────────────────────────────
    'view.webhooks.empty': 'No webhooks configured',
    'view.webhooks.emptyDesc': 'Create a webhook to receive event notifications at an external URL.',
    'view.webhooks.loadError': 'Failed to load webhooks',
    'view.webhooks.saveError': 'Failed to save webhook',
    'view.webhooks.deleteError': 'Failed to delete webhook',
    'view.webhooks.testError': 'Failed to test webhook',
    'view.webhooks.historyError': 'Failed to load trigger history',
    'view.webhooks.loading': 'Loading…',

    // ── Test result ─────────────────────────────────────────────
    'view.webhooks.testSuccess': 'Webhook test succeeded ({ms}ms)',
    'view.webhooks.testFailed': 'Webhook test failed: {error}',
    'view.webhooks.testNoListener': 'Test sent (no listener responded)',

    // ── Trigger history ─────────────────────────────────────────
    'view.webhooks.historyTitle': 'Trigger History',
    'view.webhooks.historyColTime': 'Time',
    'view.webhooks.historyColEvent': 'Event',
    'view.webhooks.historyColStatus': 'Status',
    'view.webhooks.historyColLatency': 'Latency',
    'view.webhooks.historyColResponse': 'Response',
    'view.webhooks.historyEmpty': 'No trigger records',
    'view.webhooks.deliverySuccess': 'Delivered',
    'view.webhooks.deliveryFailed': 'Failed',
    'view.webhooks.deliveryRetry': 'Retrying',

    // ── Validation ──────────────────────────────────────────────
    'view.webhooks.validateNameRequired': 'Name is required',
    'view.webhooks.validateUrlRequired': 'URL is required',
    'view.webhooks.validateUrlInvalid': 'URL must start with http:// or https://',
    'view.webhooks.validateEventsRequired': 'Select at least one event type',
    'view.webhooks.validateSecretRequired': 'HMAC secret is required when auth is HMAC',
    'view.webhooks.validateTokenRequired': 'Bearer token is required when auth is Bearer',

    // ── Toast ───────────────────────────────────────────────────
    'view.webhooks.saved': 'Webhook saved',
    'view.webhooks.deleted': 'Webhook deleted',
    'view.webhooks.never': 'Never',
  },
  zh: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.webhooks': 'Webhook',
    'nav.webhooks.subtitle': '出站 Webhook 端点与投递历史',
    'view.webhooks.enterprise': '企业版',
    'view.webhooks.subtitle': '出站 Webhook 端点与投递历史',
    'view.webhooks.createBtn': '新建 Webhook',
    'view.webhooks.refreshBtn': '刷新',
    'view.webhooks.hint': 'Webhook 在订阅事件触发时向配置的 URL 发送 POST 请求。使用 HMAC 或 Bearer 认证保护投递安全。',

    // ── Table columns ───────────────────────────────────────────
    'view.webhooks.colName': '名称',
    'view.webhooks.colUrl': 'URL',
    'view.webhooks.colEvents': '事件',
    'view.webhooks.colStatus': '状态',
    'view.webhooks.colLastTrigger': '最近触发',
    'view.webhooks.colActions': '操作',

    // ── Status ──────────────────────────────────────────────────
    'view.webhooks.statusEnabled': '已启用',
    'view.webhooks.statusDisabled': '已禁用',

    // ── Auth methods ────────────────────────────────────────────
    'view.webhooks.authNone': '无',
    'view.webhooks.authHmac': 'HMAC',
    'view.webhooks.authBearer': 'Bearer',

    // ── Actions ─────────────────────────────────────────────────
    'view.webhooks.actionEdit': '编辑',
    'view.webhooks.actionDelete': '删除',
    'view.webhooks.actionTest': '测试',
    'view.webhooks.actionHistory': '历史',
    'view.webhooks.deleteConfirm': '确认删除 Webhook "{name}"？此操作不可撤销。',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.webhooks.dialogTitleCreate': '新建 Webhook',
    'view.webhooks.dialogTitleEdit': '编辑 Webhook',
    'view.webhooks.fieldName': 'Webhook 名称',
    'view.webhooks.fieldUrl': 'Webhook URL',
    'view.webhooks.fieldEvents': '事件类型',
    'view.webhooks.fieldAuth': '认证方式',
    'view.webhooks.fieldAuthSecret': 'HMAC 密钥',
    'view.webhooks.fieldBearerToken': 'Bearer Token',
    'view.webhooks.fieldTimeout': '超时 (秒)',
    'view.webhooks.fieldRetry': '重试次数',
    'view.webhooks.fieldEnabled': '启用',
    'view.webhooks.btnSave': '保存',
    'view.webhooks.btnCancel': '取消',
    'view.webhooks.saving': '保存中…',
    'view.webhooks.placeholderUrl': 'https://example.com/webhook',
    'view.webhooks.placeholderSecret': 'HMAC 共享密钥',
    'view.webhooks.placeholderToken': 'Bearer token',

    // ── Event types ─────────────────────────────────────────────
    'view.webhooks.eventAgentDispatch': '智能体调度',
    'view.webhooks.eventTaskComplete': '任务完成',
    'view.webhooks.eventAlertTriggered': '告警触发',
    'view.webhooks.eventCostThreshold': '成本阈值',
    'view.webhooks.eventSystemHealth': '系统健康',
    'view.webhooks.eventAuditEvent': '审计事件',

    // ── Empty / error / loading ────────────────────────────────
    'view.webhooks.empty': '暂无 Webhook',
    'view.webhooks.emptyDesc': '新建一个 Webhook 以在事件触发时接收外部通知。',
    'view.webhooks.loadError': '加载 Webhook 失败',
    'view.webhooks.saveError': '保存 Webhook 失败',
    'view.webhooks.deleteError': '删除 Webhook 失败',
    'view.webhooks.testError': '测试 Webhook 失败',
    'view.webhooks.historyError': '加载触发历史失败',
    'view.webhooks.loading': '加载中…',

    // ── Test result ─────────────────────────────────────────────
    'view.webhooks.testSuccess': 'Webhook 测试成功（{ms}ms）',
    'view.webhooks.testFailed': 'Webhook 测试失败：{error}',
    'view.webhooks.testNoListener': '测试已发送（无监听者响应）',

    // ── Trigger history ─────────────────────────────────────────
    'view.webhooks.historyTitle': '触发历史',
    'view.webhooks.historyColTime': '时间',
    'view.webhooks.historyColEvent': '事件',
    'view.webhooks.historyColStatus': '状态',
    'view.webhooks.historyColLatency': '延迟',
    'view.webhooks.historyColResponse': '响应',
    'view.webhooks.historyEmpty': '暂无触发记录',
    'view.webhooks.deliverySuccess': '已投递',
    'view.webhooks.deliveryFailed': '失败',
    'view.webhooks.deliveryRetry': '重试中',

    // ── Validation ──────────────────────────────────────────────
    'view.webhooks.validateNameRequired': '名称为必填项',
    'view.webhooks.validateUrlRequired': 'URL 为必填项',
    'view.webhooks.validateUrlInvalid': 'URL 必须以 http:// 或 https:// 开头',
    'view.webhooks.validateEventsRequired': '至少选择一个事件类型',
    'view.webhooks.validateSecretRequired': '认证方式为 HMAC 时密钥为必填项',
    'view.webhooks.validateTokenRequired': '认证方式为 Bearer 时 token 为必填项',

    // ── Toast ───────────────────────────────────────────────────
    'view.webhooks.saved': 'Webhook 已保存',
    'view.webhooks.deleted': 'Webhook 已删除',
    'view.webhooks.never': '从未',
  },
};