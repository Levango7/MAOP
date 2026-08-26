export const messages = {
  en: {
    'view.licenses.subtitle': 'Issue, renew and revoke enterprise licenses',
    'view.licenses.enterprise': 'Enterprise',
    'view.licenses.searchPlaceholder': 'Filter by customer or key…',

    // ── Actions ────────────────────────────────────────────────
    'view.licenses.generate': 'Generate License',
    'view.licenses.generating': 'Generating…',
    'view.licenses.generated': 'License generated for {name}',
    'view.licenses.generateFailed': 'Generate failed',
    'view.licenses.renew': 'Renew',
    'view.licenses.renewing': 'Renewing…',
    'view.licenses.renewed': 'License {id} renewed for {days} days',
    'view.licenses.revoke': 'Revoke',
    'view.licenses.revokeConfirm': 'Revoke license "{id}"? This action cannot be undone.',
    'view.licenses.revoked': 'License {id} revoked',
    'view.licenses.viewDetails': 'View Details',

    // ── Empty / error ──────────────────────────────────────────
    'view.licenses.noLicenses': 'No licenses',
    'view.licenses.noLicensesDesc': 'Generate a license to grant enterprise access to a customer.',
    'view.licenses.loadError': 'Could not load licenses',

    // ── Table columns ──────────────────────────────────────────
    'view.licenses.customerName': 'Customer',
    'view.licenses.customerEmail': 'Email',
    'view.licenses.licenseKey': 'License Key',
    'view.licenses.licenseId': 'License ID',
    'view.licenses.version': 'Edition',
    'view.licenses.expiresAt': 'Expires',
    'view.licenses.maxAgents': 'Max Agents',
    'view.licenses.maxUsers': 'Max Users',
    'view.licenses.created': 'Created',
    'view.licenses.updated': 'Updated',

    // ── Status ─────────────────────────────────────────────────
    'view.licenses.statusTrial': 'Trial',
    'view.licenses.statusActive': 'Active',
    'view.licenses.statusExpired': 'Expired',
    'view.licenses.statusRevoked': 'Revoked',

    // ── Filters ────────────────────────────────────────────────
    'view.licenses.allStatuses': 'All statuses',
    'view.licenses.allVersions': 'All editions',
    'view.licenses.allExpiry': 'All expiry',
    'view.licenses.expiryActive': 'Active',
    'view.licenses.expiryExpired': 'Expired',
    'view.licenses.expirySoon': 'Expiring soon',

    // ── Generate dialog ────────────────────────────────────────
    'view.licenses.customerInfo': 'Customer Information',
    'view.licenses.quotaSettings': 'Quota Settings',
    'view.licenses.validPeriod': 'Validity Period',
    'view.licenses.validDays': 'Valid days',
    'view.licenses.versionPersonal': 'Personal',
    'view.licenses.versionTeam': 'Team',
    'view.licenses.versionEnterprise': 'Enterprise',
    'view.licenses.customerRequired': 'Customer name and email are required',

    // ── Detail drawer ──────────────────────────────────────────
    'view.licenses.detailTitle': 'License Detail',
    'view.licenses.detailInfo': 'Basic Information',
    'view.licenses.detailQuota': 'Quota',
    'view.licenses.detailHistory': 'Operation History',
    'view.licenses.historyAction': 'Action',
    'view.licenses.historyTime': 'Time',
    'view.licenses.historyActor': 'Actor',
    'view.licenses.noHistory': 'No operation history',

    // ── Stats ──────────────────────────────────────────────────
    'view.licenses.totalLicenses': 'Total Licenses',
    'view.licenses.activeLicenses': 'Active',
    'view.licenses.expiringSoon': 'Expiring Soon',
    'view.licenses.status': 'Status',
    'view.licenses.loadFailed': 'Failed to load licenses',
    'view.licenses.renewFailed': 'Renew failed',
    'view.licenses.revokeFailed': 'Revoke failed',
  },

  zh: {
    'view.licenses.subtitle': '签发、续期与吊销企业版 License',
    'view.licenses.enterprise': '企业版',
    'view.licenses.searchPlaceholder': '按客户或 Key 筛选…',

    // ── Actions ────────────────────────────────────────────────
    'view.licenses.generate': '生成 License',
    'view.licenses.generating': '生成中…',
    'view.licenses.generated': '已为 {name} 生成 License',
    'view.licenses.generateFailed': '生成失败',
    'view.licenses.renew': '续期',
    'view.licenses.renewing': '续期中…',
    'view.licenses.renewed': 'License {id} 已续期 {days} 天',
    'view.licenses.revoke': '吊销',
    'view.licenses.revokeConfirm': '确定吊销 License"{id}"？此操作不可恢复。',
    'view.licenses.revoked': 'License {id} 已吊销',
    'view.licenses.viewDetails': '查看详情',

    // ── Empty / error ──────────────────────────────────────────
    'view.licenses.noLicenses': '暂无 License',
    'view.licenses.noLicensesDesc': '生成 License 以向客户授予企业版访问权限。',
    'view.licenses.loadError': '无法加载 License 列表',

    // ── Table columns ──────────────────────────────────────────
    'view.licenses.customerName': '客户名',
    'view.licenses.customerEmail': '邮箱',
    'view.licenses.licenseKey': 'License Key',
    'view.licenses.licenseId': 'License ID',
    'view.licenses.version': '版本',
    'view.licenses.expiresAt': '过期时间',
    'view.licenses.maxAgents': '最大 Agent 数',
    'view.licenses.maxUsers': '最大用户数',
    'view.licenses.created': '创建时间',
    'view.licenses.updated': '更新时间',

    // ── Status ─────────────────────────────────────────────────
    'view.licenses.statusTrial': '试用',
    'view.licenses.statusActive': '正式',
    'view.licenses.statusExpired': '到期',
    'view.licenses.statusRevoked': '已吊销',

    // ── Filters ────────────────────────────────────────────────
    'view.licenses.allStatuses': '全部状态',
    'view.licenses.allVersions': '全部版本',
    'view.licenses.allExpiry': '全部过期范围',
    'view.licenses.expiryActive': '未过期',
    'view.licenses.expiryExpired': '已过期',
    'view.licenses.expirySoon': '即将过期',

    // ── Generate dialog ────────────────────────────────────────
    'view.licenses.customerInfo': '客户信息',
    'view.licenses.quotaSettings': '配额设置',
    'view.licenses.validPeriod': '有效期',
    'view.licenses.validDays': '有效天数',
    'view.licenses.versionPersonal': '个人版',
    'view.licenses.versionTeam': '团队版',
    'view.licenses.versionEnterprise': '企业版',
    'view.licenses.customerRequired': '客户名和邮箱为必填项',

    // ── Detail drawer ──────────────────────────────────────────
    'view.licenses.detailTitle': 'License 详情',
    'view.licenses.detailInfo': '基本信息',
    'view.licenses.detailQuota': '配额',
    'view.licenses.detailHistory': '操作历史',
    'view.licenses.historyAction': '操作',
    'view.licenses.historyTime': '时间',
    'view.licenses.historyActor': '执行者',
    'view.licenses.noHistory': '暂无操作历史',

    // ── Stats ──────────────────────────────────────────────────
    'view.licenses.totalLicenses': 'License 总数',
    'view.licenses.activeLicenses': '正式生效',
    'view.licenses.expiringSoon': '即将过期',
    'view.licenses.status': '状态',
    'view.licenses.loadFailed': '加载许可证失败',
    'view.licenses.renewFailed': '续期失败',
    'view.licenses.revokeFailed': '吊销失败',
  },
};