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
    'view.credentialVault.action.create': 'New credential',
    'view.credentialVault.action.rotate': 'Rotate',
    'view.credentialVault.action.delete': 'Delete',

    // ── Create form ───────────────────────────────────────────────
    'view.credentialVault.form.title': 'New credential',
    'view.credentialVault.form.type': 'Type',
    'view.credentialVault.form.agent': 'Agent',
    'view.credentialVault.form.agentHint': 'e.g. codex',
    'view.credentialVault.form.agentRequired': 'Agent name is required.',
    'view.credentialVault.form.name': 'Label (optional)',
    'view.credentialVault.form.nameHint': 'Shown in the list',
    'view.credentialVault.form.fieldRequired': '{field} is required.',
    'view.credentialVault.form.submit': 'Create',
    'view.credentialVault.form.saving': 'Creating…',

    // ── Field labels ──────────────────────────────────────────────
    'view.credentialVault.field.api_key': 'API key',
    'view.credentialVault.field.oauth_access_token': 'OAuth access token',
    'view.credentialVault.field.oauth_refresh_token': 'OAuth refresh token',
    'view.credentialVault.field.oauth_expires_at': 'OAuth expiry (epoch seconds)',
    'view.credentialVault.field.username': 'Username',
    'view.credentialVault.field.password': 'Password',
    'view.credentialVault.field.license_key': 'License key',
    'view.credentialVault.field.cookie_string': 'Cookie string',
    'view.credentialVault.field.bearer_token': 'Bearer token',

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
    'view.credentialVault.created': 'Credential created.',
    'view.credentialVault.createFailed': 'Failed to create this credential.',
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
    'view.credentialVault.action.create': '新建凭据',
    'view.credentialVault.action.rotate': '轮换',
    'view.credentialVault.action.delete': '删除',

    // ── 新建表单 ──────────────────────────────────────────────────
    'view.credentialVault.form.title': '新建凭据',
    'view.credentialVault.form.type': '类型',
    'view.credentialVault.form.agent': 'Agent',
    'view.credentialVault.form.agentHint': '例如 codex',
    'view.credentialVault.form.agentRequired': 'Agent 名不能为空。',
    'view.credentialVault.form.name': '显示名（可选）',
    'view.credentialVault.form.nameHint': '列表中展示用',
    'view.credentialVault.form.fieldRequired': '{field} 不能为空。',
    'view.credentialVault.form.submit': '创建',
    'view.credentialVault.form.saving': '创建中…',

    // ── 字段标签 ──────────────────────────────────────────────────
    'view.credentialVault.field.api_key': 'API Key',
    'view.credentialVault.field.oauth_access_token': 'OAuth Access Token',
    'view.credentialVault.field.oauth_refresh_token': 'OAuth Refresh Token',
    'view.credentialVault.field.oauth_expires_at': 'OAuth 过期时间（epoch 秒）',
    'view.credentialVault.field.username': '用户名',
    'view.credentialVault.field.password': '密码',
    'view.credentialVault.field.license_key': 'License Key',
    'view.credentialVault.field.cookie_string': 'Cookie 字符串',
    'view.credentialVault.field.bearer_token': 'Bearer Token',

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
    'view.credentialVault.created': '凭据已创建。',
    'view.credentialVault.createFailed': '创建该凭据失败。',
    'view.credentialVault.deleted': '凭据已删除。',
    'view.credentialVault.rotated': '凭据已轮换。',
    'view.credentialVault.deleteFailed': '删除该凭据失败。',
    'view.credentialVault.rotateFailed': '轮换该凭据失败。',
    'view.credentialVault.noPlaintextNote':
      '本页只会收到掩码预览。读取解密后的明文是刻意不在界面中开放的。',
  },
};
