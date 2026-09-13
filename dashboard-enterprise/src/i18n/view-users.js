// P2-175 fix: 扩充 view-users.js i18n 字典。
// 原仅含 2 个错误消息键（networkError / failed），过于稀疏。
// 补充用户管理页面常用翻译键（标题、表头、表单、操作、状态、反馈），
// 采用 view.users.* 命名空间，与 view-rbac.js / view-audit.js 风格一致。
// 注意：Users.vue 中 users.* 前缀的键定义在 coreMessages（i18n/index.js），
// 此处 view.users.* 键为视图专属补充，供未来迁移或子组件复用。
export const messages = {
  en: {
    // ── 错误/网络（原有，保留）──────────────────────────────────
    'view.users.networkError': 'Network error',
    'view.users.failed': 'Failed',

    // ── 页面标题与副标题 ──────────────────────────────────────────
    'view.users.title': 'User Management',
    'view.users.subtitle': 'Register, edit and deregister platform users',
    'view.users.enterprise': 'Enterprise',

    // ── 表头 ──────────────────────────────────────────────────────
    'view.users.columnIndex': '#',
    'view.users.username': 'Username',
    'view.users.email': 'Email',
    'view.users.roles': 'Roles',
    'view.users.role': 'Role',
    'view.users.status': 'Status',
    'view.users.created': 'Created',
    'view.users.lastLogin': 'Last Login',
    'view.users.actions': 'Actions',

    // ── 操作按钮 ──────────────────────────────────────────────────
    'view.users.registerUser': 'Register User',
    'view.users.deregisterUser': 'Deregister User',
    'view.users.updateProfile': 'Update Profile',
    'view.users.edit': 'Edit',
    'view.users.refresh': 'Refresh',

    // ── 表单 ──────────────────────────────────────────────────────
    'view.users.password': 'Password',
    'view.users.passwordOptional': 'Password (optional)',
    'view.users.passwordPlaceholder': 'Enter password',
    'view.users.usernamePlaceholder': 'Enter username',
    'view.users.roleHint': 'Select one or more roles',

    // ── 角色枚举 ──────────────────────────────────────────────────
    'view.users.role.admin': 'Admin',
    'view.users.role.superadmin': 'Super Admin',
    'view.users.role.operator': 'Operator',
    'view.users.role.write': 'Write',
    'view.users.role.read': 'Read',

    // ── 状态枚举 ──────────────────────────────────────────────────
    'view.users.statusActive': 'Active',
    'view.users.statusInactive': 'Inactive',
    'view.users.statusLocked': 'Locked',

    // ── 空状态与反馈 ──────────────────────────────────────────────
    'view.users.noUsers': 'No users',
    'view.users.noUsersDesc': 'No users have been registered yet.',
    'view.users.confirmDelete': 'Confirm deregistration of this user?',
    'view.users.registerSuccess': 'User registered',
    'view.users.updateSuccess': 'User updated',
    'view.users.deregisterSuccess': 'User deregistered',
    'view.users.registerFailed': 'Registration failed',
    'view.users.updateFailed': 'Update failed',
    'view.users.deregisterFailed': 'Deregistration failed',
    'view.users.usernameRequired': 'Username and password are required',
    'view.users.loadFailed': 'Failed to load users',
    'view.users.self': 'me',
  },

  zh: {
    // ── 错误/网络（原有，保留）──────────────────────────────────
    'view.users.networkError': '网络错误',
    'view.users.failed': '失败',

    // ── 页面标题与副标题 ──────────────────────────────────────────
    'view.users.title': '用户管理',
    'view.users.subtitle': '注册、编辑和注销平台用户',
    'view.users.enterprise': '企业版',

    // ── 表头 ──────────────────────────────────────────────────────
    'view.users.columnIndex': '#',
    'view.users.username': '用户名',
    'view.users.email': '邮箱',
    'view.users.roles': '角色',
    'view.users.role': '角色',
    'view.users.status': '状态',
    'view.users.created': '创建时间',
    'view.users.lastLogin': '最后登录',
    'view.users.actions': '操作',

    // ── 操作按钮 ──────────────────────────────────────────────────
    'view.users.registerUser': '注册用户',
    'view.users.deregisterUser': '注销用户',
    'view.users.updateProfile': '更新资料',
    'view.users.edit': '编辑',
    'view.users.refresh': '刷新',

    // ── 表单 ──────────────────────────────────────────────────────
    'view.users.password': '密码',
    'view.users.passwordOptional': '密码（可选）',
    'view.users.passwordPlaceholder': '请输入密码',
    'view.users.usernamePlaceholder': '请输入用户名',
    'view.users.roleHint': '选择一个或多个角色',

    // ── 角色枚举 ──────────────────────────────────────────────────
    'view.users.role.admin': '管理员',
    'view.users.role.superadmin': '超级管理员',
    'view.users.role.operator': '操作员',
    'view.users.role.write': '写入',
    'view.users.role.read': '只读',

    // ── 状态枚举 ──────────────────────────────────────────────────
    'view.users.statusActive': '活跃',
    'view.users.statusInactive': '未激活',
    'view.users.statusLocked': '已锁定',

    // ── 空状态与反馈 ──────────────────────────────────────────────
    'view.users.noUsers': '暂无用户',
    'view.users.noUsersDesc': '尚未注册任何用户。',
    'view.users.confirmDelete': '确认注销该用户？',
    'view.users.registerSuccess': '用户已注册',
    'view.users.updateSuccess': '用户已更新',
    'view.users.deregisterSuccess': '用户已注销',
    'view.users.registerFailed': '注册失败',
    'view.users.updateFailed': '更新失败',
    'view.users.deregisterFailed': '注销失败',
    'view.users.usernameRequired': '用户名和密码为必填项',
    'view.users.loadFailed': '加载用户列表失败',
    'view.users.self': '我',
  },
};
