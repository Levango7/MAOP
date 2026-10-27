# _register_routes: API router registration.
# Split from _register_routes.py into _register_routes/_routers.py.

from __future__ import annotations

import logging

from fastapi import FastAPI

from maop.config.edition import get_edition
from maop.dashboard.routers import auth as _auth_mod
from maop.dashboard.routers.routing_preview import router as routing_preview_router

logger = logging.getLogger(__name__)

__all__: list[str] = []


def _register_enterprise_routers(app: FastAPI) -> None:
    """Register enterprise-only routers (tenant, rbac, sso, quotas, n8n, licenses).

    Each router is gated by a feature flag and wrapped in try/except ImportError
    so that personal edition (which lacks the router module) degrades gracefully
    with a warning instead of crashing on startup.
    """
    # 延迟导入：has_feature 被 test_gdpr_compliance 通过 patch.object(RR, "has_feature")
    # 打桩，必须从包的 __dict__ 在调用时查找才能拿到桩版本。
    from maop.dashboard._register_routes import FeatureFlag, has_feature

    if has_feature(FeatureFlag.MULTI_USER):
        try:
            # `# type: ignore` is required because mypy cannot
            # statically verify that routers/tenant.py exists — it is created
            # in Phase C. The runtime `except ImportError` below emits a
            # warning if the file is missing, so the silent-swallow problem
            # from B1 is fixed at runtime; the type-ignore only silences the
            # static checker.
            from maop.dashboard.routers import tenant as tenant_router
            app.include_router(tenant_router.router)
            logger.info("[server] Enterprise router: tenant enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: tenant (import error: %s). "
                "ENTERPRISE mode will return 404 on /api/tenant/* — Phase C will "
                "add routers/tenant.py to fix this.",
                _e,
            )
        try:
            # C1 (2026-07-22): RBAC router — bridges RBACManager to frontend.
            from maop.dashboard.routers import rbac as rbac_router
            app.include_router(rbac_router.router)
            logger.info("[server] Enterprise router: rbac enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: rbac (import error: %s). "
                "ENTERPRISE mode will return 404 on /api/rbac/* — Phase C will "
                "add routers/rbac.py to fix this.",
                _e,
            )
        try:
            # 合规路由（GDPR/CCPA 删除与导出 + 2026-09-21 新增的 GDPR 法定流程）。
            # 修复：routers/compliance.py 一直定义了 router 却从未 include ——
            # 因此 /api/compliance/* 全部 404（请求会落到 SPA fallback），
            # 既有的 delete-user-data / export-user-data 也一直不可达。
            # 与本项目曾踩过的 kg_router 同类：定义但未挂载。
            from maop.dashboard.routers import compliance as compliance_router

            app.include_router(compliance_router.router)
            logger.info("[server] Enterprise router: compliance enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: compliance "
                "(import error: %s). /api/compliance/* will 404.",
                _e,
            )
        try:
            # C4 (2026-07-22): SSO router — bridges SSOManager to frontend.
            from maop.dashboard.routers import sso as sso_router
            app.include_router(sso_router.router)
            logger.info("[server] Enterprise router: sso enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: sso (import error: %s). "
                "ENTERPRISE mode will return 404 on /api/sso/* — Phase C will "
                "add routers/sso.py to fix this.",
                _e,
            )
        try:
            # Multi-tenant resource quotas router — bridges QuotaManager to
            # frontend. Implements PRD docs/prd-tenant-quota.md.
            from maop.dashboard.routers import quotas as quotas_router
            app.include_router(quotas_router.router)
            logger.info("[server] Enterprise router: quotas enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: quotas (import error: %s). "
                "ENTERPRISE mode will return 404 on /api/quotas/*.",
                _e,
            )

    # ── n8n integration router (Enterprise only, gated by FeatureFlag.N8N_INTEGRATION) ──
    if has_feature(FeatureFlag.N8N_INTEGRATION):
        try:
            from maop.dashboard.routers import n8n as n8n_router
            app.include_router(n8n_router.router)
            logger.info("[server] Enterprise router: n8n enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: n8n (import error: %s).",
                _e,
            )

    # ── License management router (Enterprise only, gated by FeatureFlag.LICENSE_MANAGEMENT) ──
    # Implements PRD docs/prd-license-management.md: CRUD + lifecycle (validate,
    # revoke, renew) + audit log for issued MAOP Enterprise licenses.
    if has_feature(FeatureFlag.LICENSE_MANAGEMENT):
        try:
            from maop.dashboard.routers import licenses as licenses_router
            app.include_router(licenses_router.router)
            logger.info("[server] Enterprise router: licenses enabled")
        except ImportError as _e:
            logger.warning(
                "[server] Enterprise router MISSING: licenses (import error: %s).",
                _e,
            )


