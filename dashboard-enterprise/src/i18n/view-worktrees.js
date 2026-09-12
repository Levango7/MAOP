export const messages = {
  en: {
    'view.worktrees.title': 'Worktrees',
    'view.worktrees.sub': 'Git worktree isolation for parallel agent runs.',

    // ── Tabs ──────────────────────────────────────────────────────
    'view.worktrees.tab.branches': 'Branches',
    'view.worktrees.tab.actions': 'Actions',

    // ── Branches ──────────────────────────────────────────────────
    'view.worktrees.branches.title': 'Worktree Branches',
    'view.worktrees.branches.add': 'Create Root',
    'view.worktrees.branches.empty': 'No worktree branches',
    'view.worktrees.branches.emptyHint': 'Create a root worktree to start parallel agent runs.',
    'view.worktrees.branches.failedLoad': 'Failed to load branches',
    'view.worktrees.branches.activeOnly': 'Active only',

    // ── Columns ───────────────────────────────────────────────────
    'view.worktrees.col.name': 'Branch',
    'view.worktrees.col.status': 'Status',
    'view.worktrees.col.parent': 'Parent',
    'view.worktrees.col.description': 'Description',
    'view.worktrees.col.created': 'Created',
    'view.worktrees.col.actions': 'Actions',

    // ── Status ────────────────────────────────────────────────────
    'view.worktrees.status.active': 'Active',
    'view.worktrees.status.abandoned': 'Abandoned',
    'view.worktrees.status.merged': 'Merged',
    'view.worktrees.status.unknown': 'Unknown',

    // ── Create root modal ────────────────────────────────────────
    'view.worktrees.modal.createRootTitle': 'Create Root Worktree',
    'view.worktrees.modal.task': 'Task',
    'view.worktrees.modal.taskHint': 'Short task description for the root worktree',
    'view.worktrees.modal.description': 'Description',
    'view.worktrees.modal.descriptionHint': 'Optional longer description',
    'view.worktrees.modal.saving': 'Creating…',
    'view.worktrees.modal.taskRequired': 'Task is required',

    // ── Branch modal ─────────────────────────────────────────────
    'view.worktrees.modal.branchTitle': 'Create Branch',
    'view.worktrees.modal.branchName': 'Branch Name',
    'view.worktrees.modal.branchNameHint': 'e.g. feature/login',
    'view.worktrees.modal.parent': 'Parent Node',
    'view.worktrees.modal.branchNameRequired': 'Branch name and parent are required',

    // ── Merge modal ──────────────────────────────────────────────
    'view.worktrees.modal.mergeTitle': 'Merge Branch',
    'view.worktrees.modal.sourceBranch': 'Source Branch',
    'view.worktrees.modal.targetBranch': 'Target Branch',
    'view.worktrees.modal.targetBranchHint': 'Leave empty to merge into root',
    'view.worktrees.modal.sourceRequired': 'Source branch is required',
    'view.worktrees.modal.mergeSuccess': 'Branch merged successfully',

    // ── Checkpoint modal ─────────────────────────────────────────
    'view.worktrees.modal.checkpointTitle': 'Create Checkpoint',
    'view.worktrees.modal.checkpointLabel': 'Label',
    'view.worktrees.modal.checkpointLabelHint': 'Optional checkpoint label',
    'view.worktrees.modal.checkpointSuccess': 'Checkpoint created',

    // ── Rollback ─────────────────────────────────────────────────
    'view.worktrees.modal.rollbackTitle': 'Rollback to Checkpoint',
    'view.worktrees.modal.checkpointId': 'Checkpoint ID',
    'view.worktrees.modal.rollbackSuccess': 'Rolled back successfully',
    'view.worktrees.modal.checkpointRequired': 'Checkpoint ID is required',

    // ── Confirm abandon ──────────────────────────────────────────
    'view.worktrees.abandonConfirm': 'Abandon this branch? This action cannot be undone.',
    'view.worktrees.abandonSuccess': 'Branch abandoned',
    'view.worktrees.abandonFailed': 'Failed to abandon branch',

    // ── Errors ───────────────────────────────────────────────────
    'view.worktrees.error.createRoot': 'Failed to create root worktree',
    'view.worktrees.error.branch': 'Failed to create branch',
    'view.worktrees.error.merge': 'Failed to merge branch',
    'view.worktrees.error.checkpoint': 'Failed to create checkpoint',
    'view.worktrees.error.rollback': 'Failed to rollback',
  },

  zh: {
    'view.worktrees.title': '工作树',
    'view.worktrees.sub': '为并行智能体运行提供 Git 工作树隔离。',

    // ── 标签页 ────────────────────────────────────────────────────
    'view.worktrees.tab.branches': '分支',
    'view.worktrees.tab.actions': '操作',

    // ── 分支 ──────────────────────────────────────────────────────
    'view.worktrees.branches.title': '工作树分支',
    'view.worktrees.branches.add': '创建根工作树',
    'view.worktrees.branches.empty': '暂无工作树分支',
    'view.worktrees.branches.emptyHint': '创建一个根工作树以启动并行智能体运行。',
    'view.worktrees.branches.failedLoad': '加载分支失败',
    'view.worktrees.branches.activeOnly': '仅活跃',

    // ── 列 ────────────────────────────────────────────────────────
    'view.worktrees.col.name': '分支',
    'view.worktrees.col.status': '状态',
    'view.worktrees.col.parent': '父节点',
    'view.worktrees.col.description': '描述',
    'view.worktrees.col.created': '创建时间',
    'view.worktrees.col.actions': '操作',

    // ── 状态 ──────────────────────────────────────────────────────
    'view.worktrees.status.active': '活跃',
    'view.worktrees.status.abandoned': '已放弃',
    'view.worktrees.status.merged': '已合并',
    'view.worktrees.status.unknown': '未知',

    // ── 创建根工作树模态框 ────────────────────────────────────────
    'view.worktrees.modal.createRootTitle': '创建根工作树',
    'view.worktrees.modal.task': '任务',
    'view.worktrees.modal.taskHint': '根工作树的简短任务描述',
    'view.worktrees.modal.description': '描述',
    'view.worktrees.modal.descriptionHint': '可选的较长描述',
    'view.worktrees.modal.saving': '创建中…',
    'view.worktrees.modal.taskRequired': '任务为必填项',

    // ── 分支模态框 ────────────────────────────────────────────────
    'view.worktrees.modal.branchTitle': '创建分支',
    'view.worktrees.modal.branchName': '分支名称',
    'view.worktrees.modal.branchNameHint': '例如 feature/login',
    'view.worktrees.modal.parent': '父节点',
    'view.worktrees.modal.branchNameRequired': '分支名称和父节点为必填项',

    // ── 合并模态框 ────────────────────────────────────────────────
    'view.worktrees.modal.mergeTitle': '合并分支',
    'view.worktrees.modal.sourceBranch': '源分支',
    'view.worktrees.modal.targetBranch': '目标分支',
    'view.worktrees.modal.targetBranchHint': '留空则合并到根',
    'view.worktrees.modal.sourceRequired': '源分支为必填项',
    'view.worktrees.modal.mergeSuccess': '分支合并成功',

    // ── 检查点模态框 ──────────────────────────────────────────────
    'view.worktrees.modal.checkpointTitle': '创建检查点',
    'view.worktrees.modal.checkpointLabel': '标签',
    'view.worktrees.modal.checkpointLabelHint': '可选的检查点标签',
    'view.worktrees.modal.checkpointSuccess': '检查点已创建',

    // ── 回滚 ──────────────────────────────────────────────────────
    'view.worktrees.modal.rollbackTitle': '回滚到检查点',
    'view.worktrees.modal.checkpointId': '检查点 ID',
    'view.worktrees.modal.rollbackSuccess': '回滚成功',
    'view.worktrees.modal.checkpointRequired': '检查点 ID 为必填项',

    // ── 确认放弃 ──────────────────────────────────────────────────
    'view.worktrees.abandonConfirm': '确认放弃此分支？此操作不可撤销。',
    'view.worktrees.abandonSuccess': '分支已放弃',
    'view.worktrees.abandonFailed': '放弃分支失败',

    // ── 错误 ──────────────────────────────────────────────────────
    'view.worktrees.error.createRoot': '创建根工作树失败',
    'view.worktrees.error.branch': '创建分支失败',
    'view.worktrees.error.merge': '合并分支失败',
    'view.worktrees.error.checkpoint': '创建检查点失败',
    'view.worktrees.error.rollback': '回滚失败',
  },
};