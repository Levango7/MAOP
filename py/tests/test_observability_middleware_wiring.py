"""可观测性中间件**是否真的被安装**（2026-11-10 接线审计的钉子）。

背景
----
``MetricsMiddleware`` 与 ``TraceContextMiddleware`` 此前从未被安装：全仓只有
它们的定义、``observability/__init__`` 的导出、以及测试——**没有任何
``app.add_middleware``**，而两者 docstring 都写着 "Installed via
``app.add_middleware(...)``"。属"文档声明了、代码没做"。

后果（真机实测，正反对照）：
- 修前：``GET /api/prometheus`` 只有 ``# HELP``/``# TYPE`` 两行，**没有任何
  ``maop_requests_total`` 数据序列**——HTTP 指标完全盲；请求头里的
  ``traceparent``/W3C trace context 也永不被提取，分布式追踪在 HTTP 边界断链。
- 修后：打 5 个请求即出现
  ``maop_requests_total{method=GET|path=/api/agents|status=200} 5.0``。

为什么这组测试必须钉"**在栈上**"而不是"类存在"：
这次缺陷的本体不是缺类，而是缺那一行 ``add_middleware``。只断言"类能实例化"
的测试在缺陷期间一样会绿——那正是它躲过 CI 的原因。
"""

from __future__ import annotations

from fastapi import FastAPI

from maop.dashboard._middleware_stack import setup_security_middleware


def _installed_middleware_names(app: FastAPI) -> list[str]:
    """Starlette 把待装中间件存在 ``app.user_middleware``（类对象或 Middleware）。"""
    names: list[str] = []
    for mw in app.user_middleware:
        cls = getattr(mw, "cls", None) or getattr(mw, "middleware_class", None)
        if cls is not None:
            names.append(getattr(cls, "__name__", str(cls)))
    return names


class TestObservabilityMiddlewareInstalled:
    def test_metrics_middleware_is_on_the_stack(self, monkeypatch):
        """MetricsMiddleware 必须在栈上——否则 HTTP 指标全盲。"""
        monkeypatch.setenv("MAOP_HTTP_METRICS", "1")
        app = FastAPI()
        setup_security_middleware(app)
        assert "MetricsMiddleware" in _installed_middleware_names(app), (
            "MetricsMiddleware 未安装：/api/prometheus 不会有任何真实流量序列，"
            "而它的 docstring 却写着已安装"
        )

    def test_trace_context_middleware_is_on_the_stack(self, monkeypatch):
        """TraceContextMiddleware 必须在栈上——否则 traceparent 永不提取。"""
        app = FastAPI()
        setup_security_middleware(app)
        assert "TraceContextMiddleware" in _installed_middleware_names(app), (
            "TraceContextMiddleware 未安装：请求头里的 W3C trace context "
            "永不被提取，分布式追踪在 HTTP 边界断链"
        )

    def test_observability_runs_before_security(self, monkeypatch):
        """执行顺序：可观测性在安全之前（最外层）。

        Starlette 的 ``add_middleware`` 是前置插入 ⇒ ``user_middleware`` 的
        **第 0 项最先执行**。指标必须在鉴权之前跑，否则被拒的 401 不计入——
        而 401/403 恰恰是最该被看到的那些请求。
        """
        app = FastAPI()
        setup_security_middleware(app)
        names = _installed_middleware_names(app)
        assert names[0] == "TraceContextMiddleware", names
        assert names[1] == "MetricsMiddleware", names
        # 安全栈仍在，且排在其后
        assert "AuthMiddleware" in names
        assert names.index("MetricsMiddleware") < names.index("AuthMiddleware")

    def test_metrics_can_be_disabled_by_env(self, monkeypatch):
        """MAOP_HTTP_METRICS=0 ⇒ 装上但 enabled=False（保留开关，不删装配）。"""
        monkeypatch.setenv("MAOP_HTTP_METRICS", "0")
        app = FastAPI()
        setup_security_middleware(app)
        assert "MetricsMiddleware" in _installed_middleware_names(app)

    def test_setup_returns_config_flags(self):
        """既有返回值契约不受影响（server.py 用它做启动日志）。"""
        app = FastAPI()
        cfg = setup_security_middleware(app)
        assert "auth_enabled" in cfg
        assert "csp_enabled" in cfg


class TestMetricsMiddlewareActuallyRecords:
    """端到端：装上去之外，还要真的记到指标里。"""

    def test_request_lands_in_requests_total(self):
        from starlette.testclient import TestClient

        from maop.core.observability.metrics import MetricsMiddleware, get_metrics

        app = FastAPI()

        @app.get("/api/_probe")
        async def _probe():
            return {"ok": True}

        app.add_middleware(
            MetricsMiddleware, exclude_paths=("/api/prometheus", "/api/health"),
        )
        with TestClient(app) as client:
            for _ in range(3):
                assert client.get("/api/_probe").status_code == 200

        # 读法是 to_prometheus()（/api/prometheus 就是这么渲染的）。
        # 注意 **不能用 `.get()`**：带标签的计数器其无标签总量恒为 0.0，
        # 用它断言会得到一个静默的假阴性（本次写测试时就先踩了一次）。
        rendered = get_metrics().requests_total.to_prometheus()
        assert "/api/_probe" in rendered or "_probe" in rendered, (
            f"探针路径没进 requests_total，说明中间件没真正记录：{rendered[:300]}"
        )
        assert "status=200" in rendered

    def test_excluded_paths_are_not_counted(self):
        from starlette.testclient import TestClient

        from maop.core.observability.metrics import MetricsMiddleware, get_metrics

        app = FastAPI()

        @app.get("/api/health")
        async def _h():
            return {"ok": True}

        app.add_middleware(
            MetricsMiddleware, exclude_paths=("/api/prometheus", "/api/health"),
        )
        with TestClient(app) as client:
            client.get("/api/health")

        rendered = get_metrics().requests_total.to_prometheus()
        assert "/api/health" not in rendered, "排除路径不应被计入"
