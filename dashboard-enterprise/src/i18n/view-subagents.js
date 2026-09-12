export const messages = {
  en: {
    'view.subagents.title': 'Subagent Manager',
    'view.subagents.sub': 'Spawn, monitor and manage child agent lifecycles.',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.subagents.tab.active': 'Active',
    'view.subagents.tab.spawn': 'Spawn',

    // ── Active list ───────────────────────────────────────────────
    'view.subagents.active.title': 'Active Subagents',
    'view.subagents.active.empty': 'No active subagents',
    'view.subagents.active.emptyHint': 'Spawn a subagent to delegate a task to a child agent.',
    'view.subagents.active.failedLoad': 'Failed to load subagents',
    'view.subagents.active.refresh': 'Refresh',

    // ── Columns ───────────────────────────────────────────────────
    'view.subagents.col.agentId': 'Agent ID',
    'view.subagents.col.agent': 'Agent',
    'view.subagents.col.task': 'Task',
    'view.subagents.col.model': 'Model',
    'view.subagents.col.status': 'Status',
    'view.subagents.col.actions': 'Actions',

    // ── Status ────────────────────────────────────────────────────
    'view.subagents.status.running': 'Running',
    'view.subagents.status.done': 'Done',
    'view.subagents.status.error': 'Error',
    'view.subagents.status.cancelled': 'Cancelled',
    'view.subagents.status.unknown': 'Unknown',

    // ── Spawn form ────────────────────────────────────────────────
    'view.subagents.spawn.title': 'Spawn Subagent',
    'view.subagents.spawn.hint': 'Delegate a task to a child agent. The parent agent can wait for or cancel it later.',
    'view.subagents.spawn.fieldAgent': 'Agent name',
    'view.subagents.spawn.fieldAgentHint': 'e.g. coder, reviewer, researcher',
    'view.subagents.spawn.fieldTask': 'Task',
    'view.subagents.spawn.fieldTaskHint': 'Describe the task for the subagent to execute',
    'view.subagents.spawn.fieldContext': 'Context (optional)',
    'view.subagents.spawn.fieldContextHint': 'Additional context passed to the subagent',
    'view.subagents.spawn.fieldModel': 'Model (optional)',
    'view.subagents.spawn.fieldModelHint': 'Override the model used by the subagent',
    'view.subagents.spawn.submit': 'Spawn',
    'view.subagents.spawn.spawning': 'Spawning…',
    'view.subagents.spawn.validateAgent': 'Agent name is required',
    'view.subagents.spawn.validateTask': 'Task is required',
    'view.subagents.spawn.success': 'Subagent spawned: {id}',
    'view.subagents.spawn.failed': 'Failed to spawn subagent',

    // ── Actions ───────────────────────────────────────────────────
    'view.subagents.action.wait': 'Wait',
    'view.subagents.action.cancel': 'Cancel',
    'view.subagents.action.transcript': 'Transcript',
    'view.subagents.action.waiting': 'Waiting…',
    'view.subagents.action.cancelling': 'Cancelling…',

    // ── Wait modal ────────────────────────────────────────────────
    'view.subagents.wait.title': 'Wait for Subagent',
    'view.subagents.wait.timeout': 'Timeout (seconds)',
    'view.subagents.wait.submit': 'Wait',
    'view.subagents.wait.success': 'Subagent completed',
    'view.subagents.wait.failed': 'Wait failed',
    'view.subagents.wait.notFound': 'Subagent not found or timed out',
    'view.subagents.wait.result': 'Result',

    // ── Transcript drawer ─────────────────────────────────────────
    'view.subagents.transcript.title': 'Live Transcript',
    'view.subagents.transcript.empty': 'No transcript available',
    'view.subagents.transcript.failedLoad': 'Failed to load transcript',

    // ── Cancel ────────────────────────────────────────────────────
    'view.subagents.cancel.success': 'Subagent cancelled',
    'view.subagents.cancel.failed': 'Failed to cancel subagent',
    'view.subagents.cancel.confirm': 'Cancel this subagent? Its task will be interrupted.',
  },

  zh: {
    'view.subagents.title': '子代理管理',
    'view.subagents.sub': '生成、监控和管理子代理生命周期。',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.subagents.tab.active': '活跃',
    'view.subagents.tab.spawn': '生成',

    // ── 活跃列表 ──────────────────────────────────────────────────
    'view.subagents.active.title': '活跃子代理',
    'view.subagents.active.empty': '暂无活跃子代理',
    'view.subagents.active.emptyHint': '生成一个子代理以将任务委派给子代理。',
    'view.subagents.active.failedLoad': '加载子代理失败',
    'view.subagents.active.refresh': '刷新',

    // ── 列 ────────────────────────────────────────────────────────
    'view.subagents.col.agentId': '代理 ID',
    'view.subagents.col.agent': '代理',
    'view.subagents.col.task': '任务',
    'view.subagents.col.model': '模型',
    'view.subagents.col.status': '状态',
    'view.subagents.col.actions': '操作',

    // ── 状态 ──────────────────────────────────────────────────────
    'view.subagents.status.running': '运行中',
    'view.subagents.status.done': '已完成',
    'view.subagents.status.error': '错误',
    'view.subagents.status.cancelled': '已取消',
    'view.subagents.status.unknown': '未知',

    // ── 生成表单 ──────────────────────────────────────────────────
    'view.subagents.spawn.title': '生成子代理',
    'view.subagents.spawn.hint': '将任务委派给子代理。父代理可稍后等待或取消它。',
    'view.subagents.spawn.fieldAgent': '代理名称',
    'view.subagents.spawn.fieldAgentHint': '例如：coder、reviewer、researcher',
    'view.subagents.spawn.fieldTask': '任务',
    'view.subagents.spawn.fieldTaskHint': '描述子代理要执行的任务',
    'view.subagents.spawn.fieldContext': '上下文（可选）',
    'view.subagents.spawn.fieldContextHint': '传递给子代理的附加上下文',
    'view.subagents.spawn.fieldModel': '模型（可选）',
    'view.subagents.spawn.fieldModelHint': '覆盖子代理使用的模型',
    'view.subagents.spawn.submit': '生成',
    'view.subagents.spawn.spawning': '生成中…',
    'view.subagents.spawn.validateAgent': '代理名称为必填项',
    'view.subagents.spawn.validateTask': '任务为必填项',
    'view.subagents.spawn.success': '子代理已生成：{id}',
    'view.subagents.spawn.failed': '生成子代理失败',

    // ── 操作 ──────────────────────────────────────────────────────
    'view.subagents.action.wait': '等待',
    'view.subagents.action.cancel': '取消',
    'view.subagents.action.transcript': '转录',
    'view.subagents.action.waiting': '等待中…',
    'view.subagents.action.cancelling': '取消中…',

    // ── 等待弹窗 ──────────────────────────────────────────────────
    'view.subagents.wait.title': '等待子代理',
    'view.subagents.wait.timeout': '超时（秒）',
    'view.subagents.wait.submit': '等待',
    'view.subagents.wait.success': '子代理已完成',
    'view.subagents.wait.failed': '等待失败',
    'view.subagents.wait.notFound': '子代理未找到或已超时',
    'view.subagents.wait.result': '结果',

    // ── 转录抽屉 ──────────────────────────────────────────────────
    'view.subagents.transcript.title': '实时转录',
    'view.subagents.transcript.empty': '暂无转录',
    'view.subagents.transcript.failedLoad': '加载转录失败',

    // ── 取消 ──────────────────────────────────────────────────────
    'view.subagents.cancel.success': '子代理已取消',
    'view.subagents.cancel.failed': '取消子代理失败',
    'view.subagents.cancel.confirm': '确认取消该子代理？其任务将被中断。',
  },
};