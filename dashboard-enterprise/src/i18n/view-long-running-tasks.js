export const messages = {
  en: {
    'view.longRunningTasks.title': 'Long-Running Tasks',
    'view.longRunningTasks.subtitle':
      'Tasks that outlive a single request. Only pending and running tasks appear here.',
    'view.longRunningTasks.listTitle': 'Active tasks',

    // ── Actions ───────────────────────────────────────────────────
    'view.longRunningTasks.action.cancel': 'Cancel',

    // ── Confirm ───────────────────────────────────────────────────
    'view.longRunningTasks.confirm.title': 'Cancel this task?',
    'view.longRunningTasks.confirm.body':
      'The task running on "{agent}" will be stopped. Work already done is not rolled back.',
    'view.longRunningTasks.confirm.proceed': 'Cancel task',

    // ── States ────────────────────────────────────────────────────
    'view.longRunningTasks.empty': 'No active long-running tasks.',
    'view.longRunningTasks.loadFailed': 'Failed to load tasks.',
    'view.longRunningTasks.cancelled': 'Task cancelled.',
    'view.longRunningTasks.cancelFailed': 'Failed to cancel this task.',
    'view.longRunningTasks.sseNote':
      'Progress updates live while this page is open. If the stream drops, refresh to resync.',
  },
  zh: {
    'view.longRunningTasks.title': '长任务',
    'view.longRunningTasks.subtitle':
      '生命周期超出单次请求的任务。此处只显示 pending 与 running 状态的任务。',
    'view.longRunningTasks.listTitle': '活跃任务',

    // ── Actions ───────────────────────────────────────────────────
    'view.longRunningTasks.action.cancel': '取消',

    // ── Confirm ───────────────────────────────────────────────────
    'view.longRunningTasks.confirm.title': '取消这个任务？',
    'view.longRunningTasks.confirm.body':
      '"{agent}" 上运行的任务将被停止。已完成的工作不会回滚。',
    'view.longRunningTasks.confirm.proceed': '确认取消',

    // ── States ────────────────────────────────────────────────────
    'view.longRunningTasks.empty': '当前没有活跃的长任务。',
    'view.longRunningTasks.loadFailed': '任务列表加载失败。',
    'view.longRunningTasks.cancelled': '任务已取消。',
    'view.longRunningTasks.cancelFailed': '取消该任务失败。',
    'view.longRunningTasks.sseNote':
      '本页打开期间进度会实时更新；若流断开，刷新一次即可重新同步。',
  },
};
