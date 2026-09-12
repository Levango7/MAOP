/**
 * i18n messages for Alert Rules management page (src/views/AlertRules.vue).
 *
 * Keys are namespaced under `view.alerts.*`. Auto-collected by
 * src/i18n/index.js via import.meta.glob('./view-*.js').
 */
export const messages = {
  en: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.alerts': 'Alert Rules',
    'nav.alerts.subtitle': 'Alert rules, thresholds and notification channels',
    'view.alerts.enterprise': 'Enterprise',
    'view.alerts.subtitle': 'Alert rules, thresholds and notification channels',
    'view.alerts.createBtn': 'New Rule',
    'view.alerts.refreshBtn': 'Refresh',
    'view.alerts.hint': 'Rules evaluate metrics against thresholds and notify via email, webhook or in-app channels.',

    // ── Table columns ───────────────────────────────────────────
    'view.alerts.colName': 'Name',
    'view.alerts.colMetric': 'Metric',
    'view.alerts.colCondition': 'Condition',
    'view.alerts.colThreshold': 'Threshold',
    'view.alerts.colChannel': 'Channel',
    'view.alerts.colStatus': 'Status',
    'view.alerts.colActions': 'Actions',

    // ── Status ──────────────────────────────────────────────────
    'view.alerts.statusEnabled': 'Enabled',
    'view.alerts.statusDisabled': 'Disabled',

    // ── Conditions ──────────────────────────────────────────────
    'view.alerts.condGt': '>',
    'view.alerts.condLt': '<',
    'view.alerts.condGte': '≥',
    'view.alerts.condLte': '≤',
    'view.alerts.condEq': '=',

    // ── Notification channels ───────────────────────────────────
    'view.alerts.channelEmail': 'Email',
    'view.alerts.channelWebhook': 'Webhook',
    'view.alerts.channelInline': 'In-App',

    // ── Actions ─────────────────────────────────────────────────
    'view.alerts.actionEdit': 'Edit',
    'view.alerts.actionDelete': 'Delete',
    'view.alerts.actionHistory': 'History',
    'view.alerts.actionToggle': 'Toggle',
    'view.alerts.deleteConfirm': 'Delete alert rule "{name}"? This action cannot be undone.',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.alerts.dialogTitleCreate': 'Create Alert Rule',
    'view.alerts.dialogTitleEdit': 'Edit Alert Rule',
    'view.alerts.fieldName': 'Rule Name',
    'view.alerts.fieldMetric': 'Metric',
    'view.alerts.fieldCondition': 'Condition',
    'view.alerts.fieldThreshold': 'Threshold',
    'view.alerts.fieldChannel': 'Notification Channel',
    'view.alerts.fieldWebhook': 'Webhook',
    'view.alerts.fieldEmail': 'Email Address',
    'view.alerts.fieldCooldown': 'Cooldown (s)',
    'view.alerts.fieldEnabled': 'Enabled',
    'view.alerts.fieldDescription': 'Description',
    'view.alerts.btnSave': 'Save',
    'view.alerts.btnCancel': 'Cancel',
    'view.alerts.saving': 'Saving…',
    'view.alerts.placeholderEmail': 'ops@example.com',
    'view.alerts.placeholderThreshold': '0',

    // ── Metrics ─────────────────────────────────────────────────
    'view.alerts.metricCpuUsage': 'CPU Usage (%)',
    'view.alerts.metricMemoryUsage': 'Memory Usage (%)',
    'view.alerts.metricLatency': 'Latency (ms)',
    'view.alerts.metricErrorRate': 'Error Rate (%)',
    'view.alerts.metricCostDaily': 'Daily Cost',
    'view.alerts.metricTaskQueue': 'Task Queue Depth',
    'view.alerts.metricAgentOffline': 'Offline Agents',

    // ── Empty / error / loading ────────────────────────────────
    'view.alerts.empty': 'No alert rules configured',
    'view.alerts.emptyDesc': 'Create a rule to get notified when metrics cross thresholds.',
    'view.alerts.loadError': 'Failed to load alert rules',
    'view.alerts.saveError': 'Failed to save alert rule',
    'view.alerts.deleteError': 'Failed to delete alert rule',
    'view.alerts.historyError': 'Failed to load alert history',
    'view.alerts.loading': 'Loading…',

    // ── Alert history ───────────────────────────────────────────
    'view.alerts.historyTitle': 'Alert History',
    'view.alerts.historyColTime': 'Time',
    'view.alerts.historyColRule': 'Rule',
    'view.alerts.historyColValue': 'Value',
    'view.alerts.historyColChannel': 'Channel',
    'view.alerts.historyColStatus': 'Status',
    'view.alerts.historyEmpty': 'No alert records',
    'view.alerts.alertFired': 'Fired',
    'view.alerts.alertResolved': 'Resolved',
    'view.alerts.alertAcknowledged': 'Acknowledged',

    // ── Stats ───────────────────────────────────────────────────
    'view.alerts.statTotalRules': 'Total Rules',
    'view.alerts.statActiveRules': 'Active Rules',
    'view.alerts.statTriggeredToday': 'Triggered Today',
    'view.alerts.statFalsePositiveRate': 'False Positive Rate',

    // ── Validation ──────────────────────────────────────────────
    'view.alerts.validateNameRequired': 'Name is required',
    'view.alerts.validateThresholdRequired': 'Threshold is required',
    'view.alerts.validateThresholdNumber': 'Threshold must be a number',
    'view.alerts.validateEmailRequired': 'Email is required when channel is Email',
    'view.alerts.validateEmailInvalid': 'Email format is invalid',
    'view.alerts.validateWebhookRequired': 'Webhook is required when channel is Webhook',

    // ── Toast ───────────────────────────────────────────────────
    'view.alerts.saved': 'Alert rule saved',
    'view.alerts.deleted': 'Alert rule deleted',
    'view.alerts.toggled': 'Alert rule {state}',
  },
  zh: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.alerts': '告警规则',
    'nav.alerts.subtitle': '告警规则、阈值与通知渠道',
    'view.alerts.enterprise': '企业版',
    'view.alerts.subtitle': '告警规则、阈值与通知渠道',
    'view.alerts.createBtn': '新建规则',
    'view.alerts.refreshBtn': '刷新',
    'view.alerts.hint': '规则将指标与阈值进行比较，并通过邮件、Webhook 或应用内通知渠道发送告警。',

    // ── Table columns ───────────────────────────────────────────
    'view.alerts.colName': '名称',
    'view.alerts.colMetric': '指标',
    'view.alerts.colCondition': '条件',
    'view.alerts.colThreshold': '阈值',
    'view.alerts.colChannel': '通知渠道',
    'view.alerts.colStatus': '状态',
    'view.alerts.colActions': '操作',

    // ── Status ──────────────────────────────────────────────────
    'view.alerts.statusEnabled': '已启用',
    'view.alerts.statusDisabled': '已禁用',

    // ── Conditions ──────────────────────────────────────────────
    'view.alerts.condGt': '>',
    'view.alerts.condLt': '<',
    'view.alerts.condGte': '≥',
    'view.alerts.condLte': '≤',
    'view.alerts.condEq': '=',

    // ── Notification channels ───────────────────────────────────
    'view.alerts.channelEmail': '邮件',
    'view.alerts.channelWebhook': 'Webhook',
    'view.alerts.channelInline': '应用内',

    // ── Actions ─────────────────────────────────────────────────
    'view.alerts.actionEdit': '编辑',
    'view.alerts.actionDelete': '删除',
    'view.alerts.actionHistory': '历史',
    'view.alerts.actionToggle': '切换状态',
    'view.alerts.deleteConfirm': '确认删除告警规则 "{name}"？此操作不可撤销。',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.alerts.dialogTitleCreate': '新建告警规则',
    'view.alerts.dialogTitleEdit': '编辑告警规则',
    'view.alerts.fieldName': '规则名称',
    'view.alerts.fieldMetric': '指标',
    'view.alerts.fieldCondition': '条件',
    'view.alerts.fieldThreshold': '阈值',
    'view.alerts.fieldChannel': '通知渠道',
    'view.alerts.fieldWebhook': 'Webhook',
    'view.alerts.fieldEmail': '邮箱地址',
    'view.alerts.fieldCooldown': '冷却时间 (秒)',
    'view.alerts.fieldEnabled': '启用',
    'view.alerts.fieldDescription': '描述',
    'view.alerts.btnSave': '保存',
    'view.alerts.btnCancel': '取消',
    'view.alerts.saving': '保存中…',
    'view.alerts.placeholderEmail': 'ops@example.com',
    'view.alerts.placeholderThreshold': '0',

    // ── Metrics ─────────────────────────────────────────────────
    'view.alerts.metricCpuUsage': 'CPU 使用率 (%)',
    'view.alerts.metricMemoryUsage': '内存使用率 (%)',
    'view.alerts.metricLatency': '延迟 (ms)',
    'view.alerts.metricErrorRate': '错误率 (%)',
    'view.alerts.metricCostDaily': '日消费',
    'view.alerts.metricTaskQueue': '任务队列深度',
    'view.alerts.metricAgentOffline': '离线智能体数',

    // ── Empty / error / loading ────────────────────────────────
    'view.alerts.empty': '暂无告警规则',
    'view.alerts.emptyDesc': '新建规则以在指标越过阈值时收到通知。',
    'view.alerts.loadError': '加载告警规则失败',
    'view.alerts.saveError': '保存告警规则失败',
    'view.alerts.deleteError': '删除告警规则失败',
    'view.alerts.historyError': '加载告警历史失败',
    'view.alerts.loading': '加载中…',

    // ── Alert history ───────────────────────────────────────────
    'view.alerts.historyTitle': '告警历史',
    'view.alerts.historyColTime': '时间',
    'view.alerts.historyColRule': '规则',
    'view.alerts.historyColValue': '值',
    'view.alerts.historyColChannel': '渠道',
    'view.alerts.historyColStatus': '状态',
    'view.alerts.historyEmpty': '暂无告警记录',
    'view.alerts.alertFired': '已触发',
    'view.alerts.alertResolved': '已恢复',
    'view.alerts.alertAcknowledged': '已确认',

    // ── Stats ───────────────────────────────────────────────────
    'view.alerts.statTotalRules': '规则总数',
    'view.alerts.statActiveRules': '启用规则',
    'view.alerts.statTriggeredToday': '今日触发',
    'view.alerts.statFalsePositiveRate': '误报率',

    // ── Validation ──────────────────────────────────────────────
    'view.alerts.validateNameRequired': '名称为必填项',
    'view.alerts.validateThresholdRequired': '阈值为必填项',
    'view.alerts.validateThresholdNumber': '阈值必须为数字',
    'view.alerts.validateEmailRequired': '通知渠道为邮件时邮箱为必填项',
    'view.alerts.validateEmailInvalid': '邮箱格式不正确',
    'view.alerts.validateWebhookRequired': '通知渠道为 Webhook 时需选择 Webhook',

    // ── Toast ───────────────────────────────────────────────────
    'view.alerts.saved': '告警规则已保存',
    'view.alerts.deleted': '告警规则已删除',
    'view.alerts.toggled': '告警规则已{state}',
  },
};