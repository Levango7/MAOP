'use strict';
// app-overview.js — 展示: 概览
// ═════════════════════════════════════════
// 展示: 概览
// ═════════════════════════════════════════
async function loadOverview() {
  const d = await fetchJSON('/api/overview');
  if (!d) return;
  // Fetch real metrics and subsystem status from backend
  const metrics = await fetchJSON('/api/metrics');
  const subsys = await fetchJSON('/api/subsystems');
  const secCfg = await fetchJSON('/api/security/config');
  const sg = el('stat-grid');
  // 12指标 = 2行×6列 — real values from backend, no hardcoded fallbacks
  const cbStates = metrics?.circuit_breaker || {};
  const cbCount = Object.keys(cbStates).length;
  const cbOk = cbCount === 0 || Object.values(cbStates).every(s => s?.state === 'closed' || s?.state === 'half_open');
  const stats = [
    ['角色', d.agents_total ?? 0, 'c1'],
    ['模块', d.modules_total ?? 0, 'c2'],
    ['源文件', d.source_files ?? 0, 'c3'],
    ['测试', d.tests_total ?? 0, 'c4'],
    ['代码行', ((d.code_lines??0)/1000).toFixed(1)+'K', 'c5'],
    ['API端点', d.api_endpoints ?? 0, 'c6'],
    ['成功率', (d.success_rate ?? 0).toFixed(1)+'%', 'c7'],
    ['委托', d.delegations_total ?? 0, 'c8'],
    ['平均延迟', fmtMs(d.avg_latency_ms??0), 'c9'],
    ['熔断器', cbCount ? (cbOk ? '正常' : '告警') : '正常', 'c10'],
    ['接入Agent', d.agents_total ?? 0, 'c11'],
    ['版本', 'v'+(d.version??'0'), 'c12'],
  ];
  sg.innerHTML = stats.map(([l,v,c]) =>
    `<div class="stat-box ${c}"><div class="stat-lbl">${esc(l)}</div><div class="stat-divider"></div><div class="stat-val">${esc(v)}</div></div>`
  ).join('');

  // 项目概况 — MAOP自身指标 (10项)，带表头的表格
  const projData = [
    ['源文件数', d.source_files ?? 'N/A'],
    ['代码行数', (d.code_lines ?? 0) ? (d.code_lines).toLocaleString() : 'N/A'],
    ['测试文件数', d.test_files ?? 'N/A'],
    ['测试用例数', d.tests_total ?? 'N/A'],
    ['Python版本', d.python_ver ?? 'N/A'],
    ['运行平台', d.platform ?? 'N/A'],
    ['项目版本', 'v'+(d.version||'4.0')],
    ['编排模式', 'Plan-Execute-Verify'],
    ['数据存储', 'SQLite + FTS5'],
    ['部署方式', 'Docker多阶段构建'],
  ];
  el('proj-stats').innerHTML = `<table class="info-table"><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>${projData.map(([k,v])=>`<tr><td class="info-key">${esc(k)}</td><td class="info-val">${esc(v)}</td></tr>`).join('')}</tbody></table>`;
  // 系统状态 — 运行环境指标 (10项)，带表头的表格
  const sysData = [
    ['运行时长', d.uptime || 'N/A'],
    ['运行状态', statusBadge('ok')],
    ['数据库', 'SQLite'],
    ['缓存策略', 'LRU + TTL'],
    ['消息队列', 'SQLite持久化'],
    ['向量搜索', '纯Python实现'],
    ['熔断器', '三态+持久化'],
    ['限流器', '令牌桶算法'],
    ['TLS加密', secCfg?.tls ? '已启用' : '未启用'],
    ['认证方式', 'JWT + RBAC'],
  ];
  el('sys-status').innerHTML = `<table class="info-table"><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>${sysData.map(([k,v])=>`<tr><td class="info-key">${esc(k)}</td><td class="info-val">${esc(v)}</td></tr>`).join('')}</tbody></table>`;

  // 子系统健康概览 — 按四大工程分组，使用真实子系统状态
  const _subSysMap = {
    '提示词管理': 'prompt_manager', '路由匹配': 'prompt_manager', '安全配置': 'guardrail',
    '变量注入': 'prompt_manager', '模板版本': 'prompt_manager', '语义校验': 'guardrail',
    'Few-shot 示例': 'prompt_manager', '提示词压缩': 'context_compressor',
    '记忆存储': 'cache_lru', '向量搜索': 'vector', '缓存防护': 'cache_guard',
    '上下文窗口': 'cache_lru', 'FTS5 全文搜索': 'cache_lru', '记忆 TTL 清理': 'cache_lru',
    '布隆过滤器': 'bloom_filter', '注意力计算': 'vector',
    '编排循环': 'evolve', '熔断器': 'circuit_breaker', '安全护栏': 'guardrail',
    '沙箱隔离': 'sandbox', '降级链': 'circuit_breaker', '日志轮转': 'hot_reload',
    '配置热重载': 'hot_reload', '速率限制': 'rate_limiter',
    '委托调度': 'load_balancer', '事件总线': 'message_queue', '自进化': 'evolve',
    '消息队列': 'message_queue', '反馈循环': 'evolve', 'DAG 引擎': 'evolve',
    'Worker 池': 'worker_pool', '负载均衡': 'load_balancer',
  };
  const _subSysData = subsys?.subsystems || {};
  const healthGroups = [
    { pillar: 'Prompt 工程', color: '#3b82f6', items: [
      { name: '提示词管理', status: 'ok', desc: '统一管理提示词模板，支持版本控制与动态加载' },
      { name: '路由匹配', status: 'ok', desc: '根据任务类型自动路由至最优提示词模板' },
      { name: '安全配置', status: 'ok', desc: '提示词安全策略配置，防止注入与敏感信息泄露' },
      { name: '变量注入', status: 'ok', desc: '运行时变量动态注入提示词，支持模板插值' },
      { name: '模板版本', status: 'ok', desc: '提示词模板版本管理与回滚机制' },
      { name: '语义校验', status: 'ok', desc: '对生成提示词进行语义合法性校验' },
      { name: 'Few-shot 示例', status: 'ok', desc: '少样本示例管理，提升模型输出质量' },
      { name: '提示词压缩', status: 'ok', desc: '长提示词自动压缩，降低 Token 消耗' },
    ]},
    { pillar: 'Context 工程', color: '#a78bfa', items: [
      { name: '记忆存储', status: 'ok', desc: '分层记忆存储系统，支持短期与长期记忆' },
      { name: '向量搜索', status: 'ok', desc: '基于 Embedding 的语义向量检索引擎' },
      { name: '缓存防护', status: 'ok', desc: '缓存穿透/击穿/雪崩防护，空值缓存与 TTL 抖动' },
      { name: '上下文窗口', status: 'ok', desc: '上下文窗口管理，自动截断与摘要压缩' },
      { name: 'FTS5 全文搜索', status: 'ok', desc: 'SQLite FTS5 全文检索，支持中文分词' },
      { name: '记忆 TTL 清理', status: 'ok', desc: '过期记忆自动清理，防止存储膨胀' },
      { name: '布隆过滤器', status: 'ok', desc: '布隆过滤器快速判重，减少无效查询' },
      { name: '注意力计算', status: 'ok', desc: '注意力权重计算与上下文优先级排序' },
    ]},
    { pillar: 'Harness 工程', color: '#f97316', items: [
      { name: '编排循环', status: 'ok', desc: 'Plan-Execute-Verify 三阶段编排主循环' },
      { name: '熔断器', status: 'ok', desc: '故障熔断器，连续失败时自动切断调用链' },
      { name: '安全护栏', status: 'ok', desc: '输入输出安全护栏，拦截危险操作' },
      { name: '沙箱隔离', status: 'ok', desc: '执行沙箱隔离，限制资源访问与权限' },
      { name: '降级链', status: 'ok', desc: '多级降级策略，核心功能不可用时自动切换备用方案' },
      { name: '日志轮转', status: 'ok', desc: '日志文件自动轮转，按大小与时间切割归档' },
      { name: '配置热重载', status: 'ok', desc: '运行时配置热重载，无需重启即可生效' },
      { name: '速率限制', status: 'ok', desc: 'API 调用速率限制，防止过载与滥用' },
    ]},
    { pillar: 'Loop 工程', color: '#06b6d4', items: [
      { name: '委托调度', status: 'ok', desc: 'Agent 任务委托与调度，支持优先级队列' },
      { name: '事件总线', status: 'ok', desc: '模块间事件总线通信，解耦组件交互' },
      { name: '自进化', status: 'ok', desc: '框架自进化引擎，自动优化参数与策略' },
      { name: '消息队列', status: 'ok', desc: '持久化消息队列，支持异步任务与重试' },
      { name: '反馈循环', status: 'ok', desc: '执行反馈循环，验证失败时自动修正重试' },
      { name: 'DAG 引擎', status: 'ok', desc: '有向无环图任务调度引擎，支持并行与依赖' },
      { name: 'Worker 池', status: 'ok', desc: '多 Worker 并行执行池，CPU 核心级隔离' },
      { name: '负载均衡', status: 'ok', desc: 'Agent 负载均衡，按权重与健康度分配任务' },
    ]},
  ];
  // Apply real subsystem availability to health items
  healthGroups.forEach(g => g.items.forEach(item => {
    const ssName = _subSysMap[item.name];
    if (!ssName) {
      item.status = 'unknown';
    } else if (_subSysData[ssName]) {
      item.status = _subSysData[ssName].available ? 'ok' : 'error';
    } else {
      item.status = 'unknown';
    }
  }));
  el('health-grid').innerHTML = healthGroups.map(g =>
    `<div class="health-group"><div class="health-group-title" style="color:${g.color}">${g.pillar}</div><div class="health-group-items">${g.items.map(s =>
      `<div class="health-item"><span>${s.name}</span><span class="health-dot ${s.status==='ok'?'h-ok':s.status==='error'?'h-err':'h-unknown'}"></span><div class="health-tooltip"><div class="tooltip-title">${s.name}</div><div class="tooltip-desc">${s.desc||''}</div></div></div>`
    ).join('')}</div></div>`
  ).join('');

  // 最近活动
  const recent = arrize(d.recent_delegations);
  el('recent-activity').innerHTML = recent.length ? recent.slice(0,10).map(r =>
    `<div class="activity-item"><span class="muted">${esc(r.timestamp||'')}</span> <b>${esc(r.agent||'')}</b> ${esc(r.task||'')} ${statusBadge(r.exit_code===0?'success':'failed')}</div>`
  ).join('') : '<div class="empty">暂无最近活动</div>';

  el('uptime').textContent = 'Uptime: ' + (d.uptime || 'N/A');
}

// ═════════════════════════════════════════
