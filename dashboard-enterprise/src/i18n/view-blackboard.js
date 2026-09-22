/**
 * i18n messages for Blackboard page (src/views/Blackboard.vue).
 *
 * Keys are namespaced under `view.blackboard.*`. Auto-collected by
 * src/i18n/index.js via import.meta.glob('./view-*.js').
 *
 * Blackboard page manages the shared knowledge blackboard architecture:
 * - Snapshot view of all domains and entries
 * - Per-domain entry list with contributor/confidence
 * - Write new entries (admin)
 * - Clear a domain (admin)
 * - Operation history and stats
 */
export const messages = {
  en: {
    // ── Header & nav ─────────────────────────────────────────────
    'nav.blackboard': 'Blackboard',
    'nav.blackboard.subtitle': 'Shared blackboard for agent collaboration',

    // ── Tabs ─────────────────────────────────────────────────────
    'view.blackboard.tab.snapshot': 'Snapshot',
    'view.blackboard.tab.domains': 'Domains',
    'view.blackboard.tab.history': 'History',
    'view.blackboard.tab.stats': 'Stats',

    // ── Snapshot ─────────────────────────────────────────────────
    'view.blackboard.snapshot.title': 'Blackboard Snapshot',
    'view.blackboard.snapshot.empty': 'Blackboard is empty',
    'view.blackboard.snapshot.emptyHint': 'Write an entry to a domain to populate the blackboard.',
    'view.blackboard.snapshot.failedLoad': 'Failed to load snapshot',
    'view.blackboard.snapshot.entries': 'Entries',

    // ── Domains ──────────────────────────────────────────────────
    'view.blackboard.domains.title': 'Domain Entries',
    'view.blackboard.domains.empty': 'No entries in this domain',
    'view.blackboard.domains.emptyHint': 'Select a domain above or write a new entry.',
    'view.blackboard.domains.failedLoad': 'Failed to load domains',
    'view.blackboard.domains.select': 'Select domain',
    'view.blackboard.domains.add': 'Write Entry',

    // ── Domain table columns ─────────────────────────────────────
    'view.blackboard.col.content': 'Content',
    'view.blackboard.col.contributor': 'Contributor',
    'view.blackboard.col.confidence': 'Confidence',
    'view.blackboard.col.timestamp': 'Timestamp',
    'view.blackboard.col.domain': 'Domain',

    // ── Write modal ──────────────────────────────────────────────
    'view.blackboard.write.title': 'Write Entry',
    'view.blackboard.write.domain': 'Domain',
    'view.blackboard.write.content': 'Content',
    'view.blackboard.write.contentPlaceholder': 'Knowledge content (any JSON-serializable value)',
    'view.blackboard.write.contributor': 'Contributor',
    'view.blackboard.write.contributorPlaceholder': 'agent_a',
    'view.blackboard.write.confidence': 'Confidence',
    'view.blackboard.write.metadata': 'Metadata (JSON)',
    'view.blackboard.write.metadataPlaceholder': '{"key": "value"}',
    'view.blackboard.write.submit': 'Write',
    'view.blackboard.write.writing': 'Writing…',
    'view.blackboard.write.success': 'Entry written',
    'view.blackboard.write.failed': 'Failed to write entry',

    // ── Clear domain ─────────────────────────────────────────────
    'view.blackboard.clear.confirm': 'Clear all entries in domain "{domain}"? This cannot be undone.',
    'view.blackboard.clear.success': 'Domain cleared ({count} entries removed)',
    'view.blackboard.clear.failed': 'Failed to clear domain',
    'view.blackboard.clear.action': 'Clear Domain',

    // ── History ──────────────────────────────────────────────────
    'view.blackboard.history.title': 'Operation History',
    'view.blackboard.history.empty': 'No operations recorded',
    'view.blackboard.history.failedLoad': 'Failed to load history',

    // ── Stats ────────────────────────────────────────────────────
    'view.blackboard.stats.title': 'Blackboard Statistics',
    'view.blackboard.stats.totalEntries': 'Total Entries',
    'view.blackboard.stats.activeDomains': 'Active Domains',
    'view.blackboard.stats.eventBus': 'Event Bus',
    'view.blackboard.stats.eventBusEnabled': 'Enabled',
    'view.blackboard.stats.eventBusDisabled': 'Disabled',
    'view.blackboard.stats.allowedDomains': 'Allowed Domains',
    'view.blackboard.stats.failedLoad': 'Failed to load statistics',
    'view.blackboard.stats.empty': 'No statistics available',

    // ── Validation ───────────────────────────────────────────────
    'view.blackboard.validate.domainRequired': 'Domain is required',
    'view.blackboard.validate.contentRequired': 'Content is required',
    'view.blackboard.validate.confidenceRange': 'Confidence must be between 0 and 1',
    'view.blackboard.validate.metadataInvalid': 'Metadata must be valid JSON',
  },

  zh: {
    // ── 页头与导航 ───────────────────────────────────────────────
    'nav.blackboard': '黑板',
    'nav.blackboard.subtitle': '智能体协作共享黑板',

    // ── 标签页 ───────────────────────────────────────────────────
    'view.blackboard.tab.snapshot': '快照',
    'view.blackboard.tab.domains': '域',
    'view.blackboard.tab.history': '历史',
    'view.blackboard.tab.stats': '统计',

    // ── 快照 ─────────────────────────────────────────────────────
    'view.blackboard.snapshot.title': '黑板快照',
    'view.blackboard.snapshot.empty': '黑板为空',
    'view.blackboard.snapshot.emptyHint': '向某个域写入条目即可填充黑板。',
    'view.blackboard.snapshot.failedLoad': '加载快照失败',
    'view.blackboard.snapshot.entries': '条目数',

    // ── 域 ───────────────────────────────────────────────────────
    'view.blackboard.domains.title': '域条目',
    'view.blackboard.domains.empty': '该域暂无条目',
    'view.blackboard.domains.emptyHint': '在上方选择一个域或写入新条目。',
    'view.blackboard.domains.failedLoad': '加载域失败',
    'view.blackboard.domains.select': '选择域',
    'view.blackboard.domains.add': '写入条目',

    // ── 域表格列 ─────────────────────────────────────────────────
    'view.blackboard.col.content': '内容',
    'view.blackboard.col.contributor': '贡献者',
    'view.blackboard.col.confidence': '置信度',
    'view.blackboard.col.timestamp': '时间戳',
    'view.blackboard.col.domain': '域',

    // ── 写入模态 ─────────────────────────────────────────────────
    'view.blackboard.write.title': '写入条目',
    'view.blackboard.write.domain': '域',
    'view.blackboard.write.content': '内容',
    'view.blackboard.write.contentPlaceholder': '知识内容（任意可 JSON 序列化的值）',
    'view.blackboard.write.contributor': '贡献者',
    'view.blackboard.write.contributorPlaceholder': 'agent_a',
    'view.blackboard.write.confidence': '置信度',
    'view.blackboard.write.metadata': '元数据（JSON）',
    'view.blackboard.write.metadataPlaceholder': '{"key": "value"}',
    'view.blackboard.write.submit': '写入',
    'view.blackboard.write.writing': '写入中…',
    'view.blackboard.write.success': '条目已写入',
    'view.blackboard.write.failed': '写入条目失败',

    // ── 清除域 ───────────────────────────────────────────────────
    'view.blackboard.clear.confirm': '确认清除域「{domain}」的全部条目？此操作不可撤销。',
    'view.blackboard.clear.success': '域已清除（移除 {count} 条条目）',
    'view.blackboard.clear.failed': '清除域失败',
    'view.blackboard.clear.action': '清除域',

    // ── 历史 ─────────────────────────────────────────────────────
    'view.blackboard.history.title': '操作历史',
    'view.blackboard.history.empty': '暂无操作记录',
    'view.blackboard.history.failedLoad': '加载历史失败',

    // ── 统计 ─────────────────────────────────────────────────────
    'view.blackboard.stats.title': '黑板统计',
    'view.blackboard.stats.totalEntries': '条目总数',
    'view.blackboard.stats.activeDomains': '活跃域',
    'view.blackboard.stats.eventBus': '事件总线',
    'view.blackboard.stats.eventBusEnabled': '已启用',
    'view.blackboard.stats.eventBusDisabled': '已禁用',
    'view.blackboard.stats.allowedDomains': '允许的域',
    'view.blackboard.stats.failedLoad': '加载统计失败',
    'view.blackboard.stats.empty': '暂无统计数据',

    // ── 校验 ─────────────────────────────────────────────────────
    'view.blackboard.validate.domainRequired': '域为必填项',
    'view.blackboard.validate.contentRequired': '内容为必填项',
    'view.blackboard.validate.confidenceRange': '置信度必须在 0 到 1 之间',
    'view.blackboard.validate.metadataInvalid': '元数据必须是合法的 JSON',
  },
};