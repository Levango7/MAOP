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
    'view.agentVersions.activeNow': 'live',
    'view.agentVersions.pager': '{total} versions · page {page}',
    'view.agentVersions.prev': 'Prev',
    'view.agentVersions.next': 'Next',

    // ── States ────────────────────────────────────────────────────
    'view.agentVersions.empty': 'No versions yet.',
    'view.agentVersions.loadFailed': 'Failed to load versions.',
    'view.agentVersions.activateFailed': 'Failed to activate this version.',
    'view.agentVersions.rollbackNote':
      'Rollback is not exposed here yet: it needs a target-version picker. Use the API directly for now.',
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
    'view.agentVersions.activeNow': '生效中',
    'view.agentVersions.pager': '共 {total} 个版本 · 第 {page} 页',
    'view.agentVersions.prev': '上一页',
    'view.agentVersions.next': '下一页',

    // ── States ────────────────────────────────────────────────────
    'view.agentVersions.empty': '暂无版本。',
    'view.agentVersions.loadFailed': '版本列表加载失败。',
    'view.agentVersions.activateFailed': '激活该版本失败。',
    'view.agentVersions.rollbackNote':
      '回滚暂未在此页开放：它需要选择目标版本。当前请直接调用 API。',
  },
};
