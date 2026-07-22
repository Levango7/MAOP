'use strict';
// app-info.js — 说明: 四大工程~项目架构
// 说明: 四大工程
// ═════════════════════════════════════════
async function loadPillars() {
  const pillars = [
    {
      name: 'Prompt Engineering',
      cn: '提示词工程',
      icon: 'P',
      color: '#3b82f6',
      bg: 'rgba(59,130,246,.15)',
      desc: '设计和管理Agent的提示词模板、变量注入和版本迭代。确保Agent接收到的指令精确、可复用、可追踪。',
      items: [
        { name: '提示词管理', mod: 'prompt_manager.py', status: 'ok', desc: '统一管理提示词模板，支持版本控制与动态加载' },
        { name: '路由匹配', mod: 'maop_plan.py', status: 'ok', desc: '根据任务类型自动路由至最优提示词模板' },
        { name: '安全配置', mod: 'core/guardrail.py', status: 'ok', desc: '提示词安全策略配置，防止注入与敏感信息泄露' },
        { name: '变量注入', mod: 'prompt_manager.py', status: 'ok', desc: '运行时变量动态注入提示词，支持模板插值' },
        { name: '模板版本', mod: 'prompt_manager.py', status: 'ok', desc: '提示词模板版本管理与回滚机制' },
        { name: '语义校验', mod: 'core/guardrail.py', status: 'ok', desc: '对生成提示词进行语义合法性校验' },
        { name: 'Few-shot 示例', mod: 'prompt_manager.py', status: 'ok', desc: '少样本示例管理，提升模型输出质量' },
        { name: '提示词压缩', mod: 'core/context_compressor.py', status: 'ok', desc: '长提示词自动压缩，降低 Token 消耗' },
      ]
    },
    {
      name: 'Context Engineering',
      cn: '上下文工程',
      icon: 'C',
      color: '#a78bfa',
      bg: 'rgba(167,139,250,.15)',
      desc: '管理Agent的上下文窗口、记忆注入、信息检索和知识关联。决定Agent"知道什么"和"记住什么"。',
      items: [
        { name: '记忆存储', mod: 'memory/store.py', status: 'ok', desc: '分层记忆存储系统，支持短期与长期记忆' },
        { name: '向量搜索', mod: 'core/vector.py', status: 'ok', desc: '基于 Embedding 的语义向量检索引擎' },
        { name: '缓存防护', mod: 'core/cache_guard.py', status: 'ok', desc: '缓存穿透/击穿/雪崩防护，空值缓存与 TTL 抖动' },
        { name: '上下文窗口', mod: 'core/context_compressor.py', status: 'ok', desc: '上下文窗口管理，自动截断与摘要压缩' },
        { name: 'FTS5 全文搜索', mod: 'memory/store.py', status: 'ok', desc: 'SQLite FTS5 全文检索，支持中文分词' },
        { name: '记忆 TTL 清理', mod: 'memory/consolidator.py', status: 'ok', desc: '过期记忆自动清理，防止存储膨胀' },
        { name: '布隆过滤器', mod: 'core/bloom_filter.py', status: 'ok', desc: '布隆过滤器快速判重，减少无效查询' },
        { name: '注意力计算', mod: 'core/vector.py', status: 'ok', desc: '注意力权重计算与上下文优先级排序' },
      ]
    },
    {
      name: 'Harness Engineering',
      cn: '驾驭工程',
      icon: 'H',
      color: '#f97316',
      bg: 'rgba(249,115,22,.15)',
      desc: 'Agent的运行时框架——工具调用、执行控制、安全隔离和容错降级。决定Agent"怎么做"和"做得多稳"。',
      items: [
        { name: '编排循环', mod: 'maop_loop.py', status: 'ok', desc: 'Plan-Execute-Verify 三阶段编排主循环' },
        { name: '熔断器', mod: 'core/circuit_breaker.py', status: 'ok', desc: '故障熔断器，连续失败时自动切断调用链' },
        { name: '安全护栏', mod: 'core/guardrail.py', status: 'ok', desc: '输入输出安全护栏，拦截危险操作' },
        { name: '沙箱隔离', mod: 'core/sandbox.py', status: 'ok', desc: '执行沙箱隔离，限制资源访问与权限' },
        { name: '降级链', mod: 'delegate/dispatcher.py', status: 'ok', desc: '多级降级策略，核心功能不可用时自动切换备用方案' },
        { name: '日志轮转', mod: 'core/log_rotate.py', status: 'ok', desc: '日志文件自动轮转，按大小与时间切割归档' },
        { name: '配置热重载', mod: 'config/hot_reload.py', status: 'ok', desc: '运行时配置热重载，无需重启即可生效' },
        { name: '速率限制', mod: 'core/rate_limiter.py', status: 'ok', desc: 'API 调用速率限制，防止过载与滥用' },
      ]
    },
    {
      name: 'Loop Engineering',
      cn: '循环工程',
      icon: 'L',
      color: '#06b6d4',
      bg: 'rgba(6,182,212,.15)',
      desc: '多Agent协调——任务分发、负载均衡、结果聚合和自进化。决定Agent团队"怎么配合"和"怎么变强"。',
      items: [
        { name: '委托调度', mod: 'delegate/dispatcher.py', status: 'ok', desc: 'Agent 任务委托与调度，支持优先级队列' },
        { name: '事件总线', mod: 'core/event_bus.py', status: 'ok', desc: '模块间事件总线通信，解耦组件交互' },
        { name: '自进化', mod: 'evolve.py', status: 'ok', desc: '框架自进化引擎，自动优化参数与策略' },
        { name: '消息队列', mod: 'core/message_queue.py', status: 'ok', desc: '持久化消息队列，支持异步任务与重试' },
        { name: '反馈循环', mod: 'maop_loop.py', status: 'ok', desc: '执行反馈循环，验证失败时自动修正重试' },
        { name: 'DAG 引擎', mod: 'engine.py', status: 'ok', desc: '有向无环图任务调度引擎，支持并行与依赖' },
        { name: 'Worker 池', mod: 'core/worker_pool.py', status: 'ok', desc: '多 Worker 并行执行池，CPU 核心级隔离' },
        { name: '负载均衡', mod: 'core/load_balancer.py', status: 'ok', desc: 'Agent 负载均衡，按权重与健康度分配任务' },
      ]
    },
  ];
  el('pillars-content').innerHTML = pillars.map(p => `
    <div class="pillar-card" style="border-left:5px solid ${p.color}">
      <div class="pillar-head">
        <div class="pillar-icon" style="background:${p.bg};color:${p.color}">${p.icon}</div>
        <div>
          <div class="pillar-name-en" style="color:${p.color}">${p.name}</div>
          <div class="pillar-name-cn">${p.cn}</div>
        </div>
      </div>
      <div class="pillar-divider" style="background:${p.color}"></div>
      <div class="pillar-desc">${p.desc}</div>
      <div class="pillar-items">
        ${p.items.map(i => `<div class="pillar-item-block" onclick="togglePillarItem(this)"><div class="pillar-item-head"><div class="pillar-item-name">${i.name}</div><span class="health-dot ${i.status==='ok'?'h-ok':'h-err'}"></span></div><div class="pillar-item-body"><div class="pillar-item-divider"></div><div class="pillar-item-script">脚本：${i.mod}</div><div class="pillar-item-desc">${i.desc||''}</div></div></div>`).join('')}
      </div>
    </div>
  `).join('');
}

