export const messages = {
  en: {
    'view.credentialVault.title': 'Credential Vault',
    'view.credentialVault.subtitle':
      'Encrypted credentials for agents. Only masked previews are shown here — plaintext never leaves the server.',
    'view.credentialVault.listTitle': 'Credentials',

    // ── Columns ───────────────────────────────────────────────────
    'view.credentialVault.col.agent': 'Agent',
    'view.credentialVault.col.type': 'Type',
    'view.credentialVault.col.preview': 'Preview',
    'view.credentialVault.col.lastUsed': 'Last used',
    'view.credentialVault.col.actions': 'Actions',

    // ── Actions ───────────────────────────────────────────────────
    'view.credentialVault.action.rotate': 'Rotate',
    'view.credentialVault.action.delete': 'Delete',

    // ── Confirm ───────────────────────────────────────────────────
    'view.credentialVault.confirm.deleteTitle': 'Delete this credential?',
    'view.credentialVault.confirm.deleteBody':
      'The credential for "{agent}" will be removed permanently. Any agent using it will fail to authenticate.',
    'view.credentialVault.confirm.rotateTitle': 'Rotate this credential?',
    'view.credentialVault.confirm.rotateBody':
      'The secret for "{agent}" will be replaced with a newly generated value. The old value stops working immediately.',
    'view.credentialVault.confirm.proceed': 'Proceed',

    // ── States ────────────────────────────────────────────────────
    'view.credentialVault.empty': 'No credentials stored yet.',
    'view.credentialVault.loadFailed': 'Failed to load credentials.',
    'view.credentialVault.deleted': 'Credential deleted.',
    'view.credentialVault.rotated': 'Credential rotated.',
    'view.credentialVault.deleteFailed': 'Failed to delete this credential.',
    'view.credentialVault.rotateFailed': 'Failed to rotate this credential.',
    'view.credentialVault.noPlaintextNote':
      'This page only ever receives masked previews. Reading a decrypted secret is deliberately not exposed in the UI.',
  },
  zh: {
    'view.credentialVault.title': '凭据保险箱',
    'view.credentialVault.subtitle':
      'Agent 使用的加密凭据。本页只显示掩码预览 —— 明文不出服务端。',
    'view.credentialVault.listTitle': '凭据列表',

    // ── Columns ───────────────────────────────────────────────────
    'view.credentialVault.col.agent': 'Agent',
    'view.credentialVault.col.type': '类型',
    'view.credentialVault.col.preview': '预览',
    'view.credentialVault.col.lastUsed': '最近使用',
    'view.credentialVault.col.actions': '操作',

    // ── Actions ───────────────────────────────────────────────────
    'view.credentialVault.action.rotate': '轮换',
    'view.credentialVault.action.delete': '删除',

    // ── Confirm ───────────────────────────────────────────────────
    'view.credentialVault.confirm.deleteTitle': '删除这条凭据？',
    'view.credentialVault.confirm.deleteBody':
      '"{agent}" 的凭据将被永久删除。使用它的 Agent 将无法通过认证。',
    'view.credentialVault.confirm.rotateTitle': '轮换这条凭据？',
    'view.credentialVault.confirm.rotateBody':
      '"{agent}" 的密钥将被替换为新生成的值，旧值立即失效。',
    'view.credentialVault.confirm.proceed': '确认执行',

    // ── States ────────────────────────────────────────────────────
    'view.credentialVault.empty': '尚未存储任何凭据。',
    'view.credentialVault.loadFailed': '凭据列表加载失败。',
    'view.credentialVault.deleted': '凭据已删除。',
    'view.credentialVault.rotated': '凭据已轮换。',
    'view.credentialVault.deleteFailed': '删除该凭据失败。',
    'view.credentialVault.rotateFailed': '轮换该凭据失败。',
    'view.credentialVault.noPlaintextNote':
      '本页只会收到掩码预览。读取解密后的明文是刻意不在界面中开放的。',
  },
};