def _register_core_routers(app: FastAPI) -> None:
    """Register core dashboard routers (data / control / model / evolution / memory / system / auth)."""
    from maop.dashboard.routers import (
        control,
        data,
        evolution_experiment,
        evolve_insights,
        memory,
        model,
        runs,
        share,
        system,
    )

    app.include_router(data.router)
    app.include_router(control.router)
    app.include_router(runs.router)
    app.include_router(share.router)
    app.include_router(model.router)
    # F2-01 first: evolution_experiment owns the static /api/evolution/ab/list,
    # while evolve_insights owns /api/evolution/ab/{cycle_id}. Starlette picks
    # the first full match, so the param route must not be registered earlier —
    # otherwise the A/B list request is served as a cycle report and the
    # dashboard's experiments list can never be non-empty.
    app.include_router(evolution_experiment.router)
    app.include_router(evolve_insights.router)
    app.include_router(memory.router)
    app.include_router(system.router)
    app.include_router(_auth_mod.router)


def _register_api_keys_router(app: FastAPI) -> None:
    """Register the structured API Key management router (optional)."""
    try:
        from maop.dashboard.routers import api_keys as _api_keys_router
        app.include_router(_api_keys_router.router)
        logger.info("[server] Router: api-keys enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: api_keys (import error: %s)", _e)


def _register_workflow_routers(app: FastAPI) -> None:
    """Register workflow / session / plugin / stream routers."""
    from maop.dashboard.routers import protocol, subagent, worktree

    app.include_router(subagent.router)
    app.include_router(worktree.router)
    app.include_router(protocol.router)

    from maop.dashboard.routers import hooks as hooks_router

    app.include_router(hooks_router.router)

    # 任务199: Hook 可视化配置 CRUD API（/api/hooks 复数路径，前端全量迁移至复数路径）

    from maop.dashboard.routers import stream as stream_router

    app.include_router(stream_router.router)

    from maop.dashboard.routers import permission as perm_router

    app.include_router(perm_router.router)

    from maop.dashboard.routers import mcp as mcp_router

    app.include_router(mcp_router.router)

    from maop.dashboard.routers import session as session_router

    app.include_router(session_router.router)
    # P1-3: 任务历史页 — /api/sessions (列表+分页) + /api/sessions/{id}/rerun
    app.include_router(session_router.tasks_router)

    from maop.dashboard.routers import react as react_router

    app.include_router(react_router.router)

    from maop.dashboard.routers import plugin as plugin_router

    app.include_router(plugin_router.router)

    # 长任务管理路由 — 自主 Agent（Devin/OpenHands 等）长任务提交/查询/取消/SSE 进度流.
    from maop.dashboard.routers import long_running_task as long_running_task_router

    app.include_router(long_running_task_router.router)


def _register_data_routers(app: FastAPI) -> None:
    """Register data / cost / agents / chat / knowledge / observability routers."""
    from maop.dashboard.routers import cost as cost_router

    app.include_router(cost_router.router)

    from maop.dashboard.routers import agents as agents_router

    app.include_router(agents_router.router)

    from maop.dashboard.routers import chat as chat_router

    app.include_router(chat_router.router)

    from maop.dashboard.routers import knowledge as knowledge_router

    app.include_router(knowledge_router.router)
    # 一号用户实测修复（2026-08-31 知识图谱 "Unexpected token '<'" 根因）：
    # kg_router（/api/knowledge-graph，v4.5.0）在 knowledge.py 中定义但从未
    # include —— 请求落到 SPA fallback 返回 HTML，前端 JSON.parse 报
    # "<!DOCTYPE" 错。
    app.include_router(knowledge_router.kg_router)

    from maop.dashboard.routers import info as info_router

    app.include_router(info_router.router)

    from maop.dashboard.routers import budget as budget_router

    app.include_router(budget_router.router)

    from maop.dashboard.routers import tool_audit as tool_audit_router

    app.include_router(tool_audit_router.router)

    from maop.dashboard.routers import agent_proxy as agent_proxy_router

    app.include_router(agent_proxy_router.router)
    app.include_router(routing_preview_router)

    # Phase γ-4: scheduling decision trace API (read-only GET endpoints).
    from maop.dashboard.routers import routing as routing_router

    app.include_router(routing_router.router)

    # F1-04: Observability stack — tracing/metrics/logging status + Prometheus JSON.
    from maop.dashboard.routers import observability as observability_router

    app.include_router(observability_router.router)

    # Alertmanager webhook receiver (POST /api/alerts/webhook). Minimal sink so the
    # Prometheus → Alertmanager → dashboard notification chain is fully wired.
    from maop.dashboard.routers import alerts as alerts_router

    app.include_router(alerts_router.router)

    # t194 (2026-08-14): LLM 智能任务拆分 — 自然语言 → 子任务 DAG。
    from maop.dashboard.routers import dag as dag_router

    app.include_router(dag_router.router)

    # 深度数据分析报表引擎 — Agent 效率 / 任务趋势 / 资源利用率 /
    # 成本分解 / 性能瓶颈 / 综合摘要。复用 CostTracker、TimeSeriesStore、
    # AuditLog、MetricsCollector 与 psutil，无新增持久化。
    from maop.dashboard.routers import analysis as analysis_router

    app.include_router(analysis_router.router)