// ═════════════════════════════════════════
// 说明: 21角色
// ═════════════════════════════════════════
async function loadRoles() {
  const groups = [
    {
      title: '核心编排 (5角色)', en: 'Core', cn: '核心编排', icon: 'CO', count: 5,
      color: '#3b82f6',
      bg: 'rgba(59,130,246,.15)',
      roles: [
        { en: 'Router', cn: '路由器', desc: '根据任务特征(关键词/正则/通配符)匹配最优Agent，决定任务由谁处理。是整个编排的入口决策点。', mods: ['maop_plan.py', 'core/dynamic_router.py'] },
        { en: 'Planner', cn: '规划器', desc: '生成执行计划：决定并行/串行调度顺序、重试次数、降级策略。将需求转化为可执行的DAG。', mods: ['maop_plan.py', 'engine.py'] },
        { en: 'Orchestrator', cn: '编排器', desc: '驱动Plan-Execute-Verify主循环，协调各阶段流转，管理整体生命周期和状态机。', mods: ['maop_loop.py', 'concurrency.py'] },
        { en: 'Worker', cn: '执行器', desc: '调用Agent CLI执行具体任务，收集输出、状态和延迟统计。是实际干活的角色。', mods: ['maop_execute.py', 'delegate/dispatcher.py'] },
        { en: 'Evaluator', cn: '评估器', desc: '三层门控验证：输出完整性(非空) + 结构正确性(schema) + 语义合理性(置信度)。验证失败触发Feedback Loop。', mods: ['maop_verify.py', 'core/guardrail.py'] },
      ]
    },
    {
      title: '调度记忆 (4角色)', en: 'Dispatch', cn: '调度记忆', icon: 'DM', count: 4,
      color: '#2dd4bf',
      bg: 'rgba(45,212,191,.15)',
      roles: [
        { en: 'Dispatcher', cn: '调度器', desc: '将任务分发给Agent CLI，管理降级链(primary->fallback->default)和重试策略。负责"找对人干活"。', mods: ['delegate/dispatcher.py'] },
        { en: 'Memory', cn: '记忆器', desc: 'FTS5全文检索 + 向量相似度搜索 + 深度记忆追踪。负责"记住经验"和"找回相关上下文"。', mods: ['memory/store.py', 'core/vector.py'] },
        { en: 'Knowledge', cn: '知识器', desc: '关联记忆图谱、经验蒸馏(从执行历史提取可复用规则)和上下文注入(将相关记忆注入Prompt)。', mods: ['memory/store.py', 'core/analyzer.py'] },
        { en: 'Consolidator', cn: '记忆合并器', desc: 'Dream Memory Consolidation四阶段：提取→合并→精炼→写入。在系统空闲时自动整合记忆，将碎片经验蒸馏为结构化知识。', mods: ['memory/consolidator.py'] },
      ]
    },
    {
      title: '安全治理 (4角色)', en: 'Security', cn: '安全治理', icon: 'SG', count: 4,
      color: '#ef4444',
      bg: 'rgba(239,68,68,.15)',
      roles: [
        { en: 'Guardrail', cn: '护栏器', desc: '输入/输出安全校验：防止prompt注入、检测越权访问、过滤敏感信息。是Agent安全的第一道防线。', mods: ['core/guardrail.py'] },
        { en: 'Sandbox', cn: '沙箱器', desc: '为Agent执行提供隔离环境：限制文件系统访问范围、网络白名单、资源配额。防止Agent越界操作。', mods: ['core/sandbox.py', 'core/runtime.py'] },
        { en: 'HumanProxy', cn: '人工代理', desc: '敏感操作(如删除/部署/花钱)需人工确认时的交互通道。支持审批队列和超时自动拒绝。', mods: ['core/human_proxy.py'] },
        { en: 'Auth', cn: '认证器', desc: 'API Key验证、JWT令牌解析、RBAC权限检查和会话管理。确保只有授权请求才能访问编排系统。', mods: ['core/auth.py', 'core/middleware.py'] },
      ]
    },
    {
      title: '数据通信 (3角色)', en: 'Data', cn: '数据通信', icon: 'DC', count: 3,
      color: '#818cf8',
      bg: 'rgba(129,140,248,.15)',
      roles: [
        { en: 'ToolManager', cn: '工具管理器', desc: 'Skills和MCP工具的注册、发现和调用。管理工具元数据(参数schema/权限级别)和调用日志。', mods: ['core/tool_manager.py'] },
        { en: 'Monitor', cn: '监控器', desc: 'Counter/Gauge/Histogram指标采集，支持Prometheus导出。监控Agent成功率、延迟和资源使用。', mods: ['core/monitoring.py', 'core/timeseries.py'] },
        { en: 'EventBus', cn: '事件总线', desc: '模块间异步通信：发布订阅模式 + 事件溯源。解耦各模块，支持水平扩展。', mods: ['core/event_bus.py', 'core/message_queue.py'] },
      ]
    },
    {
      title: '模型管理 (2角色)', en: 'Model', cn: '模型管理', icon: 'MM', count: 2,
      color: '#0ea5e9',
      bg: 'rgba(14,165,233,.15)',
      roles: [
        { en: 'ModelManager', cn: '模型管理器', desc: '统一管理所有AI模型：从models.yaml加载模型注册中心，按任务需求+配额+预算选择最优模型，管理降级链和fallback。', mods: ['model/registry.py', 'model/selector.py', 'model/fallback.py'] },
        { en: 'BudgetGuard', cn: '预算守卫', desc: 'Token消耗与费用预算管理，按Agent/模型/时间窗口追踪配额使用量，超预算时拒绝调用或自动降级到更便宜模型。', mods: ['model/budget.py', 'model/quota.py'] },
      ]
    },
    {
      title: '平台控制 (2角色)', en: 'Platform', cn: '平台控制', icon: 'PC', count: 2,
      color: '#f43f5e',
      bg: 'rgba(244,63,94,.15)',
      roles: [
        { en: 'ControlPlane', cn: '控制平面', desc: '统一平台控制面API，收敛task/agent/model/config/system控制入口为/api/control/*，所有控制动作带审计事件。', mods: ['control/plane.py'] },
        { en: 'Auditor', cn: '审计器', desc: '记录所有控制操作的审计日志：谁在何时做了什么、结果如何。支持审计回溯和合规检查。', mods: ['control/audit.py'] },
      ]
    },
    {
      title: '基础设施 (3角色)', en: 'Infra', cn: '基础设施', icon: 'IF', count: 3,
      color: '#f97316',
      bg: 'rgba(249,115,22,.15)',
      roles: [
        { en: 'Evolve', cn: '进化器', desc: '分析执行性能数据，生成优化建议(如调整路由权重/修改重试策略)，将经验蒸馏为可复用知识。是系统"自我变强"的引擎。', mods: ['evolve.py', 'core/analyzer.py'] },
        { en: 'LoadBalancer', cn: '负载均衡器', desc: '多Agent实例间的加权路由和健康检查。支持轮询/最少连接/加权随机策略，自动剔除不健康实例。', mods: ['core/load_balancer.py', 'core/circuit_breaker.py'] },
        { en: 'WorkerPool', cn: '工作池', desc: '管理Worker实例池：IO-bound用asyncio事件循环，CPU-bound用ProcessPoolExecutor。支持并行执行和资源隔离。', mods: ['core/worker_pool.py', 'concurrency.py'] },
      ]
    },
  ];
  el('roles-content').innerHTML = groups.map(g => `
    <div class="role-section">
      <div class="role-section-head">
        <div class="role-section-icon" style="background:${g.bg};color:${g.color}">${g.icon}</div>
        <div class="role-section-title" style="color:${g.color}">${g.en}</div>
        <div class="role-section-cn">${g.cn}</div>
        <div class="role-section-count">${g.count} 角色</div>
      </div>
      <div class="role-cards">
        ${g.roles.map(r => `
          <div class="role-card">
            <div class="role-card-head" onclick="toggleRoleCard(this.parentElement)">
              <div class="role-card-icon" style="background:${g.bg};color:${g.color}">${r.en[0]}</div>
              <div class="role-card-namebox">
                <div class="role-card-name">${r.en}</div>
                <div class="role-card-cn">${r.cn}</div>
              </div>
              <div class="role-card-status"></div>
              <div class="role-card-toggle">▾</div>
            </div>
            <div class="role-card-body">
              <div class="role-card-divider" style="background:${g.color};opacity:.4"></div>
              <div class="role-card-script">脚本：${r.mods.join(', ')}</div>
              <div class="role-card-desc">${r.desc}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

// ═════════════════════════════════════════
// 说明: 66模块架构
// ═════════════════════════════════════════
function modEn(fn) {
  const sp = {cli:'CLI',maop:'MAOP',dag:'DAG',db:'DB',tls:'TLS',kv:'KV',fts:'FTS',api:'API',sse:'SSE',lru:'LRU',ttl:'TTL',io:'IO',cpu:'CPU',rbac:'RBAC',jwt:'JWT'};
  return fn.replace(/\.py$/, '').split('_').map(w => sp[w] || (w.length<=2 ? w.toUpperCase() : w.charAt(0).toUpperCase()+w.slice(1))).join(' ');
}
async function loadModules() {
  const packages = [
    {
      name: '顶层包 maop/', en: 'maop/', cn: '顶层包', count: 10, icon: 'P', color: '#84cc16', bg: 'rgba(132,204,22,.15)',
      mods: [
        { name: 'cli.py', cn: '命令行入口', desc: 'CLI入口，支持run/validate/doctor/deploy子命令，是用户与MAOP交互的第一触点', exports: 'main()' },
        { name: 'maop_loop.py', cn: '主编排循环', desc: 'Plan-Execute-Verify主循环驱动器，管理整体生命周期、状态流转和Feedback Loop', exports: 'MaopLoop, LoopResult' },
        { name: 'maop_plan.py', cn: '规划引擎', desc: '路由匹配(正则+通配符)和执行策略生成(并行/串行/重试/降级)', exports: 'PlanEngine' },
        { name: 'maop_execute.py', cn: '执行引擎', desc: '按计划调度Agent，支持TaskPool并行和串行模式，含熔断器和降级链', exports: 'ExecuteEngine' },
        { name: 'maop_verify.py', cn: '验证引擎', desc: '三层门控：输出完整性(非空)+结构正确性(schema)+语义合理性(置信度)', exports: 'VerifyEngine' },
        { name: 'engine.py', cn: 'DAG拓扑引擎', desc: '有向无环图拓扑排序，解析任务依赖关系，决定执行顺序', exports: 'DAGEngine' },
        { name: 'evolve.py', cn: '自进化引擎', desc: '分析执行性能，生成优化建议，更新路由权重和策略参数', exports: 'EvolveEngine' },
        { name: 'concurrency.py', cn: '并发控制器', desc: 'asyncio TaskPool并行执行和SSE流式输出', exports: 'TaskPool, SSEStream' },
        { name: 'deploy.py', cn: '部署入口', desc: 'Docker容器化部署和配置管理', exports: 'deploy()' },
        { name: 'prompt_manager.py', cn: '提示词管理器', desc: '模板渲染、变量注入和版本管理，支持多模板格式', exports: 'PromptManager' },
      ]
    },
    {
      name: 'config/ 配置子包', en: 'config/', cn: '配置子包', count: 3, icon: 'C', color: '#06b6d4', bg: 'rgba(6,182,212,.15)',
      mods: [
        { name: 'loader.py', cn: '配置加载器', desc: 'YAML解析+Pydantic校验，支持多环境配置覆盖', exports: 'ConfigLoader, MaopConfig' },
        { name: 'settings.py', cn: '设置模型', desc: 'Pydantic BaseModel定义，类型安全的配置schema', exports: 'Settings' },
        { name: 'hot_reload.py', cn: '热重载器', desc: '文件监听+配置自动刷新，无需重启即可更新配置', exports: 'HotReloader' },
      ]
    },
    {
      name: 'control/ 控制面(2)', en: 'control/', cn: '控制面', count: 2, icon: 'A', color: '#f43f5e', bg: 'rgba(244,63,94,.15)',
      mods: [
        { name: 'audit.py', cn: '审计日志', desc: '控制操作审计事件记录，所有控制动作带audit event', exports: 'AuditEvent, AuditLog' },
        { name: 'plane.py', cn: '控制平面', desc: '统一控制面API，收敛task/agent/model/config/system控制入口', exports: 'ControlPlane, ActionResult' },
      ]
    },
    {
      name: 'core/ 基础设施(31)', en: 'core/', cn: '基础设施', count: 31, icon: 'K', color: '#f97316', bg: 'rgba(249,115,22,.15)',
      mods: [
        { name: 'analyzer.py', cn: '需求分析引擎', desc: '语义拆解+依赖DAG构建+复杂度评估，将自然语言需求转为结构化任务', exports: 'RequirementAnalyzer' },
        { name: 'auth.py', cn: '认证管理器', desc: 'API Key验证+JWT解析+RBAC权限检查', exports: 'AuthManager' },
        { name: 'bloom_filter.py', cn: '布隆过滤器', desc: '概率型去重数据结构，Memory快速判重，误判率<0.1%', exports: 'BloomFilter' },
        { name: 'cache.py', cn: 'LRU缓存', desc: '最近最少使用+TTL过期双重淘汰策略', exports: 'LRUCache' },
        { name: 'cache_guard.py', cn: '缓存防护', desc: '防穿透(空值缓存)+防击穿(SingleFlight)+防雪崩(TTL抖动)', exports: 'CacheGuard, SingleFlight' },
        { name: 'circuit_breaker.py', cn: '熔断器', desc: '三态(关闭/打开/半开)+SQLite持久化+降级链+健康检查', exports: 'CircuitBreaker' },
        { name: 'context_compressor.py', cn: '上下文压缩', desc: '结构化上下文压缩，减少Token消耗，保留关键信息', exports: 'ContextCompressor' },
        { name: 'data.py', cn: '数据层', desc: 'SQLite+FTS5全文+JSON1扩展，统一数据访问接口', exports: 'DataLayer' },
        { name: 'db_backup.py', cn: '数据库备份', desc: '增量备份+全量备份+自动恢复', exports: 'DBBackup' },
        { name: 'dynamic_router.py', cn: '动态路由器', desc: '按健康数据+委派历史动态评分Agent，缓存30秒', exports: 'DynamicRouter' },
        { name: 'error_schema.py', cn: '错误模式', desc: '错误分类+结果封装+错误码标准化', exports: 'MaopResult, ErrorSchema' },
        { name: 'event_bus.py', cn: '事件总线', desc: '发布订阅模式+事件溯源+异步通信', exports: 'EventBus' },
        { name: 'filelock.py', cn: '文件锁', desc: '跨进程互斥锁，防止并发写入冲突', exports: 'FileLock' },
        { name: 'guardrail.py', cn: '安全护栏', desc: '输入/输出安全校验，防注入、防越权、防泄露', exports: 'Guardrail' },
        { name: 'human_proxy.py', cn: '人工代理', desc: '敏感操作审批队列，支持超时自动拒绝', exports: 'HumanProxy' },
        { name: 'kv_store.py', cn: '轻量KV存储', desc: 'SQLite-backed键值存储，支持TTL和命名空间', exports: 'KVStore' },
        { name: 'load_balancer.py', cn: '负载均衡器', desc: '加权路由+健康检查+自动剔除不健康实例', exports: 'LoadBalancer' },
        { name: 'log_rotate.py', cn: '日志轮转', desc: '按大小/时间自动切割日志文件，保留最近N份', exports: 'LogRotator' },
        { name: 'message_queue.py', cn: '消息队列', desc: 'SQLite持久化+消费组+延迟队列+幂等消费', exports: 'MessageQueue' },
        { name: 'middleware.py', cn: '中间件', desc: '请求处理链：认证->限流->日志->业务->响应', exports: 'Middleware' },
        { name: 'migration.py', cn: '数据迁移', desc: '版本管理+schema升级+回滚支持', exports: 'Migration' },
        { name: 'monitoring.py', cn: '监控埋点', desc: 'Counter/Gauge/Histogram+Prometheus导出', exports: 'StructuredLogger, Counter' },
        { name: 'rate_limiter.py', cn: '速率限制器', desc: '令牌桶算法，支持突发流量和平滑限流', exports: 'RateLimiter' },
        { name: 'runtime.py', cn: '执行环境抽象', desc: 'Local/Isolated两种运行时，支持沙箱和容器', exports: 'Runtime, LocalRuntime' },
        { name: 'sandbox.py', cn: '沙箱隔离', desc: '文件系统限制+网络白名单+资源配额', exports: 'Sandbox' },
        { name: 'state_classifier.py', cn: '状态分类器', desc: '后台任务状态分类(4种状态)，自动归类运行中任务', exports: 'StateClassifier' },
        { name: 'timeseries.py', cn: '时序数据', desc: '降采样+聚合+滑动窗口，用于性能趋势分析', exports: 'TimeSeries' },
        { name: 'tls.py', cn: 'TLS加密', desc: '证书管理+HTTPS配置+加密通信', exports: 'TLSConfig' },
        { name: 'tool_manager.py', cn: '工具管理器', desc: 'Skills/MCP工具注册、发现和调用，管理权限和日志', exports: 'ToolManager' },
        { name: 'vector.py', cn: '向量搜索', desc: '纯Python实现，余弦相似度+TopK检索，无外部依赖', exports: 'VectorStore' },
        { name: 'worker_pool.py', cn: 'Worker池', desc: 'IO-bound(asyncio)+CPU-bound(ProcessPool)双模式并行', exports: 'WorkerPool' },
      ]
    },
    {
      name: 'dashboard/ 面板(3)', en: 'dashboard/', cn: '面板', count: 3, icon: 'D', color: '#ec4899', bg: 'rgba(236,72,153,.15)',
      mods: [
        { name: 'server.py', cn: 'Dashboard后端', desc: 'FastAPI后端，101个API端点+WebSocket实时推送', exports: 'app' },
        { name: 'data_bridge.py', cn: '数据桥接', desc: '后端到前端数据格式转换和适配', exports: 'DataBridge' },
        { name: 'provider.py', cn: '数据提供者', desc: '统一数据源管理，聚合各模块数据', exports: 'DataProvider' },
      ]
    },
    {
      name: 'dashboard/routers/ 路由(7)', en: 'dashboard/routers/', cn: '路由包', count: 7, icon: 'R', color: '#f472b6', bg: 'rgba(244,114,182,.15)',
      mods: [
        { name: 'control.py', cn: '控制路由', desc: '控制面API路由：run/stop/pause/resume/validate/maintain', exports: 'router' },
        { name: 'data.py', cn: '数据路由', desc: '数据查询API路由：agents/logs/skills/mcp/modules/roles', exports: 'router' },
        { name: 'evolve.py', cn: '进化路由', desc: '自进化API路由：status/analyze/suggestions/report', exports: 'router' },
        { name: 'memory.py', cn: '记忆路由', desc: '记忆系统API路由：deep/search/trace/stats/neural', exports: 'router' },
        { name: 'model.py', cn: '模型路由', desc: '模型管理API路由：registry/list/switch/budget/policies', exports: 'router' },
        { name: 'state.py', cn: '状态路由', desc: '共享状态管理：路径/DataBridge/缓存/任务', exports: 'state' },
        { name: 'system.py', cn: '系统路由', desc: '系统级API路由：health/overview/metrics/subsystems', exports: 'router' },
      ]
    },
    {
      name: 'delegate/ 调度(2)', en: 'delegate/', cn: '调度', count: 2, icon: 'S', color: '#8b5cf6', bg: 'rgba(139,92,246,.15)',
      mods: [
        { name: 'dispatcher.py', cn: '委托调度器', desc: 'Agent任务分发+降级链(primary->fallback->default)+重试+ModelSelector接入', exports: 'Dispatcher' },
        { name: 'doc_pipeline_adapter.py', cn: '文档管线适配器', desc: 'Doc-Pipeline工作流适配：DAG+质量门控+熔断器+事件钩子', exports: 'run_pipeline' },
      ]
    },
    {
      name: 'memory/ 记忆(2)', en: 'memory/', cn: '记忆', count: 2, icon: 'M', color: '#14b8a6', bg: 'rgba(20,184,166,.15)',
      mods: [
        { name: 'store.py', cn: '记忆存储', desc: 'FTS5全文+向量搜索+深度记忆追踪+经验蒸馏', exports: 'MemoryStore' },
        { name: 'consolidator.py', cn: '记忆合并器', desc: 'Dream Memory Consolidation四阶段：提取→合并→精炼→写入', exports: 'DreamConsolidator' },
      ]
    },
    {
      name: 'model/ 模型管理(6)', en: 'model/', cn: '模型管理', count: 6, icon: 'MM', color: '#0ea5e9', bg: 'rgba(14,165,233,.15)',
      mods: [
        { name: 'registry.py', cn: '模型注册中心', desc: '模型权威注册中心，从models.yaml加载所有可用模型', exports: 'ModelRegistry' },
        { name: 'schema.py', cn: '模型Schema', desc: 'Pydantic模型定义：ModelEntry/ProviderEntry/QuotaLimit/BudgetConfig', exports: 'ModelEntry, ProviderEntry' },
        { name: 'selector.py', cn: '模型选择器', desc: '按任务需求+配额+预算选择最优模型，支持优先级排序', exports: 'ModelSelector' },
        { name: 'fallback.py', cn: '降级管理器', desc: '模型降级链管理，主模型不可用时自动切换备用', exports: 'FallbackManager' },
        { name: 'quota.py', cn: '配额管理器', desc: '模型调用配额追踪与限制，支持按Agent/模型/时间窗口', exports: 'QuotaEnforcer' },
        { name: 'budget.py', cn: '预算管理器', desc: 'Token/费用预算管理，超预算时拒绝或降级', exports: 'BudgetGuard' },
      ]
    },
  ];
  el('modules-content').innerHTML = packages.map(p => `
    <div class="mod-section">
      <div class="mod-section-head">
        <div class="mod-section-icon" style="background:${p.bg};color:${p.color}">${p.icon}</div>
        <div class="mod-section-title" style="color:${p.color}">${p.en}</div>
        <div class="mod-section-cn">${p.cn}</div>
        <div class="mod-section-count">${p.count} 模块</div>
      </div>
      <div class="mod-section-divider"></div>
      <div class="mod-cards">
        ${p.mods.map(m => `
          <div class="mod-card" onclick="toggleModCard(this)">
            <div class="mod-card-head">
              <div class="mod-card-namebox">
                <div class="mod-card-name">${modEn(m.name)}</div>
                <div class="mod-card-cn">${m.cn}</div>
              </div>
              <div class="mod-card-dot"></div>
            </div>
            <div class="mod-card-body">
              <div class="mod-card-divider"></div>
              <div class="mod-card-script">脚本：${m.name}</div>
              <div class="mod-card-desc">${m.desc}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

// ═════════════════════════════════════════
// 说明: 工作流程
// ═════════════════════════════════════════
async function loadWorkflow() {
  const roleFlows = [
    { en: 'Router', cn: '路由器', color: '#3b82f6', bg: 'rgba(59,130,246,.15)', mod: 'maop_plan.py',
      steps: [
        { t: '接收任务文本', d: '从Orchestrator收到用户原始需求' },
        { t: '特征提取', d: '识别关键词、任务类型、紧急程度' },
        { t: '匹配路由规则', d: '按正则/通配符匹配config中的路由表' },
        { t: '选择最优Agent', d: '若多个匹配，用LoadBalancer加权选择' },
        { t: '输出Agent ID + 置信度', d: '传递给Planner进入下一阶段' },
      ]},
    { en: 'Planner', cn: '规划器', color: '#3b82f6', bg: 'rgba(59,130,246,.15)', mod: 'maop_plan.py + engine.py',
      steps: [
        { t: '接收Agent ID + 需求', d: '从Router获取路由结果' },
        { t: '读取Agent配置', d: '从config/loader加载Agent能力和约束' },
        { t: '生成执行策略', d: '决定并行/串行、重试次数、降级路径' },
        { t: '构建DAG', d: 'engine.py拓扑排序，解析任务间依赖' },
        { t: '输出ExecutionPlan', d: '传递给Orchestrator驱动执行' },
      ]},
    { en: 'Orchestrator', cn: '编排器', color: '#3b82f6', bg: 'rgba(59,130,246,.15)', mod: 'maop_loop.py',
      steps: [
        { t: '启动主循环', d: '初始化状态机，进入Plan阶段' },
        { t: '调用Plan -> Execute -> Verify', d: '依次驱动三个引擎阶段' },
        { t: '判断验证结果', d: '检查Evaluator的置信度和门控结果' },
        { t: '通过 -> 结束 / 失败 -> Feedback Loop', d: '失败时注入修正建议，回到Plan(最多2轮)' },
      ]},
    { en: 'Worker', cn: '执行器', color: '#3b82f6', bg: 'rgba(59,130,246,.15)', mod: 'maop_execute.py',
      steps: [
        { t: '接收单个任务', d: '从ExecutionPlan中获取分配的任务' },
        { t: '调用Agent CLI', d: '通过subprocess调用外部Agent命令行' },
        { t: '收集输出和状态', d: '捕获stdout/stderr/exit_code' },
        { t: '记录延迟统计', d: '写入monitoring时序数据' },
        { t: '返回执行结果', d: '传递给Evaluator进行验证' },
      ]},
    { en: 'Evaluator', cn: '评估器', color: '#3b82f6', bg: 'rgba(59,130,246,.15)', mod: 'maop_verify.py',
      steps: [
        { t: '接收执行结果', d: '从Worker获取Agent输出' },
        { t: '门控1: 输出完整性', d: '检查非空、长度合理、无截断' },
        { t: '门控2: 结构正确性', d: '校验JSON schema/字段类型/必填项' },
        { t: '门控3: 语义合理性', d: '向量相似度评估，置信度>阈值则通过' },
        { t: '输出验证结果 + 修正建议', d: '失败时建议传递给Feedback Loop' },
      ]},
    { en: 'Dispatcher', cn: '调度器', color: '#2dd4bf', bg: 'rgba(45,212,191,.15)', mod: 'delegate/dispatcher.py',
      steps: [
        { t: '接收任务分发请求', d: '从ExecuteEngine获取待执行任务' },
        { t: '查找Agent实例', d: '在注册表中查找匹配的Agent' },
        { t: '检查熔断器状态', d: 'CircuitBreaker判断Agent是否可用' },
        { t: '分发 -> 成功返回 / 失败进入降级链', d: 'primary -> fallback -> default' },
        { t: '降级链 exhausted -> 报告失败', d: '触发Orchestrator的Feedback Loop' },
      ]},
    { en: 'Memory', cn: '记忆器', color: '#2dd4bf', bg: 'rgba(45,212,191,.15)', mod: 'memory/store.py',
      steps: [
        { t: '接收查询请求', d: '来自Knowledge或Orchestrator的上下文查询' },
        { t: 'FTS5全文检索', d: 'SQLite FTS5匹配关键词，按相关度排序' },
        { t: '向量相似度搜索', d: 'VectorStore计算余弦相似度，TopK检索' },
        { t: '合并排序+去重', d: 'BloomFilter快速去重，合并两路结果' },
        { t: '返回记忆条目', d: '供Knowledge进行上下文注入' },
      ]},
    { en: 'Knowledge', cn: '知识器', color: '#2dd4bf', bg: 'rgba(45,212,191,.15)', mod: 'memory/store.py + analyzer.py',
      steps: [
        { t: '接收上下文请求', d: 'Orchestrator在Plan前请求相关记忆' },
        { t: '调用Memory检索', d: '获取历史相似任务的执行记录' },
        { t: '经验蒸馏', d: '从成功/失败历史提取可复用规则' },
        { t: '上下文注入', d: '将记忆和规则注入Agent的Prompt模板' },
      ]},
    { en: 'Consolidator', cn: '记忆合并器', color: '#2dd4bf', bg: 'rgba(45,212,191,.15)', mod: 'memory/consolidator.py',
      steps: [
        { t: '触发合并(空闲时)', d: '系统空闲时自动触发Dream Memory Consolidation' },
        { t: '提取(Extract)', d: '从近期执行记录中提取有价值的经验片段' },
        { t: '合并(Merge)', d: '将碎片经验按主题合并，去除冗余' },
        { t: '精炼(Refine)', d: '对合并后知识进行质量评估和精炼' },
        { t: '写入(Write)', d: '将精炼后知识写入长期记忆，供后续任务复用' },
      ]},
    { en: 'Guardrail', cn: '护栏器', color: '#ef4444', bg: 'rgba(239,68,68,.15)', mod: 'core/guardrail.py',
      steps: [
        { t: '接收输入/输出', d: '在Execute前和Verify后各检查一次' },
        { t: '注入检测', d: '检查是否含prompt injection模式' },
        { t: '越权检测', d: '验证请求是否超出Agent权限范围' },
        { t: '敏感信息过滤', d: '脱敏PII数据，防止泄露' },
        { t: '通过 -> 放行 / 拦截 -> 记录告警', d: '拦截事件写入EventBus' },
      ]},
    { en: 'Sandbox', cn: '沙箱器', color: '#ef4444', bg: 'rgba(239,68,68,.15)', mod: 'core/sandbox.py',
      steps: [
        { t: '接收执行请求', d: 'Worker请求在隔离环境中执行Agent' },
        { t: '创建隔离环境', d: 'Runtime抽象层选择Local或Isolated模式' },
        { t: '限制文件/网络访问', d: '设置白名单和资源配额' },
        { t: '执行Agent -> 收集结果', d: '在沙箱内运行，超时自动终止' },
        { t: '清理环境', d: '销毁临时文件和进程' },
      ]},
    { en: 'HumanProxy', cn: '人工代理', color: '#ef4444', bg: 'rgba(239,68,68,.15)', mod: 'core/human_proxy.py',
      steps: [
        { t: '接收敏感操作请求', d: '如删除/部署/大额消耗等高风险操作' },
        { t: '加入审批队列', d: 'SQLite持久化，等待人工确认' },
        { t: '等待确认', d: '通过Dashboard或通知推送给人' },
        { t: '批准 -> 执行 / 拒绝 -> 取消 / 超时 -> 自动拒绝', d: '默认超时30分钟' },
      ]},
    { en: 'Auth', cn: '认证器', color: '#ef4444', bg: 'rgba(239,68,68,.15)', mod: 'core/auth.py',
      steps: [
        { t: '接收API请求', d: 'Middleware链的第一环' },
        { t: '提取Token', d: '从Header/Cookie/Query中提取API Key或JWT' },
        { t: '验证签名+检查权限', d: 'RBAC角色检查，验证操作权限' },
        { t: '通过 -> 下一步 / 失败 -> 401/403', d: '记录认证日志' },
      ]},
    { en: 'ToolManager', cn: '工具管理器', color: '#818cf8', bg: 'rgba(129,140,248,.15)', mod: 'core/tool_manager.py',
      steps: [
        { t: '接收工具调用请求', d: 'Agent请求调用某个Skill或MCP工具' },
        { t: '查找工具注册信息', d: '在注册表中查找工具元数据' },
        { t: '检查权限+参数校验', d: '验证调用方是否有权使用该工具' },
        { t: '调用工具 -> 记录日志', d: '写入调用日志和耗时统计' },
        { t: '返回工具结果', d: '传递给Agent' },
      ]},
    { en: 'Monitor', cn: '监控器', color: '#818cf8', bg: 'rgba(129,140,248,.15)', mod: 'core/monitoring.py',
      steps: [
        { t: '采集运行指标', d: 'Counter(计数)/Gauge(瞬时)/Histogram(分布)' },
        { t: '更新时序数据', d: '写入TimeSeries，支持降采样' },
        { t: 'Prometheus导出', d: '/metrics端点供Prometheus抓取' },
        { t: '告警判断', d: '阈值触发时通过EventBus发布告警事件' },
      ]},
    { en: 'EventBus', cn: '事件总线', color: '#818cf8', bg: 'rgba(129,140,248,.15)', mod: 'core/event_bus.py',
      steps: [
        { t: '接收事件发布', d: '各模块通过publish()发布事件' },
        { t: '查找订阅者', d: '匹配事件类型，找到所有订阅回调' },
        { t: '异步通知订阅者', d: '不阻塞发布方，异步执行回调' },
        { t: '事件溯源记录', d: '写入MessageQueue持久化，支持回放' },
      ]},
    { en: 'Evolve', cn: '进化器', color: '#f97316', bg: 'rgba(249,115,22,.15)', mod: 'evolve.py',
      steps: [
        { t: '收集执行性能数据', d: '从Monitor获取各Agent成功率和延迟' },
        { t: '分析性能瓶颈', d: '识别低效Agent、高频失败路径' },
        { t: '生成优化建议', d: '如调整路由权重、增加重试次数' },
        { t: '更新策略参数', d: '自动或人工确认后更新配置' },
        { t: '知识沉淀', d: '将经验写入Memory供后续复用' },
      ]},
    { en: 'LoadBalancer', cn: '负载均衡器', color: '#f97316', bg: 'rgba(249,115,22,.15)', mod: 'core/load_balancer.py',
      steps: [
        { t: '接收路由请求', d: 'Dispatcher请求选择Agent实例' },
        { t: '检查健康状态', d: '过滤不健康实例(熔断器打开的)' },
        { t: '加权选择', d: '按权重/最少连接/轮询策略选择' },
        { t: '转发请求 -> 记录负载', d: '更新实例负载计数' },
      ]},
    { en: 'WorkerPool', cn: '工作池', color: '#f97316', bg: 'rgba(249,115,22,.15)', mod: 'core/worker_pool.py',
      steps: [
        { t: '接收并行任务集', d: 'ExecuteEngine提交多个独立任务' },
        { t: '判断IO/CPU密集', d: 'IO-bound用asyncio，CPU-bound用ProcessPool' },
        { t: '分配到对应池', d: '资源隔离，防止相互影响' },
        { t: '并行执行 -> 收集结果', d: '等待全部完成或超时' },
        { t: '返回结果列表', d: '保持提交顺序' },
      ]},
    { en: 'ModelManager', cn: '模型管理器', color: '#0ea5e9', bg: 'rgba(14,165,233,.15)', mod: 'model/registry.py + selector.py',
      steps: [
        { t: '加载模型注册中心', d: '从config/models.yaml加载所有可用模型和Provider配置' },
        { t: '接收模型选择请求', d: 'Dispatcher在分发前请求选择最优模型' },
        { t: '评估候选模型', d: '按任务需求+配额可用性+预算限制综合评分' },
        { t: '返回最优模型', d: '选定模型注入AgentConfig，供Dispatcher使用' },
        { t: '降级链触发', d: '主模型不可用时，FallbackManager自动切换备用模型' },
      ]},
    { en: 'BudgetGuard', cn: '预算守卫', color: '#0ea5e9', bg: 'rgba(14,165,233,.15)', mod: 'model/budget.py + quota.py',
      steps: [
        { t: '接收调用前检查', d: '每次模型调用前检查Token和费用配额' },
        { t: '查询当前用量', d: '按Agent/模型/时间窗口查询已消耗量' },
        { t: '判断是否超限', d: '对比配额限制与当前用量' },
        { t: '允许 -> 放行 / 超限 -> 拒绝或降级', d: '超预算时拒绝调用或降级到更便宜模型' },
        { t: '记录消耗', d: '调用完成后更新Token和费用计数' },
      ]},
    { en: 'ControlPlane', cn: '控制平面', color: '#f43f5e', bg: 'rgba(244,63,94,.15)', mod: 'control/plane.py',
      steps: [
        { t: '接收控制请求', d: '统一/api/control/*入口，收敛所有控制操作' },
        { t: '权限校验', d: '验证操作者是否有权执行该控制动作' },
        { t: '执行控制操作', d: 'task/agent/model/config/system五类控制' },
        { t: '记录审计事件', d: '通过Auditor记录谁在何时做了什么' },
        { t: '返回结果', d: '操作结果+审计ID返回给调用方' },
      ]},
    { en: 'Auditor', cn: '审计器', color: '#f43f5e', bg: 'rgba(244,63,94,.15)', mod: 'control/audit.py',
      steps: [
        { t: '接收审计事件', d: 'ControlPlane每次控制操作后发布审计事件' },
        { t: '记录操作详情', d: '操作者/时间/动作/目标/结果/上下文' },
        { t: '持久化存储', d: '写入SQLite审计日志，不可篡改' },
        { t: '支持回溯查询', d: '按时间/操作者/动作类型检索审计记录' },
      ]},
  ];
  let html = '<div style="margin-bottom:14px;font-size:13px;color:var(--text2)">点击任意角色卡片展开查看详细工作流程</div>';
  roleFlows.forEach(function(r, idx) {
    html += '<div class="wf-role-card" id="wf-role-' + idx + '">' +
      '<div class="wf-role-header" onclick="toggleWfRole(' + idx + ')">' +
        '<div class="wf-role-icon" style="background:' + r.bg + ';color:' + r.color + '">' + r.en[0] + '</div>' +
        '<div class="wf-role-namebox">' +
          '<div class="wf-role-name">' + r.en + '</div>' +
          '<div class="wf-role-cn">' + r.cn + '<span class="wf-role-mod">' + r.mod + '</span></div>' +
        '</div>' +
        '<div class="wf-role-toggle">&#9662;</div>' +
      '</div>' +
      '<div class="wf-role-body">' +
        '<div class="wf-role-header-divider"></div>' +
        r.steps.map(function(s, i) {
          return '<div class="wf-flow-step">' +
            '<div class="wf-flow-dot" style="background:' + r.color + '"></div>' +
            '<div class="wf-flow-content">' +
              '<div class="wf-flow-title">' + (i+1) + '. ' + s.t + '</div>' +
              '<div class="wf-flow-desc">' + s.d + '</div>' +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>' +
    '</div>';
  });
  html += '<div class="card" style="margin-top:14px"><h3>主循环流程 (Orchestrator视角)</h3>' +
    '<div style="font-size:13px;color:var(--text2);line-height:2">' +
      '<b style="color:var(--accent)">需求</b> -> Router(路由) -> <b style="color:var(--success)">Plan</b>(规划) -> Knowledge(注入记忆) -> <b style="color:var(--orange)">Execute</b>(分发+执行) -> Guardrail(护栏) -> <b style="color:var(--cyan)">Verify</b>(三层门控) -> <b style="color:var(--success)">通过</b> / <b style="color:var(--fail)">失败</b> -> Feedback Loop(<=2轮) -> <b style="color:var(--purple)">Evolve</b>(自进化)' +
    '</div></div>';
  el('wf-content').innerHTML = html;
}

async function loadWfExec() {
  const d = await fetchJSON('/api/workflows');
  const wfs = d?.workflows || d || [];
  const arr = arrize(wfs);
  el('tb-wf').innerHTML = arr.map(w =>
    `<tr><td>${esc(w.name||w)}</td><td>${esc(w.file||'')}</td><td><button onclick="runWorkflowByName('${esc(w.name||w)}')" class="btn-sm btn-blue">运行</button></td></tr>`
  ).join('') || '<tr><td colspan=3 class="empty">无工作流</td></tr>';
}

function toggleWfRole(idx) {
  const card = document.getElementById('wf-role-' + idx);
  if (card) card.classList.toggle('open');
}
function toggleModCard(card) {
  card.classList.toggle('expanded');
}
function togglePillarItem(el) {
  el.classList.toggle('expanded');
}
function toggleRoleCard(card) {
  card.classList.toggle('expanded');
}
async function runWorkflow() {
  const name = el('wf-name').value.trim();
  if (!name) { el('wf-msg').innerHTML = '<span class="warn">请输入工作流名称</span>'; return; }
  el('wf-msg').innerHTML = '<span class="info">执行中...</span>';
  const d = await postJSON('/api/control/run', { workflow: name, task: el('wf-task').value });
  el('wf-msg').innerHTML = d ? `<span class="success">已启动: ${esc(JSON.stringify(d))}</span>` : '<span class="warn">执行失败</span>';
}
function runWorkflowByName(name) { el('wf-name').value = name; runWorkflow(); }

// ═════════════════════════════════════════
// 说明: 项目架构
// ═════════════════════════════════════════
async function loadArchitecture() {
  const layers = [
    {
      num: 'L1', en: 'CLI Entry', cn: 'CLI入口层', count: '2模块', color: 'green',
      desc: '命令行接口和部署入口，用户交互的第一触点',
      mods: ['命令行入口', '部署入口']
    },
    {
      num: 'L2', en: 'MaopLoop Orchestration', cn: 'MaopLoop编排层', count: '2模块', color: 'amber',
      desc: '主编排循环和并发控制，驱动Plan-Execute-Verify主流程',
      mods: ['主编排循环', '并发控制']
    },
    {
      num: 'L3', en: 'Engine', cn: '引擎层', count: '6模块', color: 'orange',
      desc: '核心引擎：规划、执行、验证、自进化、DAG和提示词管理',
      mods: ['规划引擎', '执行引擎', '验证引擎', 'DAG拓扑引擎', '自进化引擎', '提示词管理器']
    },
    {
      num: 'L4', en: 'Service', cn: '服务层', count: '12模块', color: 'purple',
      desc: '业务服务：委托调度、记忆存储、记忆合并、文档管线、模型管理(6)、控制面(2)、工具管理、人工代理',
      mods: ['委托调度器', '文档管线适配器', '记忆存储', '记忆合并器', '模型注册中心', '模型选择器', '模型降级器', '配额管理器', '预算管理器', '控制平面', '审计日志', '工具管理器']
    },
    {
      num: 'L5', en: 'Infrastructure', cn: '基础设施层', count: '31模块', color: 'blue',
      desc: '技术基础设施：安全、缓存、消息队列、向量搜索、监控、上下文压缩、动态路由、状态分类等横切关注点',
      mods: ['需求分析', '认证管理', '布隆过滤器', 'LRU缓存', '缓存防护', '熔断器', '上下文压缩', '数据层', '数据库备份', '动态路由', '错误模式', '事件总线', '文件锁', '安全护栏', 'KV存储', '负载均衡', '日志轮转', '消息队列', '中间件', '数据迁移', '监控埋点', '速率限制', '执行环境', '沙箱隔离', '状态分类器', '时序数据', 'TLS加密', '工具管理', '向量搜索', 'Worker池', '人工代理']
    },
    {
      num: 'L6', en: 'Support', cn: '支撑层', count: '13模块', color: 'cyan',
      desc: '配置管理、Dashboard可视化和API路由包，为系统提供运行时支撑和监控能力',
      mods: ['配置加载器', '设置模型', '热重载器', 'Dashboard后端', '数据桥接', '数据提供者', '控制路由', '数据路由', '进化路由', '记忆路由', '模型路由', '状态路由', '系统路由']
    },
  ];
  el('arch-content').innerHTML = `
    <div class="arch-layers-wrap">
      ${layers.map((l, i) => `
        <div class="arch-layer ${l.color}">
          <div class="arch-layer-head">
            <div class="arch-layer-icon" style="background:var(--bg4);border:2px solid var(--border-hi)">${l.num}</div>
            <div class="arch-layer-name">
              <div class="arch-layer-name-en">${l.en}</div>
              <div class="arch-layer-name-cn">${l.cn}</div>
            </div>
            <div class="arch-layer-count">${l.count}</div>
          </div>
          <div class="arch-layer-divider"></div>
          <div class="arch-layer-desc">${l.desc}</div>
          <div class="arch-layer-modules">${l.mods.map(m=>`<div class="arch-mod-card">${esc(m)}<div class="arch-mod-tooltip"><div class="tooltip-title">${esc(m)}</div><div class="tooltip-desc">${esc(l.desc)}</div></div></div>`).join('')}</div>
        </div>
        ${i < layers.length - 1 ? '<div class="arch-flow-arrow"></div>' : ''}
      `).join('')}
    </div>
    <div class="card-row">
      <div class="card">
        <h3>数据流</h3>
        <div class="data-flow-item" style="border-left:3px solid var(--accent)">
          <div class="data-flow-label" style="color:var(--accent)">主流程</div>
          <div class="data-flow-chain">
            <span class="flow-node">用户需求</span><span class="flow-arrow"></span>
            <span class="flow-node">CLI (L1)</span><span class="flow-arrow"></span>
            <span class="flow-node">MaopLoop (L2)</span><span class="flow-arrow"></span>
            <span class="flow-node">引擎 (L3)</span><span class="flow-arrow"></span>
            <span class="flow-node">服务 (L4)</span><span class="flow-arrow"></span>
            <span class="flow-node">基础设施 (L5)</span><span class="flow-arrow"></span>
            <span class="flow-node">支撑 (L6)</span>
          </div>
        </div>
        <div class="data-flow-item" style="border-left:3px solid var(--success)">
          <div class="data-flow-label" style="color:var(--success)">验证反馈</div>
          <div class="data-flow-chain">
            <span class="flow-node">验证失败</span><span class="flow-arrow"></span>
            <span class="flow-node">Feedback Loop</span><span class="flow-arrow"></span>
            <span class="flow-node">重新规划</span><span class="flow-arrow"></span>
            <span class="flow-node">最多2轮重试</span>
          </div>
        </div>
        <div class="data-flow-item" style="border-left:3px solid var(--purple)">
          <div class="data-flow-label" style="color:var(--purple)">自进化</div>
          <div class="data-flow-chain">
            <span class="flow-node">性能分析</span><span class="flow-arrow"></span>
            <span class="flow-node">建议生成</span><span class="flow-arrow"></span>
            <span class="flow-node">策略更新</span><span class="flow-arrow"></span>
            <span class="flow-node">知识沉淀</span>
          </div>
        </div>
      </div>
      <div class="card">
        <h3>关键设计决策</h3>
        <div class="kv"><span>编排模式</span><b>Plan-Execute-Verify</b></div>
        <div class="kv"><span>并发模型</span><b>asyncio + TaskPool</b></div>
        <div class="kv"><span>数据存储</span><b>SQLite + FTS5 + JSON1</b></div>
        <div class="kv"><span>向量搜索</span><b>纯Python (无外部依赖)</b></div>
        <div class="kv"><span>消息队列</span><b>SQLite-backed持久化</b></div>
        <div class="kv"><span>缓存策略</span><b>LRU + TTL + SingleFlight</b></div>
        <div class="kv"><span>容错机制</span><b>熔断器 + 降级链 + 重试</b></div>
        <div class="kv"><span>配置管理</span><b>Pydantic + YAML + 热重载</b></div>
        <div class="kv"><span>部署方式</span><b>Docker多阶段构建</b></div>
        <div class="kv"><span>CI/CD</span><b>GitHub Actions</b></div>
      </div>
    </div>
  `;
}

