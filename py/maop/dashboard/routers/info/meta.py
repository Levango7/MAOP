"""Info metadata endpoints: pillars, roles, modules, workflows, architecture, edition, config.

Endpoints:
    GET /pillars       — 4 pillars of MAOP methodology
    GET /roles         — agent role groups
    GET /modules       — module catalog by package
    GET /workflows     — role-based workflow steps
    GET /architecture  — layered architecture overview
    GET /edition       — current edition info & feature flags
    GET /config        — runtime configuration (non-sensitive)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from maop.dashboard.error_handler import handle_api_errors  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/info", tags=["info"])

PILLARS = [
    {
        "name": "Prompt Engineering", "cn": "提示词工程", "icon": "P",
        "color": "#3b82f6", "bg": "rgba(59,130,246,.15)",
        "desc": "设计和管理Agent的提示词模板、变量注入和版本迭代。确保Agent接收到的指令精确、可复用、可追踪。",
        "items": [
            {"name": "提示词管理", "mod": "prompt_manager.py", "status": "ok", "desc": "统一管理提示词模板，支持版本控制与动态加载"},
            {"name": "路由匹配", "mod": "MAOP_plan.py", "status": "ok", "desc": "根据任务类型自动路由至最优提示词模板"},
            {"name": "安全配置", "mod": "core/guardrail.py", "status": "ok", "desc": "提示词安全策略配置，防止注入与敏感信息泄露"},
            {"name": "变量注入", "mod": "prompt_manager.py", "status": "ok", "desc": "运行时变量动态注入提示词，支持模板插值"},
            {"name": "模板版本", "mod": "prompt_manager.py", "status": "ok", "desc": "提示词模板版本管理与回滚机制"},
            {"name": "语义校验", "mod": "core/guardrail.py", "status": "ok", "desc": "对生成提示词进行语义合法性校验"},
            {"name": "Few-shot 示例", "mod": "prompt_manager.py", "status": "ok", "desc": "少样本示例管理，提升模型输出质量"},
            {"name": "提示词压缩", "mod": "core/context_compressor.py", "status": "ok", "desc": "长提示词自动压缩，降低 Token 消耗"},
        ],
    },
    {
        "name": "Context Engineering", "cn": "上下文工程", "icon": "C",
        "color": "#a78bfa", "bg": "rgba(167,139,250,.15)",
        "desc": "管理Agent的上下文窗口、记忆注入、信息检索和知识关联。决定Agent'知道什么'和'记住什么'。",
        "items": [
            {"name": "记忆存储", "mod": "memory/store.py", "status": "ok", "desc": "分层记忆存储系统，支持短期与长期记忆"},
            {"name": "向量搜索", "mod": "core/vector.py", "status": "ok", "desc": "基于 Embedding 的语义向量检索引擎"},
            {"name": "缓存防护", "mod": "core/cache_guard.py", "status": "ok", "desc": "缓存穿透/击穿/雪崩防护，空值缓存与 TTL 抖动"},
            {"name": "上下文窗口", "mod": "core/context_compressor.py", "status": "ok", "desc": "上下文窗口管理，自动截断与摘要压缩"},
            {"name": "FTS5 全文搜索", "mod": "memory/store.py", "status": "ok", "desc": "SQLite FTS5 全文检索，支持中文分词"},
            {"name": "记忆 TTL 清理", "mod": "memory/consolidator.py", "status": "ok", "desc": "过期记忆自动清理，防止存储膨胀"},
            {"name": "布隆过滤器", "mod": "core/bloom_filter.py", "status": "ok", "desc": "布隆过滤器快速判重，减少无效查询"},
            {"name": "注意力计算", "mod": "core/vector.py", "status": "ok", "desc": "注意力权重计算与上下文优先级排序"},
        ],
    },
    {
        "name": "Harness Engineering", "cn": "驾驭工程", "icon": "H",
        "color": "#f97316", "bg": "rgba(249,115,22,.15)",
        "desc": "Agent的运行时框架——工具调用、执行控制、安全隔离和容错降级。决定Agent'怎么做'和'做得多稳'。",
        "items": [
            {"name": "编排循环", "mod": "MAOP_loop.py", "status": "ok", "desc": "Plan-Execute-Verify 三阶段编排主循环"},
            {"name": "熔断器", "mod": "core/circuit_breaker.py", "status": "ok", "desc": "故障熔断器，连续失败时自动切断调用链"},
            {"name": "安全护栏", "mod": "core/guardrail.py", "status": "ok", "desc": "输入输出安全护栏，拦截危险操作"},
            {"name": "沙箱隔离", "mod": "core/sandbox.py", "status": "ok", "desc": "执行沙箱隔离，限制资源访问与权限"},
            {"name": "降级链", "mod": "delegate/dispatcher.py", "status": "ok", "desc": "多级降级策略，核心功能不可用时自动切换备用方案"},
            {"name": "日志轮转", "mod": "core/log_rotate.py", "status": "ok", "desc": "日志文件自动轮转，按大小与时间切割归档"},
            {"name": "配置热重载", "mod": "config/hot_reload.py", "status": "ok", "desc": "运行时配置热重载，无需重启即可生效"},
            {"name": "速率限制", "mod": "core/rate_limiter.py", "status": "ok", "desc": "API 调用速率限制，防止过载与滥用"},
        ],
    },
    {
        "name": "Loop Engineering", "cn": "循环工程", "icon": "L",
        "color": "#06b6d4", "bg": "rgba(6,182,212,.15)",
        "desc": "多Agent协调——任务分发、负载均衡、结果聚合和自进化。决定Agent团队'怎么配合'和'怎么变强'。",
        "items": [
            {"name": "委托调度", "mod": "delegate/dispatcher.py", "status": "ok", "desc": "Agent 任务委托与调度，支持优先级队列"},
            {"name": "事件总线", "mod": "core/event_bus.py", "status": "ok", "desc": "模块间事件总线通信，解耦组件交互"},
            {"name": "自进化", "mod": "evolve.py", "status": "ok", "desc": "框架自进化引擎，自动优化参数与策略"},
            {"name": "消息队列", "mod": "core/message_queue.py", "status": "ok", "desc": "持久化消息队列，支持异步任务与重试"},
            {"name": "反馈循环", "mod": "MAOP_loop.py", "status": "ok", "desc": "执行反馈循环，验证失败时自动修正重试"},
            {"name": "DAG 引擎", "mod": "engine.py", "status": "ok", "desc": "有向无环图任务调度引擎，支持并行与依赖"},
            {"name": "Worker 池", "mod": "core/worker_pool.py", "status": "ok", "desc": "多 Worker 并行执行池，CPU 核心级隔离"},
            {"name": "负载均衡", "mod": "core/load_balancer.py", "status": "ok", "desc": "Agent 负载均衡，按权重与健康度分配任务"},
        ],
    },
]

ROLES = [
    {
        "title": "核心编排 (5角色)", "en": "Core", "cn": "核心编排", "icon": "CO", "count": 5,
        "color": "#3b82f6", "bg": "rgba(59,130,246,.15)",
        "roles": [
            {"en": "Router", "cn": "路由器", "desc": "根据任务特征匹配最优Agent，决定任务由谁处理。", "mods": ["MAOP_plan.py", "core/dynamic_router.py"]},
            {"en": "Planner", "cn": "规划器", "desc": "生成执行计划：决定并行/串行调度顺序、重试次数、降级策略。", "mods": ["MAOP_plan.py", "engine.py"]},
            {"en": "Orchestrator", "cn": "编排器", "desc": "驱动Plan-Execute-Verify主循环，协调各阶段流转。", "mods": ["MAOP_loop.py", "concurrency.py"]},
            {"en": "Worker", "cn": "执行器", "desc": "调用Agent CLI执行具体任务，收集输出、状态和延迟统计。", "mods": ["MAOP_execute.py", "delegate/dispatcher.py"]},
            {"en": "Evaluator", "cn": "评估器", "desc": "三层门控验证：输出完整性+结构正确性+语义合理性。", "mods": ["MAOP_verify.py", "core/guardrail.py"]},
        ],
    },
    {
        "title": "调度记忆 (4角色)", "en": "Dispatch", "cn": "调度记忆", "icon": "DM", "count": 4,
        "color": "#2dd4bf", "bg": "rgba(45,212,191,.15)",
        "roles": [
            {"en": "Dispatcher", "cn": "调度器", "desc": "将任务分发给Agent CLI，管理降级链和重试策略。", "mods": ["delegate/dispatcher.py"]},
            {"en": "Memory", "cn": "记忆器", "desc": "FTS5全文检索+向量相似度搜索+深度记忆追踪。", "mods": ["memory/store.py", "core/vector.py"]},
            {"en": "Knowledge", "cn": "知识器", "desc": "关联记忆图谱、经验蒸馏和上下文注入。", "mods": ["memory/store.py", "core/analyzer.py"]},
            {"en": "Consolidator", "cn": "记忆合并器", "desc": "Dream Memory Consolidation四阶段：提取→合并→精炼→写入。", "mods": ["memory/consolidator.py"]},
        ],
    },
    {
        "title": "安全治理 (4角色)", "en": "Security", "cn": "安全治理", "icon": "SG", "count": 4,
        "color": "#ef4444", "bg": "rgba(239,68,68,.15)",
        "roles": [
            {"en": "Guardrail", "cn": "护栏器", "desc": "输入/输出安全校验：防止prompt注入、检测越权访问。", "mods": ["core/guardrail.py"]},
            {"en": "Sandbox", "cn": "沙箱器", "desc": "为Agent执行提供隔离环境：限制文件系统访问范围。", "mods": ["core/sandbox.py", "core/runtime.py"]},
            {"en": "HumanProxy", "cn": "人工代理", "desc": "敏感操作需人工确认时的交互通道。", "mods": ["core/human_proxy.py"]},
            {"en": "Auth", "cn": "认证器", "desc": "API Key验证、JWT令牌解析、RBAC权限检查。", "mods": ["core/auth.py", "core/middleware.py"]},
        ],
    },
    {
        "title": "数据通信 (3角色)", "en": "Data", "cn": "数据通信", "icon": "DC", "count": 3,
        "color": "#818cf8", "bg": "rgba(129,140,248,.15)",
        "roles": [
            {"en": "ToolManager", "cn": "工具管理器", "desc": "Skills和MCP工具的注册、发现和调用。", "mods": ["core/tool_manager.py"]},
            {"en": "Monitor", "cn": "监控器", "desc": "Counter/Gauge/Histogram指标采集，支持Prometheus导出。", "mods": ["core/monitoring.py", "core/timeseries.py"]},
            {"en": "EventBus", "cn": "事件总线", "desc": "模块间异步通信：发布订阅模式+事件溯源。", "mods": ["core/event_bus.py", "core/message_queue.py"]},
        ],
    },
    {
        "title": "模型管理 (2角色)", "en": "Model", "cn": "模型管理", "icon": "MM", "count": 2,
        "color": "#0ea5e9", "bg": "rgba(14,165,233,.15)",
        "roles": [
            {"en": "ModelManager", "cn": "模型管理器", "desc": "统一管理所有AI模型，按任务需求+配额+预算选择最优模型。", "mods": ["model/registry.py", "model/selector.py"]},
            {"en": "BudgetGuard", "cn": "预算守卫", "desc": "Token消耗与费用预算管理，超预算时拒绝调用或自动降级。", "mods": ["model/budget.py", "model/quota.py"]},
        ],
    },
    {
        "title": "平台控制 (2角色)", "en": "Platform", "cn": "平台控制", "icon": "PC", "count": 2,
        "color": "#f43f5e", "bg": "rgba(244,63,94,.15)",
        "roles": [
            {"en": "ControlPlane", "cn": "控制平面", "desc": "统一平台控制面API，收敛所有控制入口。", "mods": ["control/plane.py"]},
            {"en": "Auditor", "cn": "审计器", "desc": "记录所有控制操作的审计日志。", "mods": ["control/audit.py"]},
        ],
    },
    {
        "title": "基础设施 (3角色)", "en": "Infra", "cn": "基础设施", "icon": "IF", "count": 3,
        "color": "#f97316", "bg": "rgba(249,115,22,.15)",
        "roles": [
            {"en": "Evolve", "cn": "进化器", "desc": "分析执行性能数据，生成优化建议。", "mods": ["evolve.py", "core/analyzer.py"]},
            {"en": "LoadBalancer", "cn": "负载均衡器", "desc": "多Agent实例间的加权路由和健康检查。", "mods": ["core/load_balancer.py", "core/circuit_breaker.py"]},
            {"en": "WorkerPool", "cn": "工作池", "desc": "管理Worker实例池，支持并行执行和资源隔离。", "mods": ["core/worker_pool.py", "concurrency.py"]},
        ],
    },
]

MODULES = [
    {
        "name": "顶层包 MAOP/", "en": "MAOP/", "cn": "顶层包", "count": 10, "icon": "P",
        "color": "#84cc16", "bg": "rgba(132,204,22,.15)",
        "mods": [
            {"name": "cli.py", "cn": "命令行入口", "desc": "CLI入口，支持run/validate/doctor/deploy子命令", "exports": "main()"},
            {"name": "MAOP_loop.py", "cn": "主编排循环", "desc": "Plan-Execute-Verify主循环驱动器", "exports": "MaopLoop, LoopResult"},
            {"name": "MAOP_plan.py", "cn": "规划引擎", "desc": "路由匹配和执行策略生成", "exports": "PlanEngine"},
            {"name": "MAOP_execute.py", "cn": "执行引擎", "desc": "按计划调度Agent，含熔断器和降级链", "exports": "ExecuteEngine"},
            {"name": "MAOP_verify.py", "cn": "验证引擎", "desc": "三层门控验证", "exports": "VerifyEngine"},
            {"name": "engine.py", "cn": "DAG拓扑引擎", "desc": "有向无环图拓扑排序", "exports": "DAGEngine"},
            {"name": "evolve.py", "cn": "自进化引擎", "desc": "分析执行性能，生成优化建议", "exports": "EvolveEngine"},
            {"name": "concurrency.py", "cn": "并发控制器", "desc": "asyncio TaskPool并行执行", "exports": "TaskPool, SSEStream"},
            {"name": "deploy.py", "cn": "部署入口", "desc": "Docker容器化部署", "exports": "deploy()"},
            {"name": "prompt_manager.py", "cn": "提示词管理器", "desc": "模板渲染、变量注入和版本管理", "exports": "PromptManager"},
        ],
    },
    {
        "name": "config/ 配置子包", "en": "config/", "cn": "配置子包", "count": 3, "icon": "C",
        "color": "#06b6d4", "bg": "rgba(6,182,212,.15)",
        "mods": [
            {"name": "loader.py", "cn": "配置加载器", "desc": "YAML解析+Pydantic校验", "exports": "ConfigLoader, MaopConfig"},
            {"name": "settings.py", "cn": "设置模型", "desc": "Pydantic BaseModel定义", "exports": "Settings"},
            {"name": "hot_reload.py", "cn": "热重载器", "desc": "文件监听+配置自动刷新", "exports": "HotReloader"},
        ],
    },
    {
        "name": "control/ 控制面", "en": "control/", "cn": "控制面", "count": 2, "icon": "A",
        "color": "#f43f5e", "bg": "rgba(244,63,94,.15)",
        "mods": [
            {"name": "audit.py", "cn": "审计日志", "desc": "控制操作审计事件记录", "exports": "AuditEvent, AuditLog"},
            {"name": "plane.py", "cn": "控制平面", "desc": "统一控制面API", "exports": "ControlPlane, ActionResult"},
        ],
    },
    {
        "name": "core/ 基础设施", "en": "core/", "cn": "基础设施", "count": 31, "icon": "K",
        "color": "#f97316", "bg": "rgba(249,115,22,.15)",
        "mods": [
            {"name": "analyzer.py", "cn": "需求分析引擎", "desc": "语义拆解+依赖DAG构建+复杂度评估", "exports": "RequirementAnalyzer"},
            {"name": "auth.py", "cn": "认证管理器", "desc": "API Key验证+JWT解析+RBAC权限检查", "exports": "AuthManager"},
            {"name": "bloom_filter.py", "cn": "布隆过滤器", "desc": "概率型去重数据结构", "exports": "BloomFilter"},
            {"name": "cache.py", "cn": "LRU缓存", "desc": "最近最少使用+TTL过期双重淘汰", "exports": "LRUCache"},
            {"name": "cache_guard.py", "cn": "缓存防护", "desc": "防穿透+防击穿+防雪崩", "exports": "CacheGuard, SingleFlight"},
            {"name": "circuit_breaker.py", "cn": "熔断器", "desc": "三态+SQLite持久化+降级链", "exports": "CircuitBreaker"},
            {"name": "context_compressor.py", "cn": "上下文压缩", "desc": "结构化上下文压缩", "exports": "ContextCompressor"},
            {"name": "data.py", "cn": "数据层", "desc": "SQLite+FTS5全文+JSON1扩展", "exports": "DataLayer"},
            {"name": "db_backup.py", "cn": "数据库备份", "desc": "增量备份+全量备份+自动恢复", "exports": "DBBackup"},
            {"name": "dynamic_router.py", "cn": "动态路由器", "desc": "按健康数据动态评分Agent", "exports": "DynamicRouter"},
            {"name": "error_schema.py", "cn": "错误模式", "desc": "错误分类+结果封装", "exports": "MaopResult, ErrorSchema"},
            {"name": "event_bus.py", "cn": "事件总线", "desc": "发布订阅+事件溯源", "exports": "EventBus"},
            {"name": "filelock.py", "cn": "文件锁", "desc": "跨进程互斥锁", "exports": "FileLock"},
            {"name": "guardrail.py", "cn": "安全护栏", "desc": "输入/输出安全校验", "exports": "Guardrail"},
            {"name": "human_proxy.py", "cn": "人工代理", "desc": "敏感操作审批队列", "exports": "HumanProxy"},
            {"name": "kv_store.py", "cn": "轻量KV存储", "desc": "SQLite-backed键值存储", "exports": "KVStore"},
            {"name": "load_balancer.py", "cn": "负载均衡器", "desc": "加权路由+健康检查", "exports": "LoadBalancer"},
            {"name": "log_rotate.py", "cn": "日志轮转", "desc": "按大小/时间自动切割", "exports": "LogRotator"},
            {"name": "message_queue.py", "cn": "消息队列", "desc": "SQLite持久化+消费组", "exports": "MessageQueue"},
            {"name": "middleware.py", "cn": "中间件", "desc": "请求处理链", "exports": "Middleware"},
            {"name": "migration.py", "cn": "数据迁移", "desc": "版本管理+schema升级", "exports": "Migration"},
            {"name": "monitoring.py", "cn": "监控埋点", "desc": "Counter/Gauge/Histogram+Prometheus", "exports": "StructuredLogger, Counter"},
            {"name": "rate_limiter.py", "cn": "速率限制器", "desc": "令牌桶算法", "exports": "RateLimiter"},
            {"name": "runtime.py", "cn": "执行环境抽象", "desc": "Local/Isolated运行时", "exports": "Runtime, LocalRuntime"},
            {"name": "sandbox.py", "cn": "沙箱隔离", "desc": "文件系统限制+网络白名单", "exports": "Sandbox"},
            {"name": "state_classifier.py", "cn": "状态分类器", "desc": "后台任务状态分类", "exports": "StateClassifier"},
            {"name": "timeseries.py", "cn": "时序数据", "desc": "降采样+聚合+滑动窗口", "exports": "TimeSeries"},
            {"name": "tls.py", "cn": "TLS加密", "desc": "证书管理+HTTPS配置", "exports": "TLSConfig"},
            {"name": "tool_manager.py", "cn": "工具管理器", "desc": "Skills/MCP工具注册和调用", "exports": "ToolManager"},
            {"name": "vector.py", "cn": "向量搜索", "desc": "纯Python实现，余弦相似度+TopK", "exports": "VectorStore"},
            {"name": "worker_pool.py", "cn": "Worker池", "desc": "IO-bound+CPU-bound双模式并行", "exports": "WorkerPool"},
        ],
    },
    {
        "name": "dashboard/ 面板", "en": "dashboard/", "cn": "面板", "count": 3, "icon": "D",
        "color": "#ec4899", "bg": "rgba(236,72,153,.15)",
        "mods": [
            {"name": "server.py", "cn": "FastAPI服务", "desc": "异步HTTP服务+WebSocket+SSE", "exports": "app"},
            {"name": "routers/", "cn": "API路由", "desc": "按域拆分的28个路由模块", "exports": "28 routers"},
            {"name": "static/", "cn": "前端资源", "desc": "SPA单页应用+暗色主题", "exports": "HTML/CSS/JS"},
        ],
    },
    {
        "name": "delegate/ 调度", "en": "delegate/", "cn": "调度", "count": 2, "icon": "S",
        "color": "#14b8a6", "bg": "rgba(20,184,166,.15)",
        "mods": [
            {"name": "dispatcher.py", "cn": "任务调度器", "desc": "Agent委派+降级链+重试策略", "exports": "Dispatcher"},
            {"name": "drivers/", "cn": "Agent驱动", "desc": "多厂商CLI适配器", "exports": "BaseDriver, *Driver"},
        ],
    },
    {
        "name": "memory/ 记忆", "en": "memory/", "cn": "记忆", "count": 2, "icon": "M",
        "color": "#a78bfa", "bg": "rgba(167,139,250,.15)",
        "mods": [
            {"name": "store.py", "cn": "记忆存储", "desc": "分层存储+FTS5全文检索", "exports": "MemoryStore"},
            {"name": "consolidator.py", "cn": "记忆合并", "desc": "Dream Consolidation四阶段管道", "exports": "DreamConsolidator"},
        ],
    },
    {
        "name": "model/ 模型管理", "en": "model/", "cn": "模型管理", "count": 6, "icon": "L",
        "color": "#0ea5e9", "bg": "rgba(14,165,233,.15)",
        "mods": [
            {"name": "registry.py", "cn": "模型注册中心", "desc": "从models.yaml加载模型注册", "exports": "ModelRegistry"},
            {"name": "selector.py", "cn": "模型选择器", "desc": "按策略选择最优模型", "exports": "ModelSelector"},
            {"name": "fallback.py", "cn": "降级策略", "desc": "多级降级链管理", "exports": "FallbackChain"},
            {"name": "budget.py", "cn": "预算管理", "desc": "Token消耗与费用追踪", "exports": "BudgetGuard"},
            {"name": "quota.py", "cn": "配额管理", "desc": "按Agent/模型/时间窗口配额", "exports": "QuotaManager"},
            {"name": "provider.py", "cn": "Provider抽象", "desc": "LLM Provider统一接口", "exports": "LLMProvider"},
        ],
    },
]

WORKFLOWS = [
    {"en": "Router", "cn": "路由器", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)", "mod": "MAOP_plan.py, core/dynamic_router.py",
     "steps": [{"t": "接收任务", "d": "从CLI/API/工作流接收任务描述"}, {"t": "特征提取", "d": "解析任务关键词、类型和优先级"}, {"t": "Agent匹配", "d": "按正则/通配符/语义匹配最优Agent"}, {"t": "路由决策", "d": "考虑健康度、负载和降级链，确定目标Agent"}]},
    {"en": "Planner", "cn": "规划器", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)", "mod": "MAOP_plan.py, engine.py",
     "steps": [{"t": "需求分析", "d": "语义拆解+依赖DAG构建"}, {"t": "策略生成", "d": "决定并行/串行/重试/降级策略"}, {"t": "计划输出", "d": "生成可执行的DAG执行计划"}, {"t": "资源评估", "d": "预估Token消耗和执行时间"}]},
    {"en": "Orchestrator", "cn": "编排器", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)", "mod": "MAOP_loop.py, concurrency.py",
     "steps": [{"t": "初始化循环", "d": "加载配置、初始化状态机"}, {"t": "阶段驱动", "d": "Plan→Execute→Verify三阶段流转"}, {"t": "反馈控制", "d": "验证失败时触发Feedback Loop(≤2轮)"}, {"t": "结果聚合", "d": "收集各阶段输出，生成最终结果"}]},
    {"en": "Worker", "cn": "执行器", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)", "mod": "MAOP_execute.py, delegate/dispatcher.py",
     "steps": [{"t": "任务接收", "d": "从编排器接收执行指令"}, {"t": "CLI调用", "d": "调用Agent CLI执行具体任务"}, {"t": "输出收集", "d": "捕获stdout/stderr和退出码"}, {"t": "统计上报", "d": "记录延迟、成功率和资源使用"}]},
    {"en": "Evaluator", "cn": "评估器", "color": "#3b82f6", "bg": "rgba(59,130,246,.15)", "mod": "MAOP_verify.py, core/guardrail.py",
     "steps": [{"t": "完整性检查", "d": "验证输出非空且格式正确"}, {"t": "结构校验", "d": "按schema验证输出结构"}, {"t": "语义评估", "d": "置信度评分和合理性判断"}, {"t": "门控决策", "d": "通过/失败/需修正三态决策"}]},
    {"en": "Dispatcher", "cn": "调度器", "color": "#2dd4bf", "bg": "rgba(45,212,191,.15)", "mod": "delegate/dispatcher.py",
     "steps": [{"t": "任务排队", "d": "按优先级入队"}, {"t": "Agent选择", "d": "按降级链选择可用Agent"}, {"t": "委派执行", "d": "分发任务到Agent CLI"}, {"t": "结果回收", "d": "收集执行结果和状态"}]},
    {"en": "Memory", "cn": "记忆器", "color": "#2dd4bf", "bg": "rgba(45,212,191,.15)", "mod": "memory/store.py, core/vector.py",
     "steps": [{"t": "写入记忆", "d": "存储执行经验和上下文"}, {"t": "全文检索", "d": "FTS5关键词搜索"}, {"t": "向量搜索", "d": "语义相似度TopK检索"}, {"t": "记忆注入", "d": "将相关记忆注入Prompt"}]},
    {"en": "Knowledge", "cn": "知识器", "color": "#2dd4bf", "bg": "rgba(45,212,191,.15)", "mod": "memory/store.py, core/analyzer.py",
     "steps": [{"t": "经验蒸馏", "d": "从执行历史提取可复用规则"}, {"t": "图谱构建", "d": "构建实体-关系知识图谱"}, {"t": "上下文注入", "d": "将相关知识注入当前任务"}, {"t": "知识更新", "d": "新经验合并到知识库"}]},
    {"en": "Consolidator", "cn": "记忆合并器", "color": "#2dd4bf", "bg": "rgba(45,212,191,.15)", "mod": "memory/consolidator.py",
     "steps": [{"t": "提取", "d": "从短期记忆提取高价值片段"}, {"t": "合并", "d": "去重和语义合并"}, {"t": "精炼", "d": "压缩和结构化"}, {"t": "写入", "d": "持久化到长期记忆"}]},
    {"en": "Guardrail", "cn": "护栏器", "color": "#ef4444", "bg": "rgba(239,68,68,.15)", "mod": "core/guardrail.py",
     "steps": [{"t": "输入校验", "d": "检测prompt注入和越权访问"}, {"t": "输出过滤", "d": "过滤敏感信息和不当内容"}, {"t": "策略执行", "d": "按安全策略拦截或放行"}, {"t": "审计记录", "d": "记录安全事件"}]},
    {"en": "Sandbox", "cn": "沙箱器", "color": "#ef4444", "bg": "rgba(239,68,68,.15)", "mod": "core/sandbox.py, core/runtime.py",
     "steps": [{"t": "环境初始化", "d": "创建隔离执行环境"}, {"t": "权限限制", "d": "设置文件系统和网络白名单"}, {"t": "资源配额", "d": "限制CPU/内存/时间使用"}, {"t": "执行监控", "d": "实时监控和异常中断"}]},
    {"en": "HumanProxy", "cn": "人工代理", "color": "#ef4444", "bg": "rgba(239,68,68,.15)", "mod": "core/human_proxy.py",
     "steps": [{"t": "审批请求", "d": "将敏感操作加入审批队列"}, {"t": "通知人工", "d": "推送审批通知"}, {"t": "等待决策", "d": "超时自动拒绝"}, {"t": "执行结果", "d": "按审批结果执行或取消"}]},
    {"en": "Auth", "cn": "认证器", "color": "#ef4444", "bg": "rgba(239,68,68,.15)", "mod": "core/auth.py, core/middleware.py",
     "steps": [{"t": "令牌验证", "d": "解析和验证JWT/API Key"}, {"t": "权限检查", "d": "RBAC角色权限匹配"}, {"t": "会话管理", "d": "创建和维护用户会话"}, {"t": "审计日志", "d": "记录认证事件"}]},
    {"en": "ToolManager", "cn": "工具管理器", "color": "#818cf8", "bg": "rgba(129,140,248,.15)", "mod": "core/tool_manager.py",
     "steps": [{"t": "工具注册", "d": "注册Skills和MCP工具"}, {"t": "工具发现", "d": "按能力匹配可用工具"}, {"t": "工具调用", "d": "执行工具并收集结果"}, {"t": "调用日志", "d": "记录工具使用统计"}]},
    {"en": "Monitor", "cn": "监控器", "color": "#818cf8", "bg": "rgba(129,140,248,.15)", "mod": "core/monitoring.py, core/timeseries.py",
     "steps": [{"t": "指标采集", "d": "Counter/Gauge/Histogram埋点"}, {"t": "时序存储", "d": "降采样+聚合+滑动窗口"}, {"t": "Prometheus导出", "d": "标准metrics端点"}, {"t": "告警触发", "d": "阈值检测和通知"}]},
    {"en": "EventBus", "cn": "事件总线", "color": "#818cf8", "bg": "rgba(129,140,248,.15)", "mod": "core/event_bus.py, core/message_queue.py",
     "steps": [{"t": "事件发布", "d": "模块发布领域事件"}, {"t": "事件路由", "d": "按topic分发到订阅者"}, {"t": "事件溯源", "d": "持久化事件历史"}, {"t": "异步处理", "d": "消费组并行处理"}]},
    {"en": "ModelManager", "cn": "模型管理器", "color": "#0ea5e9", "bg": "rgba(14,165,233,.15)", "mod": "model/registry.py, model/selector.py",
     "steps": [{"t": "模型注册", "d": "从models.yaml加载模型配置"}, {"t": "模型选择", "d": "按任务+配额+预算选最优模型"}, {"t": "降级管理", "d": "管理fallback降级链"}, {"t": "Provider适配", "d": "统一LLM调用接口"}]},
    {"en": "BudgetGuard", "cn": "预算守卫", "color": "#0ea5e9", "bg": "rgba(14,165,233,.15)", "mod": "model/budget.py, model/quota.py",
     "steps": [{"t": "消耗追踪", "d": "按Agent/模型/时间窗口追踪Token"}, {"t": "预算检查", "d": "调用前检查预算余量"}, {"t": "超限处理", "d": "拒绝调用或自动降级"}, {"t": "报告生成", "d": "成本分析和趋势报告"}]},
    {"en": "ControlPlane", "cn": "控制平面", "color": "#f43f5e", "bg": "rgba(244,63,94,.15)", "mod": "control/plane.py",
     "steps": [{"t": "请求接收", "d": "统一控制入口API"}, {"t": "权限验证", "d": "RBAC权限检查"}, {"t": "动作执行", "d": "执行控制操作"}, {"t": "审计记录", "d": "记录操作审计事件"}]},
    {"en": "Auditor", "cn": "审计器", "color": "#f43f5e", "bg": "rgba(244,63,94,.15)", "mod": "control/audit.py",
     "steps": [{"t": "事件捕获", "d": "拦截所有控制操作"}, {"t": "事件记录", "d": "写入审计日志"}, {"t": "合规检查", "d": "验证操作合规性"}, {"t": "审计查询", "d": "支持按条件回溯查询"}]},
    {"en": "Evolve", "cn": "进化器", "color": "#f97316", "bg": "rgba(249,115,22,.15)", "mod": "evolve.py, core/analyzer.py",
     "steps": [{"t": "数据采集", "d": "从Monitor采集Agent执行统计"}, {"t": "瓶颈识别", "d": "分析性能瓶颈和低效路径"}, {"t": "建议生成", "d": "生成优化建议(权重/重试/降级)"}, {"t": "建议应用", "d": "更新配置并写入Memory"}]},
    {"en": "LoadBalancer", "cn": "负载均衡器", "color": "#f97316", "bg": "rgba(249,115,22,.15)", "mod": "core/load_balancer.py, core/circuit_breaker.py",
     "steps": [{"t": "健康检查", "d": "定期检查Agent实例健康度"}, {"t": "权重计算", "d": "按成功率/延迟计算动态权重"}, {"t": "路由分配", "d": "轮询/最少连接/加权随机"}, {"t": "故障剔除", "d": "自动剔除不健康实例"}]},
    {"en": "WorkerPool", "cn": "工作池", "color": "#f97316", "bg": "rgba(249,115,22,.15)", "mod": "core/worker_pool.py, concurrency.py",
     "steps": [{"t": "池初始化", "d": "按CPU核心数创建Worker"}, {"t": "任务分配", "d": "IO-bound用asyncio，CPU-bound用ProcessPool"}, {"t": "并行执行", "d": "多Worker并行处理任务"}, {"t": "结果收集", "d": "聚合各Worker输出"}]},
]

ARCHITECTURE = {
    "layers": [
        {"num": "L1", "en": "CLI Entry", "cn": "CLI入口层", "count": "2模块", "color": "green",
         "desc": "用户与MAOP交互的第一触点", "mods": ["cli.py", "maop.ps1"]},
        {"num": "L2", "en": "MaopLoop Orchestration", "cn": "MaopLoop编排层", "count": "2模块", "color": "blue",
         "desc": "Plan-Execute-Verify主循环驱动", "mods": ["MAOP_loop.py", "concurrency.py"]},
        {"num": "L3", "en": "Engine", "cn": "引擎层", "count": "6模块", "color": "purple",
         "desc": "规划/执行/验证/DAG/进化/提示词", "mods": ["MAOP_plan.py", "MAOP_execute.py", "MAOP_verify.py", "engine.py", "evolve.py", "prompt_manager.py"]},
        {"num": "L4", "en": "Service", "cn": "服务层", "count": "12模块", "color": "orange",
         "desc": "调度/记忆/模型/控制/部署", "mods": ["dispatcher", "store", "consolidator", "registry", "selector", "fallback", "budget", "quota", "provider", "audit", "plane", "deploy"]},
        {"num": "L5", "en": "Infrastructure", "cn": "基础设施层", "count": "31模块", "color": "red",
         "desc": "64个core模块提供全部基础设施能力", "mods": ["auth", "cache", "circuit_breaker", "data", "event_bus", "guardrail", "load_balancer", "monitoring", "rate_limiter", "sandbox", "vector", "worker_pool", "..."]},
        {"num": "L6", "en": "Support", "cn": "支撑层", "count": "13模块", "color": "cyan",
         "desc": "配置/日志/迁移/备份/TLS/中间件", "mods": ["loader", "settings", "hot_reload", "log_rotate", "migration", "db_backup", "tls", "middleware", "filelock", "kv_store", "timeseries", "state_classifier", "error_schema"]},
    ],
    "dataFlows": [
        "CLI → MaopLoop → Plan → Dispatcher → Agent CLI → Result → Verify → Output",
        "Memory Store ← FTS5/Vector Search ← Knowledge Graph ← Consolidator",
        "Monitor → Timeseries → Evolve → Config Update → Hot Reload",
    ],
    "decisions": [
        ["数据存储", "SQLite + FTS5全文 + JSON1扩展，零外部依赖"],
        ["缓存策略", "LRU + TTL双重淘汰 + CacheGuard三防"],
        ["并发模型", "asyncio事件循环 + ProcessPoolExecutor + TaskPool"],
        ["部署方式", "Docker多阶段构建 + 环境变量注入"],
    ],
}


@router.get("/pillars")
@handle_api_errors
async def get_pillars() -> dict[str, Any]:
    return {"pillars": PILLARS}


@router.get("/roles")
@handle_api_errors
async def get_roles() -> dict[str, Any]:
    return {"groups": ROLES}


@router.get("/modules")
@handle_api_errors
async def get_modules() -> dict[str, Any]:
    return {"packages": MODULES}


@router.get("/workflows")
@handle_api_errors
async def get_workflows() -> dict[str, Any]:
    return {"roleFlows": WORKFLOWS}


@router.get("/architecture")
@handle_api_errors
async def get_architecture() -> dict[str, Any]:
    return ARCHITECTURE


@router.get("/edition")
@handle_api_errors
async def get_edition() -> dict[str, Any]:
    """Return current edition info, feature flags, backends, and degradations."""
    from maop.config.edition import edition_info
    return edition_info()


@router.get("/config")
@handle_api_errors
async def get_config() -> dict[str, Any]:
    """Return current runtime configuration (non-sensitive)."""
    try:
        from maop.config.settings import MAOPSettings
        s = MAOPSettings()
        return {
            "project_name": s.project_name,
            "edition": s.edition,
            "debug": s.debug,
            "log_level": s.log_level,
            "dash_host": s.dash_host,
            "dash_port": s.dash_port,
            "dash_workers": s.dash_workers,
            "auth_enabled": s.auth_enabled,
            "tls_enabled": s.tls_enabled,
            "root_dir": str(s.root_dir),
            "data_dir": str(s.data_dir),
            "db_path": str(s.db_path),
            "memory_db_path": str(s.memory_db_path),
            "rate_limit_enabled": s.rate_limit_enabled,
            "rate_limit_rps": s.rate_limit_rps,
            "rate_limit_burst": s.rate_limit_burst,
        }
    except Exception as exc:
        return {"error": str(exc)}