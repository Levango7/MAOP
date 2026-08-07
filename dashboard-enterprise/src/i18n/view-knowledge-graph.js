/**
 * Knowledge Graph view i18n messages (v4.5.0).
 *
 * Keys are namespaced under `view.kg.*` for view-local strings and
 * `nav.knowledgeGraph*` for the shared navigation entry. The navigation
 * keys are referenced from src/nav.js and must stay in sync with the
 * labels declared there.
 */
export const messages = {
  en: {
    // ── Navigation entry (referenced by src/nav.js) ───────────────
    'nav.knowledgeGraph': 'Knowledge Graph',
    'nav.knowledgeGraph.subtitle': 'Entity-relation visual exploration',

    // ── Page header ───────────────────────────────────────────────
    'view.kg.title': 'Knowledge Graph',
    'view.kg.sub': 'Explore entities and relations extracted from memory',

    // ── Filter panel ─────────────────────────────────────────────
    'view.kg.filter.title': 'Filters',
    'view.kg.filter.nodeTypes': 'Node Types',
    'view.kg.filter.type.agent': 'Agent',
    'view.kg.filter.type.task': 'Task',
    'view.kg.filter.type.memory': 'Memory',
    'view.kg.filter.type.concept': 'Concept',
    'view.kg.filter.minConfidence': 'Min Confidence',
    'view.kg.filter.search': 'Search',
    'view.kg.filter.searchPlaceholder': 'Search node label…',
    'view.kg.filter.limit': 'Limit',
    'view.kg.filter.apply': 'Apply',
    'view.kg.filter.reset': 'Reset',

    // ── Timeline ─────────────────────────────────────────────────
    'view.kg.timeline.title': 'Timeline',
    'view.kg.timeline.start': 'Start',
    'view.kg.timeline.end': 'End',
    'view.kg.timeline.replay': 'Replay',
    'view.kg.timeline.invalidRange': 'Start time must not be later than end time',

    // ── Detail panel ─────────────────────────────────────────────
    'view.kg.detail.title': 'Node Details',
    'view.kg.detail.type': 'Type',
    'view.kg.detail.label': 'Label',
    'view.kg.detail.timestamp': 'Created',
    'view.kg.detail.confidence': 'Confidence',
    'view.kg.detail.properties': 'Properties',
    'view.kg.detail.relations': 'Relations',
    'view.kg.detail.relationCount': '{count} relation(s)',
    'view.kg.detail.relatedNodes': 'Related Nodes',
    'view.kg.detail.memorySummary': 'Memory Summary',
    'view.kg.detail.noRelations': 'No direct relations',
    'view.kg.detail.close': 'Close',

    // ── Stats / status ───────────────────────────────────────────
    'view.kg.stats.nodes': 'Nodes',
    'view.kg.stats.edges': 'Edges',
    'view.kg.stats.visible': 'Visible',
    'view.kg.stats.fps': 'FPS',

    // ── Empty / error / loading ──────────────────────────────────
    'view.kg.empty.title': 'No knowledge graph data',
    'view.kg.empty.desc': 'Run an orchestration to sediment memory first',
    'view.kg.error.load': 'Failed to load knowledge graph',
    'view.kg.error.render': 'Graph rendering component failed to load',
    'view.kg.error.timeout': 'Query timed out, try reducing the limit',
    'view.kg.loading': 'Loading graph…',
    'view.kg.retry': 'Retry',
    'view.kg.refresh': 'Refresh',

    // ── LOD / performance ────────────────────────────────────────
    'view.kg.lod.enabled': 'Node count too large — LOD mode enabled',
    'view.kg.lod.cluster': 'Clustered',
    'view.kg.lod.hiddenLabels': 'Labels hidden (zoom out to reveal)',

    // ── P2-10: Physics threshold & cluster folding ───────────────
    'view.kg.physics.disabled': 'Physics off',
    'view.kg.physics.disabledTip': 'Node count exceeds 300 — physics simulation disabled, preset layout applied',
    'view.kg.cluster.enabled': 'Clustered',
    'view.kg.cluster.unfold': 'Unfold all',
    'view.kg.cluster.notice': 'Showing {display} nodes, {folded} folded',

    // ── Path highlight ───────────────────────────────────────────
    'view.kg.path.highlight': 'Highlight path to root',
    'view.kg.path.clear': 'Clear highlight',
    'view.kg.path.noPath': 'No path to root found',
  },

  zh: {
    // ── 导航入口（src/nav.js 引用） ──────────────────────────────
    'nav.knowledgeGraph': '知识图谱',
    'nav.knowledgeGraph.subtitle': '实体-关系可视化探索',

    // ── 页面标题 ─────────────────────────────────────────────────
    'view.kg.title': '知识图谱',
    'view.kg.sub': '探索从记忆中抽取的实体与关系',

    // ── 筛选面板 ─────────────────────────────────────────────────
    'view.kg.filter.title': '筛选',
    'view.kg.filter.nodeTypes': '节点类型',
    'view.kg.filter.type.agent': '智能体',
    'view.kg.filter.type.task': '任务',
    'view.kg.filter.type.memory': '记忆',
    'view.kg.filter.type.concept': '概念',
    'view.kg.filter.minConfidence': '最小置信度',
    'view.kg.filter.search': '搜索',
    'view.kg.filter.searchPlaceholder': '搜索节点标签…',
    'view.kg.filter.limit': '数量上限',
    'view.kg.filter.apply': '应用',
    'view.kg.filter.reset': '重置',

    // ── 时间轴 ───────────────────────────────────────────────────
    'view.kg.timeline.title': '时间轴',
    'view.kg.timeline.start': '起始',
    'view.kg.timeline.end': '结束',
    'view.kg.timeline.replay': '回放',
    'view.kg.timeline.invalidRange': '开始时间不得晚于结束时间',

    // ── 详情面板 ─────────────────────────────────────────────────
    'view.kg.detail.title': '节点详情',
    'view.kg.detail.type': '类型',
    'view.kg.detail.label': '标签',
    'view.kg.detail.timestamp': '创建时间',
    'view.kg.detail.confidence': '置信度',
    'view.kg.detail.properties': '属性',
    'view.kg.detail.relations': '关联关系',
    'view.kg.detail.relationCount': '{count} 条关联',
    'view.kg.detail.relatedNodes': '关联节点',
    'view.kg.detail.memorySummary': '记忆摘要',
    'view.kg.detail.noRelations': '无直接关联',
    'view.kg.detail.close': '关闭',

    // ── 统计 / 状态 ──────────────────────────────────────────────
    'view.kg.stats.nodes': '节点',
    'view.kg.stats.edges': '边',
    'view.kg.stats.visible': '可见',
    'view.kg.stats.fps': '帧率',

    // ── 空 / 错误 / 加载 ─────────────────────────────────────────
    'view.kg.empty.title': '暂无知识图谱数据',
    'view.kg.empty.desc': '请先运行编排以沉淀记忆',
    'view.kg.error.load': '加载知识图谱失败',
    'view.kg.error.render': '图渲染组件加载失败',
    'view.kg.error.timeout': '查询超时，请尝试减小数量上限',
    'view.kg.loading': '加载图谱中…',
    'view.kg.retry': '重试',
    'view.kg.refresh': '刷新',

    // ── LOD / 性能 ───────────────────────────────────────────────
    'view.kg.lod.enabled': '节点数过多 — 已启用 LOD 模式',
    'view.kg.lod.cluster': '已聚合',
    'view.kg.lod.hiddenLabels': '已隐藏标签（放大以显示）',

    // ── P2-10: 物理模拟阈值 & 聚类折叠 ───────────────────────────
    'view.kg.physics.disabled': '物理模拟已关闭',
    'view.kg.physics.disabledTip': '节点数超过 300 — 已关闭物理模拟，启用预设布局',
    'view.kg.cluster.enabled': '已聚类折叠',
    'view.kg.cluster.unfold': '展开全部',
    'view.kg.cluster.notice': '当前显示 {display} 个节点，已折叠 {folded} 个',

    // ── 路径高亮 ─────────────────────────────────────────────────
    'view.kg.path.highlight': '高亮至根路径',
    'view.kg.path.clear': '清除高亮',
    'view.kg.path.noPath': '未找到至根节点的路径',
  },
};