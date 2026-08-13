/**
 * i18n messages for Hook management (任务199).
 *
 * Used by Settings.vue "Hook 管理" tab. Keys are namespaced under `view.hooks.*`.
 * Auto-collected by src/i18n/index.js via import.meta.glob('./view-*.js').
 */
export const messages = {
  en: {
    // ── Tab & header ────────────────────────────────────────────
    'view.settings.tabHooks': 'Hook Management',
    'view.hooks.subtitle': 'Configure event hooks for lifecycle webhooks',
    'view.hooks.createBtn': 'New Hook',
    'view.hooks.refreshBtn': 'Refresh',
    'view.hooks.hint': 'Hooks fire on lifecycle events (e.g. agent.pre_dispatch, loop.complete) and POST to the configured URL.',

    // ── Table columns ───────────────────────────────────────────
    'view.hooks.colName': 'Name',
    'view.hooks.colEvent': 'Event',
    'view.hooks.colUrl': 'URL',
    'view.hooks.colEnabled': 'Enabled',
    'view.hooks.colStatus': 'Status',
    'view.hooks.colActions': 'Actions',

    // ── Status ──────────────────────────────────────────────────
    'view.hooks.statusEnabled': 'Enabled',
    'view.hooks.statusDisabled': 'Disabled',

    // ── Actions ─────────────────────────────────────────────────
    'view.hooks.actionEdit': 'Edit',
    'view.hooks.actionDelete': 'Delete',
    'view.hooks.actionTest': 'Test',
    'view.hooks.deleteConfirm': 'Delete hook "{name}"? This action cannot be undone.',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.hooks.dialogTitleCreate': 'Create Hook',
    'view.hooks.dialogTitleEdit': 'Edit Hook',
    'view.hooks.fieldName': 'Hook Name',
    'view.hooks.fieldEvent': 'Event Type',
    'view.hooks.fieldUrl': 'Webhook URL',
    'view.hooks.fieldMethod': 'HTTP Method',
    'view.hooks.fieldHeaders': 'Custom Headers (JSON)',
    'view.hooks.fieldTimeout': 'Timeout (s)',
    'view.hooks.fieldRetry': 'Retry Count',
    'view.hooks.fieldEnabled': 'Enabled',
    'view.hooks.btnSave': 'Save',
    'view.hooks.btnCancel': 'Cancel',
    'view.hooks.placeholderUrl': 'https://example.com/webhook',
    'view.hooks.placeholderHeaders': '{"X-Token": "abc"}',
    'view.hooks.eventPlaceholder': 'Select an event…',

    // ── Empty / error / loading ────────────────────────────────
    'view.hooks.empty': 'No hooks configured',
    'view.hooks.emptyDesc': 'Create a hook to receive lifecycle event notifications.',
    'view.hooks.loadError': 'Failed to load hooks',
    'view.hooks.saveError': 'Failed to save hook',
    'view.hooks.deleteError': 'Failed to delete hook',
    'view.hooks.testError': 'Failed to test hook',
    'view.hooks.loading': 'Loading…',

    // ── Test result ─────────────────────────────────────────────
    'view.hooks.testSuccess': 'Hook test succeeded ({ms}ms)',
    'view.hooks.testFailed': 'Hook test failed: {error}',
    'view.hooks.testNoListener': 'Hook test sent (no listener responded)',

    // ── Validation ──────────────────────────────────────────────
    'view.hooks.validateNameRequired': 'Name is required',
    'view.hooks.validateEventRequired': 'Event type is required',
    'view.hooks.validateUrlRequired': 'URL is required',
    'view.hooks.validateHeadersJson': 'Headers must be valid JSON',
  },
  zh: {
    // ── Tab & header ────────────────────────────────────────────
    'view.settings.tabHooks': 'Hook 管理',
    'view.hooks.subtitle': '为生命周期事件配置 Hook',
    'view.hooks.createBtn': '新建 Hook',
    'view.hooks.refreshBtn': '刷新',
    'view.hooks.hint': 'Hook 在生命周期事件（如 agent.pre_dispatch、loop.complete）触发时向配置的 URL 发送 POST 请求。',

    // ── Table columns ───────────────────────────────────────────
    'view.hooks.colName': '名称',
    'view.hooks.colEvent': '事件',
    'view.hooks.colUrl': 'URL',
    'view.hooks.colEnabled': '启用',
    'view.hooks.colStatus': '状态',
    'view.hooks.colActions': '操作',

    // ── Status ──────────────────────────────────────────────────
    'view.hooks.statusEnabled': '已启用',
    'view.hooks.statusDisabled': '已禁用',

    // ── Actions ─────────────────────────────────────────────────
    'view.hooks.actionEdit': '编辑',
    'view.hooks.actionDelete': '删除',
    'view.hooks.actionTest': '测试',
    'view.hooks.deleteConfirm': '确认删除 Hook "{name}"？此操作不可撤销。',

    // ── Dialog: create/edit ─────────────────────────────────────
    'view.hooks.dialogTitleCreate': '新建 Hook',
    'view.hooks.dialogTitleEdit': '编辑 Hook',
    'view.hooks.fieldName': 'Hook 名称',
    'view.hooks.fieldEvent': '事件类型',
    'view.hooks.fieldUrl': 'Webhook URL',
    'view.hooks.fieldMethod': 'HTTP 方法',
    'view.hooks.fieldHeaders': '自定义请求头 (JSON)',
    'view.hooks.fieldTimeout': '超时 (秒)',
    'view.hooks.fieldRetry': '重试次数',
    'view.hooks.fieldEnabled': '启用',
    'view.hooks.btnSave': '保存',
    'view.hooks.btnCancel': '取消',
    'view.hooks.placeholderUrl': 'https://example.com/webhook',
    'view.hooks.placeholderHeaders': '{"X-Token": "abc"}',
    'view.hooks.eventPlaceholder': '选择事件…',

    // ── Empty / error / loading ────────────────────────────────
    'view.hooks.empty': '暂无 Hook',
    'view.hooks.emptyDesc': '新建一个 Hook 以接收生命周期事件通知。',
    'view.hooks.loadError': '加载 Hook 失败',
    'view.hooks.saveError': '保存 Hook 失败',
    'view.hooks.deleteError': '删除 Hook 失败',
    'view.hooks.testError': '测试 Hook 失败',
    'view.hooks.loading': '加载中…',

    // ── Test result ─────────────────────────────────────────────
    'view.hooks.testSuccess': 'Hook 测试成功（{ms}ms）',
    'view.hooks.testFailed': 'Hook 测试失败：{error}',
    'view.hooks.testNoListener': 'Hook 测试已发送（无监听者响应）',

    // ── Validation ──────────────────────────────────────────────
    'view.hooks.validateNameRequired': '名称为必填项',
    'view.hooks.validateEventRequired': '事件类型为必填项',
    'view.hooks.validateUrlRequired': 'URL 为必填项',
    'view.hooks.validateHeadersJson': '请求头必须是合法 JSON',
  },
};