def _register_scheduling_routers(app: FastAPI) -> None:
    """Register scheduling / supervisor / debate / engine routers (all optional, guarded)."""
    # F1-02 (异常自适应调度): scheduling failure-detector stats endpoint
    # (GET /api/scheduling/failure-stats + POST .../reset). Mounted
    # unconditionally — the detector is process-wide and available in both
    # personal and enterprise editions.
    try:
        from maop.dashboard.routers import scheduling as scheduling_router

        app.include_router(scheduling_router.router)
        logger.info("[server] Router: scheduling enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: scheduling (import error: %s)", _e)

    # M0 L0.1 — Engine distributed execution endpoints:
    # POST /api/engine/run              — single-task local execution
    # POST /api/engine/run-distributed  — DAG distributed execution (Redis Streams)
    try:
        from maop.dashboard.routers import engine_router as engine_api

        app.include_router(engine_api.router)
        logger.info("[server] Router: engine enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: engine (import error: %s)", _e)

    # Supervisor (proactive multi-agent supervision): patrol / alert /
    # replace / degrade / terminate / upgrade. Mounted unconditionally —
    # endpoints return 404 when no Supervisor has been configured
    # (passive-only mode), so the router is always importable.
    try:
        from maop.dashboard.routers import supervisor as supervisor_router

        app.include_router(supervisor_router.router)
        logger.info("[server] Router: supervisor enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: supervisor (import error: %s)", _e)

    # Debate (adversarial multi-agent debate): start / get / verdict /
    # config / history. Mounted unconditionally — endpoints return 404
    # when no DebateDispatcher has been configured, so the router is
    # always importable.
    try:
        from maop.dashboard.routers import debate as debate_router

        app.include_router(debate_router.router)
        logger.info("[server] Router: debate enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: debate (import error: %s)", _e)


def _register_a2a_router(app: FastAPI) -> None:
    """Mount the A2A protocol endpoint (JSON-RPC /a2a) if available.

    F6b (2026-07-22, Phase F): mount the A2A protocol so external agents
    (Google ADK / LangGraph / CrewAI / any A2A-compliant system) can
    discover MAOP agents and dispatch tasks to them. The A2AManager is
    assembled by ServiceContainer with the WorkerPool injected (F6a):
    every ``tasks/send`` is forwarded via ``WorkerPool.submit(agent_name=...)``
    to ``MaopLoop.run(agent=...)`` so the caller-pinned agent actually
    executes the task. See ADR-013.
    """
    from maop.core.agent.delegation.a2a import create_a2a_router as _create_a2a_router
    from maop.core.reliability.services import ServiceContainer as _A2AContainer

    # MAOP_ROOT is needed for the A2A container; import lazily to avoid a
    # circular import at module load time (server.py sets MAOP_ROOT).
    from maop.dashboard.server import MAOP_ROOT

    try:
        _a2a_container = _A2AContainer(root_dir=MAOP_ROOT)
        _a2a_manager = _a2a_container.get("a2a_manager", raise_on_failure=False)
        if _a2a_manager is not None:
            app.include_router(_create_a2a_router(_a2a_manager))
            logger.info("[server] A2A router mounted at /a2a (agent_name routing enabled)")
        else:
            logger.warning("[server] A2AManager unavailable — /a2a endpoint not mounted")
    except Exception as _a2a_exc:
        logger.warning("[server] A2A router mount failed: %s", _a2a_exc)


