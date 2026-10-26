export const messages = {
  en: {
    'view.agentVersions.title': 'Agent Versions',
    'view.agentVersions.subtitle':
      'Version history for agent configs — activate a version to make it the live one.',
    'view.agentVersions.listTitle': 'Versions',

    // ── Filters ───────────────────────────────────────────────────
    'view.agentVersions.filter.agentPlaceholder': 'Filter by agent id',
    'view.agentVersions.filter.allStatuses': 'All statuses',

    // ── Columns ───────────────────────────────────────────────────
    'view.agentVersions.col.version': 'Version',
    'view.agentVersions.col.agent': 'Agent',
    'view.agentVersions.col.status': 'Status',
    'view.agentVersions.col.created': 'Created',
    'view.agentVersions.col.actions': 'Actions',

    // ── Actions ───────────────────────────────────────────────────
    'view.agentVersions.action.activate': 'Activate',
    'view.agentVersions.action.canary': 'Canary',
    'view.agentVersions.action.rollback': 'Rollback',
    'view.agentVersions.activeNow': 'live',
    'view.agentVersions.pager': '{total} versions · page {page}',
    'view.agentVersions.prev': 'Prev',
    'view.agentVersions.next': 'Next',

    // ── States ────────────────────────────────────────────────────
    'view.agentVersions.empty': 'No versions yet.',
    'view.agentVersions.loadFailed': 'Failed to load versions.',
    'view.agentVersions.activateFailed': 'Failed to activate this version.',
    // ── Rollback ──────────────────────────────────────────────────
    'view.agentVersions.rollback.title': 'Roll back',
    'view.agentVersions.rollback.body':
      'Version {version} will be retired and the target version activated instead.',
    'view.agentVersions.rollback.target': 'Target version',
    'view.agentVersions.rollback.targetPlaceholder': 'Select a version',
    'view.agentVersions.rollback.targetRequired': 'Pick a target version first.',
    'view.agentVersions.rollback.reason': 'Reason (optional)',
    'view.agentVersions.rollback.reasonHint': 'e.g. regression in 2.0.0',
    'view.agentVersions.rollback.submit': 'Roll back',
    'view.agentVersions.rollback.done': 'Rolled back.',
    'view.agentVersions.rollback.failed': 'Rollback failed.',

    // ── Canary ────────────────────────────────────────────────────
    'view.agentVersions.canary.title': 'Configure canary',
    'view.agentVersions.canary.body': 'Release version {version} to a subset of traffic.',
    'view.agentVersions.canary.percentage': 'Percentage (0-100)',
    'view.agentVersions.canary.percentageInvalid': 'Percentage must be between 0 and 100.',
    'view.agentVersions.canary.strategy': 'Strategy',
    'view.agentVersions.canary.groups': 'User groups (comma separated)',
    'view.agentVersions.canary.groupsHint': 'e.g. internal, beta',
    'view.agentVersions.canary.duration': 'Duration (seconds, optional)',
    'view.agentVersions.canary.durationHint': 'Leave 0 for no time limit',
    'view.agentVersions.canary.submit': 'Start canary',
    'view.agentVersions.canary.done': 'Canary configured.',
    'view.agentVersions.canary.failed': 'Failed to configure canary.',
  },
  zh: {
    'view.agentVersions.title': 'Agent 版本',
    'view.agentVersions.subtitle': 'Agent 配置的版本历史 —— 激活某个版本使其成为当前生效版本。',
    'view.agentVersions.listTitle': '版本列表',

    // ── Filters ───────────────────────────────────────────────────
    'view.agentVersions.filter.agentPlaceholder': '按 agent id 过滤',
    'view.agentVersions.filter.allStatuses': '全部状态',

    // ── Columns ───────────────────────────────────────────────────
    'view.agentVersions.col.version': '版本',
    'view.agentVersions.col.agent': 'Agent',
    'view.agentVersions.col.status': '状态',
    'view.agentVersions.col.created': '创建时间',
    'view.agentVersions.col.actions': '操作',

    // ── Actions ───────────────────────────────────────────────────
    'view.agentVersions.action.activate': '激活',
    'view.agentVersions.action.canary': '灰度',
    'view.agentVersions.action.rollback': '回滚',
    'view.agentVersions.activeNow': '生效中',
    'view.agentVersions.pager': '共 {total} 个版本 · 第 {page} 页',
    'view.agentVersions.prev': '上一页',
    'view.agentVersions.next': '下一页',

    // ── States ────────────────────────────────────────────────────
    'view.agentVersions.empty': '暂无版本。',
    'view.agentVersions.loadFailed': '版本列表加载失败。',
    'view.agentVersions.activateFailed': '激活该版本失败。',
    // ── 回滚 ──────────────────────────────────────────────────────
    'view.agentVersions.rollback.title': '回滚',
    'view.agentVersions.rollback.body':
      '版本 {version} 将被置为 retired，选中的目标版本将被激活。',
    'view.agentVersions.rollback.target': '目标版本',
    'view.agentVersions.rollback.targetPlaceholder': '请选择版本',
    'view.agentVersions.rollback.targetRequired': '请先选择目标版本。',
    'view.agentVersions.rollback.reason': '原因（可选）',
    'view.agentVersions.rollback.reasonHint': '例如：2.0.0 出现回归',
    'view.agentVersions.rollback.submit': '确认回滚',
    'view.agentVersions.rollback.done': '已回滚。',
    'view.agentVersions.rollback.failed': '回滚失败。',

    // ── 灰度 ──────────────────────────────────────────────────────
    'view.agentVersions.canary.title': '配置灰度',
    'view.agentVersions.canary.body': '把版本 {version} 发布给一部分流量。',
    'view.agentVersions.canary.percentage': '灰度百分比（0-100）',
    'view.agentVersions.canary.percentageInvalid': '百分比必须在 0 到 100 之间。',
    'view.agentVersions.canary.strategy': '灰度策略',
    'view.agentVersions.canary.groups': '目标用户组（逗号分隔）',
    'view.agentVersions.canary.groupsHint': '例如：internal, beta',
    'view.agentVersions.canary.duration': '持续时间（秒，可选）',
    'view.agentVersions.canary.durationHint': '填 0 表示不限时',
    'view.agentVersions.canary.submit': '开始灰度',
    'view.agentVersions.canary.done': '灰度已配置。',
    'view.agentVersions.canary.failed': '灰度配置失败。',
  },
};
