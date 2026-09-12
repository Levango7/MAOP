export const messages = {
  en: {
    'view.scheduling.title': 'Scheduling',
    'view.scheduling.sub': 'Monitor agent health, failure rates and adaptive scheduling state.',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.scheduling.tab.agents': 'Agent Health',
    'view.scheduling.tab.config': 'Configuration',

    // ── Agent health ──────────────────────────────────────────────
    'view.scheduling.agents.title': 'Agent Health Snapshot',
    'view.scheduling.agents.empty': 'No agents tracked',
    'view.scheduling.agents.emptyHint': 'Agents appear here once they are dispatched and tracked.',
    'view.scheduling.agents.failedLoad': 'Failed to load agent health',
    'view.scheduling.agents.reset': 'Reset',
    'view.scheduling.agents.resetAll': 'Reset All',
    'view.scheduling.agents.resetConfirm': 'Reset failure detector state for this agent?',
    'view.scheduling.agents.resetAllConfirm': 'Reset failure detector state for ALL agents?',
    'view.scheduling.agents.resetSuccess': 'State reset successfully',
    'view.scheduling.agents.resetFailed': 'Failed to reset state',

    // ── Configuration ─────────────────────────────────────────────
    'view.scheduling.config.title': 'Detector Configuration',
    'view.scheduling.config.empty': 'No configuration available',
    'view.scheduling.config.emptyHint': 'Configuration appears once the detector is initialized.',
    'view.scheduling.config.windowSize': 'Window Size',
    'view.scheduling.config.failureThreshold': 'Failure Rate Threshold',
    'view.scheduling.config.timeoutThreshold': 'Timeout Threshold',
    'view.scheduling.config.recoverySuccesses': 'Recovery Successes',
    'view.scheduling.config.windowSizeHint': 'Number of recent calls evaluated per agent.',
    'view.scheduling.config.failureThresholdHint': 'Failure rate above which an agent is drained.',
    'view.scheduling.config.timeoutThresholdHint': 'Latency (seconds) above which a call is a timeout.',
    'view.scheduling.config.recoverySuccessesHint': 'Consecutive successes needed to recover.',

    // ── Columns / common ──────────────────────────────────────────
    'view.scheduling.col.agent': 'Agent',
    'view.scheduling.col.status': 'Status',
    'view.scheduling.col.failureRate': 'Failure Rate',
    'view.scheduling.col.avgLatency': 'Avg Latency',
    'view.scheduling.col.timeoutRate': 'Timeout Rate',
    'view.scheduling.col.weight': 'Weight',
    'view.scheduling.col.window': 'Window',
    'view.scheduling.col.recorded': 'Recorded',
    'view.scheduling.col.actions': 'Actions',

    // ── Agent status ──────────────────────────────────────────────
    'view.scheduling.status.normal': 'Normal',
    'view.scheduling.status.degraded': 'Degraded',
    'view.scheduling.status.drained': 'Drained',
    'view.scheduling.status.recovering': 'Recovering',
    'view.scheduling.status.unknown': 'Unknown',

    // ── Config keys ───────────────────────────────────────────────
    'view.scheduling.config.totalAgents': 'Total Agents',
  },

  zh: {
    'view.scheduling.title': '调度策略',
    'view.scheduling.sub': '监控智能体健康、失败率与自适应调度状态。',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.scheduling.tab.agents': '智能体健康',
    'view.scheduling.tab.config': '配置',

    // ── 智能体健康 ────────────────────────────────────────────────
    'view.scheduling.agents.title': '智能体健康快照',
    'view.scheduling.agents.empty': '暂无被追踪的智能体',
    'view.scheduling.agents.emptyHint': '智能体被调度并追踪后将显示在此处。',
    'view.scheduling.agents.failedLoad': '加载智能体健康状态失败',
    'view.scheduling.agents.reset': '重置',
    'view.scheduling.agents.resetAll': '全部重置',
    'view.scheduling.agents.resetConfirm': '确认重置该智能体的失败检测器状态？',
    'view.scheduling.agents.resetAllConfirm': '确认重置所有智能体的失败检测器状态？',
    'view.scheduling.agents.resetSuccess': '状态重置成功',
    'view.scheduling.agents.resetFailed': '重置状态失败',

    // ── 配置 ──────────────────────────────────────────────────────
    'view.scheduling.config.title': '检测器配置',
    'view.scheduling.config.empty': '暂无配置信息',
    'view.scheduling.config.emptyHint': '检测器初始化后配置将显示在此处。',
    'view.scheduling.config.windowSize': '窗口大小',
    'view.scheduling.config.failureThreshold': '失败率阈值',
    'view.scheduling.config.timeoutThreshold': '超时阈值',
    'view.scheduling.config.recoverySuccesses': '恢复连续成功数',
    'view.scheduling.config.windowSizeHint': '每个智能体评估的最近调用数。',
    'view.scheduling.config.failureThresholdHint': '超过该失败率则排空智能体。',
    'view.scheduling.config.timeoutThresholdHint': '延迟（秒）超过该值则计为超时。',
    'view.scheduling.config.recoverySuccessesHint': '恢复所需的连续成功次数。',

    // ── 列 / 通用 ─────────────────────────────────────────────────
    'view.scheduling.col.agent': '智能体',
    'view.scheduling.col.status': '状态',
    'view.scheduling.col.failureRate': '失败率',
    'view.scheduling.col.avgLatency': '平均延迟',
    'view.scheduling.col.timeoutRate': '超时率',
    'view.scheduling.col.weight': '权重',
    'view.scheduling.col.window': '窗口',
    'view.scheduling.col.recorded': '已记录',
    'view.scheduling.col.actions': '操作',

    // ── 智能体状态 ────────────────────────────────────────────────
    'view.scheduling.status.normal': '正常',
    'view.scheduling.status.degraded': '降级',
    'view.scheduling.status.drained': '已排空',
    'view.scheduling.status.recovering': '恢复中',
    'view.scheduling.status.unknown': '未知',

    // ── 配置键 ────────────────────────────────────────────────────
    'view.scheduling.config.totalAgents': '智能体总数',
  },
};