def _register_audit_router(app: FastAPI) -> None:
    """Register the audit router (unified for both editions).

    audit.py handles enterprise (EnterpriseAuditLogger) and personal
    (control.audit.AuditLog) editions with FeatureFlag gating internally.
    """
    try:
        from maop.dashboard.routers import audit as audit_router
        app.include_router(audit_router.router)
        logger.info("[server] Router: audit enabled (edition=%s)", get_edition().value)
    except ImportError as _e:
        logger.warning("[server] Router MISSING: audit (import error: %s)", _e)


def _register_notifications_and_config(app: FastAPI) -> None:
    """Register notifications / config-history / blackboard routers."""
    # ── Notification Center router (available in both editions) ──
    # Implements PRD docs/prd-notification-center.md: channels (Email/Webhook/InApp),
    # rules, templates, event bus, async delivery with retry + dead-letter queue,
    # and WebSocket real-time push at /api/notifications/ws.
    try:
        from maop.dashboard.routers import notifications as notifications_router
        app.include_router(notifications_router.router)
        # Wire the notification manager's broadcaster to the router's WS pool
        # so InApp notifications are pushed to connected clients in real time.
        try:
            notifications_router.wire_broadcaster()
        except Exception as _notif_wire_exc:
            logger.warning("[server] Notification broadcaster wire failed: %s", _notif_wire_exc)
        logger.info("[server] Router: notifications enabled (edition=%s)", get_edition().value)
    except ImportError as _e:
        logger.warning("[server] Router MISSING: notifications (import error: %s)", _e)

    # ── Config history & rollback router ────────────────────────────────
    # Provides /api/config/history + /api/config/rollback/{version} so operators
    # can inspect the configuration change timeline and restore a known-good
    # snapshot. All endpoints are admin-guarded inside the router.
    try:
        from maop.dashboard.routers import config as config_router
        app.include_router(config_router.router)
        logger.info("[server] Router: config-history enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: config-history (import error: %s)", _e)

    # ── Blackboard architecture router ──────────────────────────────────
    # P2: 经典黑板架构 — 结构化共享知识库 + 知识源 + 控制器。
    # 读操作开放访问；写操作（write/clear）需 admin 鉴权。
    # 黑板默认不接入 EventBus（向后兼容），需显式 enable_event_bus() 启用。
    try:
        from maop.dashboard.routers import blackboard as blackboard_router
        app.include_router(blackboard_router.router)
        logger.info("[server] Router: blackboard enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: blackboard (import error: %s)", _e)

    # ── Blackboard M3 Operations router ────────────────────────────────
    # M3: 高级黑板 API — 辩论/共识/收敛求解/健康监控/Prometheus 指标。
    #   POST /api/bb-m3/debate/run — 发起辩论
    #   GET  /api/bb-m3/consensus/scan — 冲突扫描
    #   POST /api/bb-m3/convergence/start — 启动收敛求解（异步）
    #   GET  /api/bb-m3/convergence/{solve_id} — 查询求解状态
    #   GET  /api/bb-m3/health — 健康快照
    #   GET  /api/bb-m3/probes — 存活/就绪探针
    #   GET  /api/bb-m3/metrics — Prometheus 文本指标
    try:
        from maop.dashboard.routers import bb_m3_ops as bb_m3_ops_router
        app.include_router(bb_m3_ops_router.router)
        logger.info("[server] Router: bb-m3-ops enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: bb-m3-ops (import error: %s)", _e)

    # ── Multi-Tenant Blackboard Admin router ──────────────────────────
    # M4-D: 多租户 Blackboard 管理 API — 租户扫描/详情/清空/RBAC/上下文验证。
    #   GET  /api/tenant-blackboard/tenants — 租户列表
    #   GET  /api/tenant-blackboard/tenants/{tenant_id} — 租户详情
    #   DELETE /api/tenant-blackboard/tenants/{tenant_id} — 清空租户数据
    #   GET  /api/tenant-blackboard/rbac/permissions — RBAC 权限列表
    #   POST /api/tenant-blackboard/context/validate — 验证租户上下文
    try:
        from maop.dashboard.routers import tenant_blackboard as tenant_blackboard_router
        app.include_router(tenant_blackboard_router.router)
        logger.info("[server] Router: tenant-blackboard enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: tenant-blackboard (import error: %s)", _e)


