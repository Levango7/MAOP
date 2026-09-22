export const messages = {
  en: {
    'view.agentRegistry.title': 'Agent Registry',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.agentRegistry.tab.agents': 'Agents',
    'view.agentRegistry.tab.capabilities': 'Capabilities',
    'view.agentRegistry.tab.fallback': 'Fallback Chains',

    // ── Columns ───────────────────────────────────────────────────
    'view.agentRegistry.col.name': 'Name',
    'view.agentRegistry.col.vendor': 'Vendor',
    'view.agentRegistry.col.adapter': 'Adapter',
    'view.agentRegistry.col.capabilities': 'Capabilities',
    'view.agentRegistry.col.billing': 'Billing',
    'view.agentRegistry.col.auth': 'Auth',
    'view.agentRegistry.col.status': 'Status',
    'view.agentRegistry.col.actions': 'Actions',

    // ── Actions ───────────────────────────────────────────────────
    'view.agentRegistry.action.refresh': 'Refresh',
    'view.agentRegistry.action.register': 'Register Agent',
    'view.agentRegistry.action.edit': 'Edit',
    'view.agentRegistry.action.delete': 'Delete',
    'view.agentRegistry.action.enable': 'Enable',
    'view.agentRegistry.action.disable': 'Disable',
    'view.agentRegistry.action.search': 'Search',
    'view.agentRegistry.action.filterCapability': 'Filter by capability',

    // ── Form ──────────────────────────────────────────────────────
    'view.agentRegistry.form.title': 'Register New Agent',
    'view.agentRegistry.form.editTitle': 'Edit Agent',
    'view.agentRegistry.form.name': 'Name',
    'view.agentRegistry.form.nameHint': 'Unique identifier (e.g. claude-code)',
    'view.agentRegistry.form.displayName': 'Display Name',
    'view.agentRegistry.form.displayNameHint': 'Human-friendly label',
    'view.agentRegistry.form.vendor': 'Vendor',
    'view.agentRegistry.form.vendorHint': 'Provider name (e.g. Anthropic)',
    'view.agentRegistry.form.adapterType': 'Adapter Type',
    'view.agentRegistry.form.capabilities': 'Capabilities',
    'view.agentRegistry.form.billingModel': 'Billing Model',
    'view.agentRegistry.form.authMethod': 'Auth Method',
    'view.agentRegistry.form.maxConcurrent': 'Max Concurrent',
    'view.agentRegistry.form.timeout': 'Timeout (s)',
    'view.agentRegistry.form.retryCount': 'Retry Count',
    'view.agentRegistry.form.fallbackAgents': 'Fallback Agents',
    'view.agentRegistry.form.fallbackHint': 'Ordered list of agents to try on failure',
    'view.agentRegistry.form.submit': 'Submit',
    'view.agentRegistry.form.cancel': 'Cancel',
    'view.agentRegistry.form.saving': 'Saving…',
    'view.agentRegistry.form.validateName': 'Name is required',
    'view.agentRegistry.form.success': 'Agent registered',
    'view.agentRegistry.form.updateSuccess': 'Agent updated',
    'view.agentRegistry.form.failed': 'Failed to save agent',

    // ── Capability labels ─────────────────────────────────────────
    'view.agentRegistry.capability.code_generation': 'Code Generation',
    'view.agentRegistry.capability.code_review': 'Code Review',
    'view.agentRegistry.capability.chat': 'Chat',
    'view.agentRegistry.capability.reasoning': 'Reasoning',
    'view.agentRegistry.capability.multimodal': 'Multimodal',
    'view.agentRegistry.capability.long_context': 'Long Context',
    'view.agentRegistry.capability.tool_use': 'Tool Use',
    'view.agentRegistry.capability.web_search': 'Web Search',
    'view.agentRegistry.capability.file_edit': 'File Edit',
    'view.agentRegistry.capability.terminal': 'Terminal',

    // ── Billing labels ────────────────────────────────────────────
    'view.agentRegistry.billing.per_token': 'Per Token',
    'view.agentRegistry.billing.per_call': 'Per Call',
    'view.agentRegistry.billing.subscription': 'Subscription',
    'view.agentRegistry.billing.credit': 'Credit',
    'view.agentRegistry.billing.blackbox': 'Blackbox',
    'view.agentRegistry.billing.free': 'Free',

    // ── Auth labels ───────────────────────────────────────────────
    'view.agentRegistry.auth.api_key': 'API Key',
    'view.agentRegistry.auth.oauth': 'OAuth',
    'view.agentRegistry.auth.username_password': 'Username / Password',
    'view.agentRegistry.auth.license': 'License',
    'view.agentRegistry.auth.none': 'None',

    // ── Status ────────────────────────────────────────────────────
    'view.agentRegistry.status.enabled': 'Enabled',
    'view.agentRegistry.status.disabled': 'Disabled',
    'view.agentRegistry.status.healthy': 'Healthy',
    'view.agentRegistry.status.unhealthy': 'Unhealthy',
    'view.agentRegistry.status.unknown': 'Unknown',

    // ── Empty / error ─────────────────────────────────────────────
    'view.agentRegistry.empty': 'No agents registered',
    'view.agentRegistry.emptyHint': 'Register an agent to add it to the catalog.',
    'view.agentRegistry.failedLoad': 'Failed to load agents',
    'view.agentRegistry.confirmDelete': 'Are you sure you want to delete this agent?',

    // ── Capabilities tab ──────────────────────────────────────────
    'view.agentRegistry.cap.title': 'Capability Map',
    'view.agentRegistry.cap.empty': 'No capabilities found',
    'view.agentRegistry.cap.emptyHint': 'Register agents with capabilities to see them here.',

    'view.agentRegistry.cap.failedLoad': 'Failed to load capabilities',

    // ── Fallback tab ──────────────────────────────────────────────
    'view.agentRegistry.fallback.title': 'Fallback Chains',
    'view.agentRegistry.fallback.empty': 'No fallback chains configured',
    'view.agentRegistry.fallback.emptyHint': 'Add fallback agents when registering or editing an agent.',
    'view.agentRegistry.fallback.primary': 'Primary',
    'view.agentRegistry.fallback.none': 'No fallback',
    'view.agentRegistry.fallback.addAgent': 'Add fallback',
    'view.agentRegistry.fallback.remove': 'Remove',
    'view.agentRegistry.fallback.save': 'Save Chain',
    'view.agentRegistry.fallback.saving': 'Saving…',
    'view.agentRegistry.fallback.saved': 'Fallback chain saved',
    'view.agentRegistry.fallback.saveFailed': 'Failed to save fallback chain',

    // ── Stats ─────────────────────────────────────────────────────
    'view.agentRegistry.stats.total': 'Total Agents',
    'view.agentRegistry.stats.enabled': 'Enabled',
    'view.agentRegistry.stats.healthy': 'Healthy',
    'view.agentRegistry.stats.capabilities': 'Capabilities',

    // ── Toast ─────────────────────────────────────────────────────
    'view.agentRegistry.toast.deleteSuccess': 'Agent deleted',
    'view.agentRegistry.toast.deleteFailed': 'Failed to delete agent',
    'view.agentRegistry.toast.enableSuccess': 'Agent enabled',
    'view.agentRegistry.toast.disableSuccess': 'Agent disabled',
    'view.agentRegistry.toast.toggleFailed': 'Failed to toggle agent',

  },

  zh: {
    'view.agentRegistry.title': 'Agent 注册中心',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.agentRegistry.tab.agents': 'Agent 列表',
    'view.agentRegistry.tab.capabilities': '能力视图',
    'view.agentRegistry.tab.fallback': '降级链',

    // ── 列 ────────────────────────────────────────────────────────
    'view.agentRegistry.col.name': '名称',
    'view.agentRegistry.col.vendor': '供应商',
    'view.agentRegistry.col.adapter': '适配器',
    'view.agentRegistry.col.capabilities': '能力',
    'view.agentRegistry.col.billing': '计费',
    'view.agentRegistry.col.auth': '授权',
    'view.agentRegistry.col.status': '状态',
    'view.agentRegistry.col.actions': '操作',

    // ── 操作 ──────────────────────────────────────────────────────
    'view.agentRegistry.action.refresh': '刷新',
    'view.agentRegistry.action.register': '注册 Agent',
    'view.agentRegistry.action.edit': '编辑',
    'view.agentRegistry.action.delete': '删除',
    'view.agentRegistry.action.enable': '启用',
    'view.agentRegistry.action.disable': '禁用',
    'view.agentRegistry.action.search': '搜索',
    'view.agentRegistry.action.filterCapability': '按能力过滤',

    // ── 表单 ──────────────────────────────────────────────────────
    'view.agentRegistry.form.title': '注册新 Agent',
    'view.agentRegistry.form.editTitle': '编辑 Agent',
    'view.agentRegistry.form.name': '名称',
    'view.agentRegistry.form.nameHint': '唯一标识（如 claude-code）',
    'view.agentRegistry.form.displayName': '显示名称',
    'view.agentRegistry.form.displayNameHint': '易读的标签',
    'view.agentRegistry.form.vendor': '供应商',
    'view.agentRegistry.form.vendorHint': '提供方名称（如 Anthropic）',
    'view.agentRegistry.form.adapterType': '适配器类型',
    'view.agentRegistry.form.capabilities': '能力',
    'view.agentRegistry.form.billingModel': '计费模式',
    'view.agentRegistry.form.authMethod': '授权方式',
    'view.agentRegistry.form.maxConcurrent': '最大并发',
    'view.agentRegistry.form.timeout': '超时（秒）',
    'view.agentRegistry.form.retryCount': '重试次数',
    'view.agentRegistry.form.fallbackAgents': '降级 Agent',
    'view.agentRegistry.form.fallbackHint': '失败时依次尝试的 Agent 列表',
    'view.agentRegistry.form.submit': '提交',
    'view.agentRegistry.form.cancel': '取消',
    'view.agentRegistry.form.saving': '保存中…',
    'view.agentRegistry.form.validateName': '名称为必填项',
    'view.agentRegistry.form.success': 'Agent 已注册',
    'view.agentRegistry.form.updateSuccess': 'Agent 已更新',
    'view.agentRegistry.form.failed': '保存 Agent 失败',

    // ── 能力标签 ──────────────────────────────────────────────────
    'view.agentRegistry.capability.code_generation': '代码生成',
    'view.agentRegistry.capability.code_review': '代码审查',
    'view.agentRegistry.capability.chat': '对话',
    'view.agentRegistry.capability.reasoning': '推理',
    'view.agentRegistry.capability.multimodal': '多模态',
    'view.agentRegistry.capability.long_context': '长上下文',
    'view.agentRegistry.capability.tool_use': '工具调用',
    'view.agentRegistry.capability.web_search': '网页搜索',
    'view.agentRegistry.capability.file_edit': '文件编辑',
    'view.agentRegistry.capability.terminal': '终端',

    // ── 计费标签 ──────────────────────────────────────────────────
    'view.agentRegistry.billing.per_token': '按 Token',
    'view.agentRegistry.billing.per_call': '按调用',
    'view.agentRegistry.billing.subscription': '订阅',
    'view.agentRegistry.billing.credit': '额度',
    'view.agentRegistry.billing.blackbox': '黑盒',
    'view.agentRegistry.billing.free': '免费',

    // ── 授权标签 ──────────────────────────────────────────────────
    'view.agentRegistry.auth.api_key': 'API 密钥',
    'view.agentRegistry.auth.oauth': 'OAuth',
    'view.agentRegistry.auth.username_password': '用户名 / 密码',
    'view.agentRegistry.auth.license': '许可证',
    'view.agentRegistry.auth.none': '无',

    // ── 状态 ──────────────────────────────────────────────────────
    'view.agentRegistry.status.enabled': '已启用',
    'view.agentRegistry.status.disabled': '已禁用',
    'view.agentRegistry.status.healthy': '健康',
    'view.agentRegistry.status.unhealthy': '不健康',
    'view.agentRegistry.status.unknown': '未知',

    // ── 空状态 / 错误 ─────────────────────────────────────────────
    'view.agentRegistry.empty': '暂无已注册 Agent',
    'view.agentRegistry.emptyHint': '注册一个 Agent 即可将其加入目录。',
    'view.agentRegistry.failedLoad': '加载 Agent 失败',
    'view.agentRegistry.confirmDelete': '确定要删除此 Agent 吗？',

    // ── 能力标签页 ────────────────────────────────────────────────
    'view.agentRegistry.cap.title': '能力图谱',
    'view.agentRegistry.cap.empty': '暂无能力',
    'view.agentRegistry.cap.emptyHint': '注册带能力的 Agent 即可在此查看。',

    'view.agentRegistry.cap.failedLoad': '加载能力失败',

    // ── 降级链标签页 ──────────────────────────────────────────────
    'view.agentRegistry.fallback.title': '降级链',
    'view.agentRegistry.fallback.empty': '暂无降级链配置',
    'view.agentRegistry.fallback.emptyHint': '在注册或编辑 Agent 时添加降级 Agent。',
    'view.agentRegistry.fallback.primary': '主',
    'view.agentRegistry.fallback.none': '无降级',
    'view.agentRegistry.fallback.addAgent': '添加降级',
    'view.agentRegistry.fallback.remove': '移除',
    'view.agentRegistry.fallback.save': '保存降级链',
    'view.agentRegistry.fallback.saving': '保存中…',
    'view.agentRegistry.fallback.saved': '降级链已保存',
    'view.agentRegistry.fallback.saveFailed': '保存降级链失败',

    // ── 统计 ──────────────────────────────────────────────────────
    'view.agentRegistry.stats.total': 'Agent 总数',
    'view.agentRegistry.stats.enabled': '已启用',
    'view.agentRegistry.stats.healthy': '健康',
    'view.agentRegistry.stats.capabilities': '能力数',

    // ── 提示 ──────────────────────────────────────────────────────
    'view.agentRegistry.toast.deleteSuccess': 'Agent 已删除',
    'view.agentRegistry.toast.deleteFailed': '删除 Agent 失败',
    'view.agentRegistry.toast.enableSuccess': 'Agent 已启用',
    'view.agentRegistry.toast.disableSuccess': 'Agent 已禁用',
    'view.agentRegistry.toast.toggleFailed': '切换 Agent 状态失败',

  },
};