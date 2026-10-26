export const messages = {
  en: {
    'view.examples.title': 'Example Tasks',
    'view.examples.subtitle':
      'Prompts that actually run against MAOP. Copy one and hand it to an agent.',
    'view.examples.searchPlaceholder': 'Search examples',
    'view.examples.allCategories': 'All categories',
    'view.examples.empty': 'No example matches this filter.',
    'view.examples.copy': 'Copy command',
    'view.examples.copied': 'Command copied.',
    'view.examples.copyFailed': 'Copy failed — select the command on the card instead.',
    'view.examples.note':
      'Each example is a single-task run. For multi-step work, chain them in the Workflow editor.',

    // ── Categories ────────────────────────────────────────────────
    'view.examples.category.refactor': 'Refactor',
    'view.examples.category.test': 'Testing',
    'view.examples.category.review': 'Review',
    'view.examples.category.migrate': 'Migration',
    'view.examples.category.docs': 'Docs',
    'view.examples.category.fix': 'Bug fix',

    // ── Items ─────────────────────────────────────────────────────
    'view.examples.item.refactor-jwt.title': 'Switch auth to JWT',
    'view.examples.item.refactor-jwt.why':
      'Touches signing, verification, and every caller at once — a good first read of how the loop plans before editing.',
    'view.examples.item.add-unit-tests.title': 'Cover a service with tests',
    'view.examples.item.add-unit-tests.why':
      'Exercises the verify phase: the agent has to make the suite pass, not just write assertions.',
    'view.examples.item.review-pr-diff.title': 'Review a staged diff',
    'view.examples.item.review-pr-diff.why':
      'Read-only task. Useful for judging output quality before letting an agent write code.',
    'view.examples.item.migrate-api.title': 'Migrate off a deprecated helper',
    'view.examples.item.migrate-api.why':
      'Many small edits across files — shows how the loop handles breadth without losing the gate.',
    'view.examples.item.generate-module-docs.title': 'Generate module docs',
    'view.examples.item.generate-module-docs.why':
      'A write-heavy task with a checkable result: either the sections exist or they do not.',
    'view.examples.item.fix-failing-test.title': 'Fix a failing test',
    'view.examples.item.fix-failing-test.why':
      'The classic verify-driven case: reproduce, fix the cause, then prove the suite is green.',
  },
  zh: {
    'view.examples.title': '示例任务',
    'view.examples.subtitle': '可以直接跑在 MAOP 上的 prompt。复制一条交给 agent 即可。',
    'view.examples.searchPlaceholder': '搜索示例',
    'view.examples.allCategories': '全部分类',
    'view.examples.empty': '没有匹配该筛选的示例。',
    'view.examples.copy': '复制命令',
    'view.examples.copied': '命令已复制。',
    'view.examples.copyFailed': '复制失败 —— 请手动选中卡片上的命令。',
    'view.examples.note': '每个示例都是一次单任务运行。多步骤的工作请到工作流编辑器里串联。',

    // ── 分类 ──────────────────────────────────────────────────────
    'view.examples.category.refactor': '重构',
    'view.examples.category.test': '测试',
    'view.examples.category.review': '审查',
    'view.examples.category.migrate': '迁移',
    'view.examples.category.docs': '文档',
    'view.examples.category.fix': '缺陷修复',

    // ── 条目 ──────────────────────────────────────────────────────
    'view.examples.item.refactor-jwt.title': '把认证切换到 JWT',
    'view.examples.item.refactor-jwt.why':
      '同时牵动签名、校验和所有调用方 —— 适合第一次观察循环在动手前怎么规划。',
    'view.examples.item.add-unit-tests.title': '给一个 service 补测试',
    'view.examples.item.add-unit-tests.why':
      '会真正走 verify 阶段：agent 必须让测试通过，而不只是写出断言。',
    'view.examples.item.review-pr-diff.title': '审查暂存的改动',
    'view.examples.item.review-pr-diff.why':
      '纯只读任务。适合在让 agent 写代码之前，先判断它的输出质量。',
    'view.examples.item.migrate-api.title': '迁移掉一个废弃 helper',
    'view.examples.item.migrate-api.why':
      '大量跨文件小改动 —— 看循环怎么处理广度而不丢掉门禁。',
    'view.examples.item.generate-module-docs.title': '生成模块文档',
    'view.examples.item.generate-module-docs.why':
      '写操作多、结果可核验：章节要么存在要么不存在。',
    'view.examples.item.fix-failing-test.title': '修复失败的测试',
    'view.examples.item.fix-failing-test.why':
      '最典型的 verify 驱动场景：先复现，再修根因，最后证明测试全绿。',
  },
};