def _register_feedback_router(app: FastAPI) -> None:
    """Register the feedback router — 用户反馈评价系统.

    提交/查询/更新/删除反馈, admin 可导出与查看全部, 普通用户仅管理自己的反馈.
    """
    try:
        from maop.dashboard.routers import feedback as feedback_router
        app.include_router(feedback_router.router)
        logger.info("[server] Router: feedback enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: feedback (import error: %s)", _e)


def _register_agent_versions_router(app: FastAPI) -> None:
    """Register the agent-versions router — Agent 版本管理 / 灰度发布.

    版本 CRUD + 激活 + 回滚 + 灰度配置 + 指标查询.
    写操作需 admin; 读操作需已认证; IDOR 防护非 admin 仅看自己创建的版本.
    """
    try:
        from maop.dashboard.routers import agent_versions as agent_versions_router
        app.include_router(agent_versions_router.router)
        logger.info("[server] Router: agent-versions enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: agent-versions (import error: %s)", _e)


def _register_relay_platform_router(app: FastAPI) -> None:
    """注册第三方中转平台管理路由（/api/relay-platforms/*）。

    提供中转平台 CRUD、模型发现、价格对比、平台推荐等端点。
    使用 try/except 守护，缺失依赖时降级为 warning 而非崩溃。
    """
    try:
        from maop.dashboard.routers import relay_platform as relay_platform_router
        app.include_router(relay_platform_router.router)
        logger.info("[server] Router: relay-platforms enabled")
    except ImportError as _e:
        logger.warning("[server] Router MISSING: relay_platform (import error: %s)", _e)


def _register_desktop_dispatch_routers(app: FastAPI) -> None:
    """Register desktop Agent dispatch layer routers.

    Bridges Agent catalog / router / credential vault / quota bucket /
    billing engine / model gateway to the frontend. All endpoints are
    admin-guarded inside each router.
    """
    # Desktop Agent dispatch layer routes
    from maop.dashboard.routers import agent_catalog as agent_catalog_router
    app.include_router(agent_catalog_router.router)

    from maop.dashboard.routers import agent_router as agent_router_route_router
    app.include_router(agent_router_route_router.router)

    from maop.dashboard.routers import credential_vault as credential_vault_router
    app.include_router(credential_vault_router.router)

    from maop.dashboard.routers import quota as quota_route_router
    app.include_router(quota_route_router.router)

    from maop.dashboard.routers import billing_abstraction as billing_abstraction_router
    app.include_router(billing_abstraction_router.router)

    from maop.dashboard.routers import model_gateway as model_gateway_router
    app.include_router(model_gateway_router.router)

    from maop.dashboard.routers import agent_collaboration as agent_collaboration_router
    app.include_router(agent_collaboration_router.router)

    # Agent 代理网关 — 内网代理 + 审计 + 部门预算（企业场景）。
    from maop.dashboard.routers import agent_proxy_gateway as agent_proxy_gateway_router
    app.include_router(agent_proxy_gateway_router.router)


def register_routers(app: FastAPI) -> None:
    """Register all API routers (core + enterprise + optional)."""
    # 通过包的 __dict__ 设置 _app，使 _register_v1_aliases 能在调用时读到。
    import maop.dashboard._register_routes as _rr

    _rr._app = app

    _register_core_routers(app)
    _register_api_keys_router(app)
    _register_workflow_routers(app)
    _register_data_routers(app)
    _register_scheduling_routers(app)
    _register_a2a_router(app)

    # ── Enterprise-only routers ────────────────────────────────────────
    # t19 (2026-07-21): removed redundant `from maop.config.edition import
    # has_feature, FeatureFlag` — these symbols were already imported at line 52
    # and the duplicate import triggered ruff F811.
    #
    # B1 (2026-07-22): replaced silent `except ImportError: logger.debug(...)`
    # (which used `# type: ignore` to mask missing modules) with
    # explicit `logger.warning` + a `has_<name>_router` flag. The previous
    # behavior silently degraded ENTERPRISE mode to "router enabled but 404
    # on every call" because the missing module was treated as a non-event.
    # Enterprise routers (files already exist in routers/) (routers/{rbac,tenant,audit,sso}.py)
    # and flip these flags to True.
    _register_audit_router(app)
    _register_enterprise_routers(app)
    _register_notifications_and_config(app)
    _register_feedback_router(app)
    _register_agent_versions_router(app)
    _register_desktop_dispatch_routers(app)
    _register_relay_platform_router(app)
