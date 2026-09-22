/**
 * i18n messages for Debate page (src/views/Debate.vue).
 *
 * Keys are namespaced under `view.debate.*`. Auto-collected by
 * src/i18n/index.js via import.meta.glob('./view-*.js').
 *
 * Debate page manages multi-agent adversarial discussions:
 * - Start a new debate (question + participants + config)
 * - View debate history with verdicts
 * - Inspect a single debate's verdict and trajectory
 * - Configure debate parameters (max_rounds, consensus_threshold, timeouts)
 */
export const messages = {
  en: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.debate': 'Debate',
    'nav.debate.subtitle': 'Multi-agent discussion and consensus',

    // ── Tabs ─────────────────────────────────────────────────────
    'view.debate.tab.history': 'History',
    'view.debate.tab.start': 'Start',
    'view.debate.tab.config': 'Config',

    // ── History ──────────────────────────────────────────────────
    'view.debate.history.title': 'Recent Debates',
    'view.debate.history.empty': 'No debate history yet',
    'view.debate.history.emptyHint': 'Start a debate from the Start tab to see verdicts here.',
    'view.debate.history.failedLoad': 'Failed to load debate history',
    'view.debate.history.refresh': 'Refresh',

    // ── History table columns ────────────────────────────────────
    'view.debate.col.id': 'Debate ID',
    'view.debate.col.question': 'Question',
    'view.debate.col.rounds': 'Rounds',
    'view.debate.col.consensus': 'Consensus',
    'view.debate.col.verdict': 'Verdict',
    'view.debate.col.actions': 'Actions',

    // ── Verdict labels ───────────────────────────────────────────
    'view.debate.verdict.agree': 'Agree',
    'view.debate.verdict.disagree': 'Disagree',
    'view.debate.verdict.uncertain': 'Uncertain',
    'view.debate.verdict.consensus': 'Consensus',
    'view.debate.verdict.split': 'Split',
    'view.debate.verdict.unknown': 'Unknown',

    // ── Start form ───────────────────────────────────────────────
    'view.debate.start.title': 'Start a New Debate',
    'view.debate.start.hint': 'Pose a question and select at least 3 participants. The dispatcher runs rounds until consensus or max_rounds is reached.',
    'view.debate.start.question': 'Question',
    'view.debate.start.questionPlaceholder': 'e.g. Is it safe to route codegen from agent_a to agent_b?',
    'view.debate.start.participants': 'Participants',
    'view.debate.start.participantsPlaceholder': 'agent_a, agent_b, agent_c',
    'view.debate.start.participantsHint': 'Comma-separated agent names (minimum 3).',
    'view.debate.start.routingKey': 'Routing Key',
    'view.debate.start.routingKeyPlaceholder': 'codegen',
    'view.debate.start.maxRounds': 'Max Rounds',
    'view.debate.start.consensusThreshold': 'Consensus Threshold',
    'view.debate.start.submit': 'Start Debate',
    'view.debate.start.starting': 'Starting…',
    'view.debate.start.success': 'Debate started',
    'view.debate.start.failed': 'Failed to start debate',

    // ── Config ───────────────────────────────────────────────────
    'view.debate.config.title': 'Debate Configuration',
    'view.debate.config.hint': 'Runtime parameters applied to all new debates. Changes take effect immediately.',
    'view.debate.config.maxRounds': 'Max Rounds',
    'view.debate.config.minRounds': 'Min Rounds',
    'view.debate.config.consensusThreshold': 'Consensus Threshold',
    'view.debate.config.agentTimeout': 'Agent Timeout (s)',
    'view.debate.config.roundTimeout': 'Round Timeout (s)',
    'view.debate.config.maxTokens': 'Max Debate Tokens',
    'view.debate.config.earlyExit': 'Early exit on unanimous',
    'view.debate.config.retentionDays': 'Retention (days)',
    'view.debate.config.save': 'Save Configuration',
    'view.debate.config.saving': 'Saving…',
    'view.debate.config.saved': 'Configuration saved',
    'view.debate.config.saveFailed': 'Failed to save configuration',

    // ── Verdict detail drawer ────────────────────────────────────
    'view.debate.detail.title': 'Debate Verdict',
    'view.debate.detail.question': 'Question',
    'view.debate.detail.verdict': 'Verdict',
    'view.debate.detail.consensus': 'Consensus Score',
    'view.debate.detail.rounds': 'Rounds Executed',
    'view.debate.detail.participants': 'Participants',
    'view.debate.detail.trajectory': 'Trajectory',
    'view.debate.detail.emptyTrajectory': 'No trajectory recorded',
    'view.debate.detail.round': 'Round {n}',

    // ── Validation ───────────────────────────────────────────────
    'view.debate.validate.questionRequired': 'Question is required',
    'view.debate.validate.participantsRequired': 'At least 3 participants are required',
    'view.debate.validate.consensusRange': 'Consensus threshold must be between 0 and 1',

    // ── Stats overview ───────────────────────────────────────────
  },

  zh: {
    // ── 页头与导航 ───────────────────────────────────────────────
    'nav.debate': '辩论',
    'nav.debate.subtitle': '多智能体讨论与共识',

    // ── 标签页 ───────────────────────────────────────────────────
    'view.debate.tab.history': '历史',
    'view.debate.tab.start': '发起',
    'view.debate.tab.config': '配置',

    // ── 历史 ─────────────────────────────────────────────────────
    'view.debate.history.title': '近期辩论',
    'view.debate.history.empty': '暂无辩论历史',
    'view.debate.history.emptyHint': '在「发起」标签页发起一场辩论即可在此查看裁决。',
    'view.debate.history.failedLoad': '加载辩论历史失败',
    'view.debate.history.refresh': '刷新',

    // ── 历史表格列 ───────────────────────────────────────────────
    'view.debate.col.id': '辩论 ID',
    'view.debate.col.question': '问题',
    'view.debate.col.rounds': '轮数',
    'view.debate.col.consensus': '共识度',
    'view.debate.col.verdict': '裁决',
    'view.debate.col.actions': '操作',

    // ── 裁决标签 ─────────────────────────────────────────────────
    'view.debate.verdict.agree': '同意',
    'view.debate.verdict.disagree': '反对',
    'view.debate.verdict.uncertain': '不确定',
    'view.debate.verdict.consensus': '达成共识',
    'view.debate.verdict.split': '分歧',
    'view.debate.verdict.unknown': '未知',

    // ── 发起表单 ─────────────────────────────────────────────────
    'view.debate.start.title': '发起新辩论',
    'view.debate.start.hint': '提出问题并选择至少 3 个参与者。调度器将循环执行直至达成共识或达到最大轮数。',
    'view.debate.start.question': '问题',
    'view.debate.start.questionPlaceholder': '例如：将 codegen 路由从 agent_a 切换到 agent_b 是否安全？',
    'view.debate.start.participants': '参与者',
    'view.debate.start.participantsPlaceholder': 'agent_a, agent_b, agent_c',
    'view.debate.start.participantsHint': '逗号分隔的智能体名称（不少于 3 个）。',
    'view.debate.start.routingKey': '路由键',
    'view.debate.start.routingKeyPlaceholder': 'codegen',
    'view.debate.start.maxRounds': '最大轮数',
    'view.debate.start.consensusThreshold': '共识阈值',
    'view.debate.start.submit': '发起辩论',
    'view.debate.start.starting': '发起中…',
    'view.debate.start.success': '辩论已发起',
    'view.debate.start.failed': '发起辩论失败',

    // ── 配置 ─────────────────────────────────────────────────────
    'view.debate.config.title': '辩论配置',
    'view.debate.config.hint': '运行时参数将应用于所有新辩论。修改立即生效。',
    'view.debate.config.maxRounds': '最大轮数',
    'view.debate.config.minRounds': '最小轮数',
    'view.debate.config.consensusThreshold': '共识阈值',
    'view.debate.config.agentTimeout': '智能体超时（秒）',
    'view.debate.config.roundTimeout': '轮次超时（秒）',
    'view.debate.config.maxTokens': '最大 Token 数',
    'view.debate.config.earlyExit': '全票一致时提前退出',
    'view.debate.config.retentionDays': '保留天数',
    'view.debate.config.save': '保存配置',
    'view.debate.config.saving': '保存中…',
    'view.debate.config.saved': '配置已保存',
    'view.debate.config.saveFailed': '保存配置失败',

    // ── 裁决详情抽屉 ─────────────────────────────────────────────
    'view.debate.detail.title': '辩论裁决',
    'view.debate.detail.question': '问题',
    'view.debate.detail.verdict': '裁决',
    'view.debate.detail.consensus': '共识分数',
    'view.debate.detail.rounds': '执行轮数',
    'view.debate.detail.participants': '参与者',
    'view.debate.detail.trajectory': '轨迹',
    'view.debate.detail.emptyTrajectory': '无轨迹记录',
    'view.debate.detail.round': '第 {n} 轮',

    // ── 校验 ─────────────────────────────────────────────────────
    'view.debate.validate.questionRequired': '问题为必填项',
    'view.debate.validate.participantsRequired': '至少需要 3 个参与者',
    'view.debate.validate.consensusRange': '共识阈值必须在 0 到 1 之间',

    // ── 统计概览 ─────────────────────────────────────────────────
  },
};