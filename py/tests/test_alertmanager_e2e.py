"""线1-告警出口验证：alertmanager 配置验证 + 真实告警出口测试.

本模块分两部分：
  Part A — alertmanager 配置静态验证
      * alertmanager.yml YAML 语法 / receivers / route / inhibit_rules
      * ${VAR:default} 占位符格式与 render-config.sh 渲染变量一致性
      * alertmanager/templates/maop.tmpl 模板定义齐全
      * docker-compose.prod.yml 挂载与 entrypoint 一致

  Part B — 真实告警出口（POST /api/alerts/webhook）端到端测试
      * 空 payload / 空 alerts / 单告警 / 多告警
      * firing / resolved 状态
      * critical / warning / info 严重级别
      * 模拟真实 Alertmanager v0.27 webhook payload
      * 200 始终返回（避免 alertmanager 无限重试）
      * 公开端点（无需认证）

Alertmanager payload 规范参考：
  https://prometheus.io/docs/alerting/latest/notifications/
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALERTMANAGER_YML = _PROJECT_ROOT / "alertmanager.yml"
_ALERTMANAGER_DIR = _PROJECT_ROOT / "alertmanager"
_TEMPLATES_DIR = _ALERTMANAGER_DIR / "templates"
_RENDER_SH = _ALERTMANAGER_DIR / "render-config.sh"
_DOCKER_COMPOSE = _PROJECT_ROOT / "docker-compose.yml"
_DOCKER_COMPOSE_PROD = _PROJECT_ROOT / "docker-compose.prod.yml"


# ════════════════════════════════════════════════════════════════════
# Part A — alertmanager 配置静态验证
# ════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def alertmanager_cfg() -> dict:
    """加载并解析 alertmanager.yml，整个模块共享."""
    assert _ALERTMANAGER_YML.exists(), f"alertmanager.yml 不存在: {_ALERTMANAGER_YML}"
    with open(_ALERTMANAGER_YML, encoding="utf-8") as f:
        cfg: dict = yaml.safe_load(f)
    return cfg


# ── A.1 YAML 基础结构 ────────────────────────────────────────────


def test_alertmanager_yml_valid(alertmanager_cfg: dict) -> None:
    """alertmanager.yml 是合法 YAML 且包含顶层必需字段."""
    assert isinstance(alertmanager_cfg, dict)
    for key in ("global", "route", "receivers", "inhibit_rules"):
        assert key in alertmanager_cfg, f"alertmanager.yml 缺少顶层字段: {key}"


def test_alertmanager_yml_global_resolve_timeout(alertmanager_cfg: dict) -> None:
    """global.resolve_timeout 必须存在且为字符串时长."""
    rt = alertmanager_cfg["global"].get("resolve_timeout")
    assert rt is not None
    assert isinstance(rt, str)
    assert rt.endswith(("s", "m", "h")), f"resolve_timeout 非合法时长: {rt}"


def test_alertmanager_yml_templates_path(alertmanager_cfg: dict) -> None:
    """templates 字段指向 /etc/alertmanager/templates/*.tmpl."""
    templates = alertmanager_cfg.get("templates", [])
    assert len(templates) > 0
    assert any("templates/*.tmpl" in t for t in templates), (
        f"templates 路径未指向 templates/*.tmpl: {templates}"
    )


# ── A.2 receivers 完整性 ────────────────────────────────────────


def test_alertmanager_yml_receivers_present(alertmanager_cfg: dict) -> None:
    """四个核心 receiver 必须存在：webhook / email / slack / critical."""
    receivers = {r["name"]: r for r in alertmanager_cfg["receivers"]}
    for name in ("webhook", "email", "slack", "critical"):
        assert name in receivers, f"缺少 receiver: {name}"


def test_alertmanager_yml_webhook_receiver(alertmanager_cfg: dict) -> None:
    """webhook receiver 指向 dashboard /api/alerts/webhook，且 send_resolved=true."""
    webhook = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "webhook")
    configs = webhook.get("webhook_configs", [])
    assert len(configs) == 1
    cfg = configs[0]
    assert "/api/alerts/webhook" in cfg["url"], f"webhook url 未指向 dashboard: {cfg['url']}"
    assert cfg.get("send_resolved") is True
    # max_alerts=0 表示不限制
    assert cfg.get("max_alerts", 0) == 0


def test_alertmanager_yml_email_receiver(alertmanager_cfg: dict) -> None:
    """email receiver 使用 ${VAR:default} 占位符，require_tls=true，send_resolved=true."""
    email = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "email")
    configs = email.get("email_configs", [])
    assert len(configs) == 1
    cfg = configs[0]
    assert "${ALERT_EMAIL_TO:" in cfg["to"], f"email.to 未使用占位符: {cfg['to']}"
    assert "${SMTP_HOST:" in cfg["smarthost"], f"smarthost 未使用占位符: {cfg['smarthost']}"
    assert cfg.get("require_tls") is True
    assert cfg.get("send_resolved") is True
    # 模板引用
    assert "email.html" in cfg.get("html", "")
    assert "email.text" in cfg.get("text", "")


def test_alertmanager_yml_slack_receiver(alertmanager_cfg: dict) -> None:
    """slack receiver 使用 ${SLACK_WEBHOOK_URL:} 占位符（空则 no-op）."""
    slack = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "slack")
    configs = slack.get("slack_configs", [])
    assert len(configs) == 1
    cfg = configs[0]
    assert "${SLACK_WEBHOOK_URL:" in cfg["api_url"], f"slack api_url 未使用占位符: {cfg['api_url']}"
    assert cfg.get("send_resolved") is True


def test_alertmanager_yml_critical_receiver(alertmanager_cfg: dict) -> None:
    """critical receiver 同时 fan-out 到 webhook + email（多通道）."""
    critical = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "critical")
    assert len(critical.get("webhook_configs", [])) >= 1, "critical 缺少 webhook_configs"
    assert len(critical.get("email_configs", [])) >= 1, "critical 缺少 email_configs"
    # webhook url 指向 dashboard
    wh = critical["webhook_configs"][0]
    assert "/api/alerts/webhook" in wh["url"]
    # email subject 包含 CRITICAL 标识
    em = critical["email_configs"][0]
    subject = em.get("headers", {}).get("Subject", "")
    assert "CRITICAL" in subject, f"critical email subject 缺少 CRITICAL 标识: {subject}"


# ── A.3 路由配置 ────────────────────────────────────────────────


def test_alertmanager_yml_route_default(alertmanager_cfg: dict) -> None:
    """默认路由 receiver=webhook，且 group_by/group_wait/group_interval/repeat_interval 齐全."""
    route = alertmanager_cfg["route"]
    assert route["receiver"] == "webhook"
    assert "alertname" in route["group_by"]
    assert "severity" in route["group_by"]
    assert route["group_wait"] == "30s"
    assert route["group_interval"] == "5m"
    assert route["repeat_interval"] == "4h"


def test_alertmanager_yml_route_critical(alertmanager_cfg: dict) -> None:
    """critical 子路由：severity=critical → receiver=critical, continue=false."""
    routes = alertmanager_cfg["route"]["routes"]
    critical_route = next(
        r for r in routes if "severity = \"critical\"" in str(r.get("matchers", []))
    )
    assert critical_route["receiver"] == "critical"
    assert critical_route.get("continue") is False


def test_alertmanager_yml_route_warning(alertmanager_cfg: dict) -> None:
    """warning 子路由：severity=warning → receiver=webhook, continue=false."""
    routes = alertmanager_cfg["route"]["routes"]
    warning_route = next(
        r for r in routes if "severity = \"warning\"" in str(r.get("matchers", []))
    )
    assert warning_route["receiver"] == "webhook"
    assert warning_route.get("continue") is False


def test_alertmanager_yml_route_info(alertmanager_cfg: dict) -> None:
    """info 子路由：severity=info → receiver=webhook, continue=false."""
    routes = alertmanager_cfg["route"]["routes"]
    info_route = next(
        r for r in routes if "severity = \"info\"" in str(r.get("matchers", []))
    )
    assert info_route["receiver"] == "webhook"
    assert info_route.get("continue") is False


# ── A.4 inhibition 规则 ────────────────────────────────────────


def test_alertmanager_yml_inhibit_rules_count(alertmanager_cfg: dict) -> None:
    """至少 3 条 inhibition 规则（critical 抑制 warning / MAOPDown 抑制子组件 / NoActiveAgents 抑制 CB）."""
    rules = alertmanager_cfg["inhibit_rules"]
    assert len(rules) >= 3, f"inhibit_rules 数量不足: {len(rules)}"


def test_alertmanager_yml_inhibit_critical_suppresses_warning(alertmanager_cfg: dict) -> None:
    """规则1：critical 抑制同组件的 warning 告警."""
    rules = alertmanager_cfg["inhibit_rules"]
    rule = next(
        r for r in rules
        if "severity = \"critical\"" in str(r.get("source_matchers", []))
        and "severity = \"warning\"" in str(r.get("target_matchers", []))
    )
    assert "alertname" in rule["equal"]
    assert "agent" in rule["equal"]


def test_alertmanager_yml_inhibit_down_suppresses_children(alertmanager_cfg: dict) -> None:
    """规则2：MAOPDown 抑制该 agent 的所有 MAOP.* 子告警."""
    rules = alertmanager_cfg["inhibit_rules"]
    rule = next(
        r for r in rules
        if "MAOPDown" in str(r.get("source_matchers", []))
    )
    assert "MAOP.*" in str(rule.get("target_matchers", []))
    assert "agent" in rule["equal"]


# ── A.5 ${VAR:default} 占位符 ──────────────────────────────────

# alertmanager.yml 中所有合法的 ${VAR:default} 占位符变量名
_EXPECTED_PLACEHOLDER_VARS = {
    "WEBHOOK_URL",
    "ALERT_EMAIL_TO",
    "ALERT_EMAIL_FROM",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_AUTH_SECRET",
    "SMTP_AUTH_IDENTITY",
    "SLACK_WEBHOOK_URL",
    "SLACK_CHANNEL",
}


def test_alertmanager_yml_placeholder_format() -> None:
    """alertmanager.yml 中所有 ${VAR:default} 占位符格式合法（变量名大写下划线）."""
    content = _ALERTMANAGER_YML.read_text(encoding="utf-8")
    # 匹配 ${VAR} 或 ${VAR:default}
    pattern = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::[^}]*)?\}")
    found_vars = set(pattern.findall(content))
    # 至少包含所有预期变量
    missing = _EXPECTED_PLACEHOLDER_VARS - found_vars
    assert not missing, f"alertmanager.yml 缺少占位符变量: {missing}"


def test_render_config_sh_exists_and_executable() -> None:
    """render-config.sh 存在且具有可执行权限（Unix bit 或 Windows 任意可读）."""
    assert _RENDER_SH.exists(), f"render-config.sh 不存在: {_RENDER_SH}"
    # Windows 上 stat 检查可读即可；CI/Linux 上检查可执行位
    if os.name == "nt":
        # Windows：只要文件可读即可（entrypoint 由 docker 容器内 sh 执行）
        assert _RENDER_SH.stat().st_size > 0
    else:
        mode = _RENDER_SH.stat().st_mode
        assert mode & stat.S_IXUSR, f"render-config.sh 不可执行: {oct(mode)}"


def test_render_config_sh_variables() -> None:
    """render-config.sh 调用 render_var 覆盖所有 alertmanager.yml 占位符变量."""
    sh_content = _RENDER_SH.read_text(encoding="utf-8")
    # 提取所有 render_var VAR 调用
    render_var_calls = re.findall(r"render_var\s+(\w+)", sh_content)
    rendered_vars = set(render_var_calls)
    # alertmanager.yml 中的占位符变量必须全部被 render-config.sh 处理
    # 仅扫描非注释行（避免注释中的 ${VAR:default} 示例被误识别）
    yml_lines = _ALERTMANAGER_YML.read_text(encoding="utf-8").splitlines()
    yml_no_comments = "\n".join(
        line for line in yml_lines if not line.lstrip().startswith("#")
    )
    # 匹配 ${VAR:default} 形式（带默认值），这是 render-config.sh 处理的目标
    yml_vars = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):[^}]*\}", yml_no_comments))
    missing = yml_vars - rendered_vars
    assert not missing, (
        f"render-config.sh 未处理 alertmanager.yml 中的占位符变量: {missing}"
    )


def test_render_config_sh_handoff_to_alertmanager() -> None:
    """render-config.sh 末尾 exec /bin/alertmanager --config.file=$OUTPUT."""
    sh_content = _RENDER_SH.read_text(encoding="utf-8")
    assert "exec /bin/alertmanager" in sh_content
    assert "--config.file=" in sh_content
    assert "--storage.path=/alertmanager" in sh_content


def test_render_config_sh_unresolved_warning() -> None:
    """render-config.sh 检测未解析占位符并输出 WARNING."""
    sh_content = _RENDER_SH.read_text(encoding="utf-8")
    assert "unresolved" in sh_content.lower()
    assert "WARNING" in sh_content


# ── A.6 模板文件 ───────────────────────────────────────────────


def test_alertmanager_templates_dir_exists() -> None:
    """alertmanager/templates/ 目录存在."""
    assert _TEMPLATES_DIR.is_dir(), f"templates 目录不存在: {_TEMPLATES_DIR}"


def test_alertmanager_template_file_exists() -> None:
    """maop.tmpl 模板文件存在."""
    tmpl = _TEMPLATES_DIR / "maop.tmpl"
    assert tmpl.exists(), f"maop.tmpl 不存在: {tmpl}"


def test_alertmanager_template_definitions() -> None:
    """maop.tmpl 定义了 email.html / email.text / slack.text 三个模板."""
    tmpl_content = (_TEMPLATES_DIR / "maop.tmpl").read_text(encoding="utf-8")
    for name in ('"email.html"', '"email.text"', '"slack.text"'):
        assert f"define {name}" in tmpl_content, f"maop.tmpl 缺少模板定义: define {name}"


def test_alertmanager_template_email_html_structure() -> None:
    """email.html 模板包含 <html> 结构和 .header / .alert CSS 类."""
    tmpl_content = (_TEMPLATES_DIR / "maop.tmpl").read_text(encoding="utf-8")
    # 简化检查：模板中应包含 HTML 标签和样式类
    assert "<html>" in tmpl_content
    assert ".header" in tmpl_content
    assert ".alert" in tmpl_content
    # 引用 .Status / .CommonLabels / .Alerts 等 alertmanager 数据
    assert ".Status" in tmpl_content
    assert ".CommonLabels" in tmpl_content
    assert ".Alerts" in tmpl_content


def test_alertmanager_template_slack_text_structure() -> None:
    """slack.text 模板遍历 .Alerts 并输出 alertname + severity."""
    tmpl_content = (_TEMPLATES_DIR / "maop.tmpl").read_text(encoding="utf-8")
    # slack.text 模板段
    slack_start = tmpl_content.find('{{ define "slack.text" }}')
    assert slack_start != -1
    slack_end = tmpl_content.find("{{ end }}", slack_start)
    slack_section = tmpl_content[slack_start:slack_end]
    assert "range .Alerts" in slack_section
    assert ".Labels.alertname" in slack_section
    assert ".Labels.severity" in slack_section


# ── A.7 docker-compose 挂载一致性 ──────────────────────────────


def test_docker_compose_prod_alertmanager_mounts() -> None:
    """docker-compose.prod.yml 中 alertmanager 挂载 alertmanager.yml 为 .tmpl 模板."""
    assert _DOCKER_COMPOSE_PROD.exists()
    with open(_DOCKER_COMPOSE_PROD, encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    am = compose.get("services", {}).get("alertmanager", {})
    assert am, "docker-compose.prod.yml 缺少 alertmanager 服务"
    volumes = am.get("volumes", [])
    # 模板挂载
    assert any("alertmanager.yml.tmpl" in v for v in volumes), (
        f"未挂载 alertmanager.yml 为 .tmpl: {volumes}"
    )
    # render-config.sh 挂载
    assert any("render-config.sh" in v for v in volumes), (
        f"未挂载 render-config.sh: {volumes}"
    )
    # templates 目录挂载
    assert any("templates" in v for v in volumes), f"未挂载 templates 目录: {volumes}"


def test_docker_compose_prod_alertmanager_entrypoint() -> None:
    """docker-compose.prod.yml alertmanager entrypoint 调用 render-config.sh."""
    with open(_DOCKER_COMPOSE_PROD, encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    am = compose["services"]["alertmanager"]
    entrypoint = am.get("entrypoint", [])
    assert any("render-config.sh" in str(e) for e in entrypoint), (
        f"entrypoint 未调用 render-config.sh: {entrypoint}"
    )
    # 传入模板和输出路径
    assert any("alertmanager.yml.tmpl" in str(e) for e in entrypoint)
    assert any("alertmanager.yml" in str(e) and "tmpl" not in str(e) for e in entrypoint)


def test_docker_compose_prod_alertmanager_env_vars() -> None:
    """docker-compose.prod.yml alertmanager 透传所有 receiver 环境变量."""
    with open(_DOCKER_COMPOSE_PROD, encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    am = compose["services"]["alertmanager"]
    env_list = am.get("environment", [])
    env_str = " ".join(env_list)
    for var in _EXPECTED_PLACEHOLDER_VARS:
        assert var in env_str, f"docker-compose.prod.yml 未透传环境变量: {var}"


def test_docker_compose_prod_alertmanager_healthcheck() -> None:
    """docker-compose.prod.yml alertmanager 配置了 healthcheck."""
    with open(_DOCKER_COMPOSE_PROD, encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    am = compose["services"]["alertmanager"]
    hc = am.get("healthcheck")
    assert hc is not None, "alertmanager 缺少 healthcheck"
    test_cmd = str(hc.get("test", ""))
    assert "healthy" in test_cmd or "9093" in test_cmd, (
        f"healthcheck 未检查 /-/healthy: {test_cmd}"
    )


def test_docker_compose_base_alertmanager() -> None:
    """docker-compose.yml 基础 alertmanager 服务存在（profile=monitoring）."""
    with open(_DOCKER_COMPOSE, encoding="utf-8") as f:
        compose = yaml.safe_load(f)
    am = compose.get("services", {}).get("alertmanager", {})
    assert am, "docker-compose.yml 缺少 alertmanager 服务"
    assert "monitoring" in am.get("profiles", []), "alertmanager 未在 monitoring profile"
    # 端口绑定 127.0.0.1:9093
    ports = am.get("ports", [])
    assert any("9093" in str(p) for p in ports), f"未暴露 9093 端口: {ports}"


# ════════════════════════════════════════════════════════════════════
# Part B — 真实告警出口（POST /api/alerts/webhook）端到端测试
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def webhook_client() -> TestClient:
    """构造仅挂载 alerts 路由的 TestClient（无需认证）."""
    from maop.dashboard.routers.alerts import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_alert(
    *,
    alertname: str = "MAOPHighLatency",
    severity: str = "warning",
    status: str = "firing",
    summary: str = "MAOP delegation P95 latency > 2s",
    description: str = "Delegation duration P95 is 3.2s",
    agent: str = "default",
) -> dict:
    """构造单个 alertmanager alert 对象（符合 v0.27 schema 子集）."""
    return {
        "status": status,
        "labels": {
            "alertname": alertname,
            "severity": severity,
            "agent": agent,
        },
        "annotations": {
            "summary": summary,
            "description": description,
        },
        "startsAt": "2026-08-20T06:41:30.000Z",
        "endsAt": "0001-01-01T00:00:00.000Z" if status == "firing" else "2026-08-20T06:46:30.000Z",
        "generatorURL": "http://prometheus:9090/graph?g0.expr=MAOPHighLatency",
    }


def _make_alertmanager_payload(
    *,
    receiver: str = "webhook",
    status: str = "firing",
    alerts: list[dict] | None = None,
) -> dict:
    """构造完整 alertmanager webhook payload（v0.27 schema 子集）."""
    if alerts is None:
        alerts = [_make_alert()]
    common_labels = alerts[0]["labels"] if alerts else {}
    common_annotations = alerts[0]["annotations"] if alerts else {}
    return {
        "receiver": receiver,
        "status": status,
        "alerts": alerts,
        "groupLabels": {"alertname": common_labels.get("alertname", "")},
        "commonLabels": common_labels,
        "commonAnnotations": common_annotations,
        "externalURL": "http://maop-alertmanager:9093",
        "version": "4",
        "groupKey": "{}:{}:{}".format(
            common_labels.get("alertname", ""),
            common_labels.get("severity", ""),
            "2026-08-20T06:41:30.000Z",
        ),
        "truncatedAlerts": 0,
    }


# ── B.1 端点基础 ───────────────────────────────────────────────


def test_webhook_endpoint_registered() -> None:
    """alerts router 注册 POST /api/alerts/webhook 路由."""
    from maop.dashboard.routers.alerts import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/alerts/webhook" in paths


def test_webhook_returns_200_for_empty_payload(webhook_client: TestClient) -> None:
    """空 alerts 列表返回 200 + status=ok + received=0."""
    payload = _make_alertmanager_payload(alerts=[])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["received"] == 0


def test_webhook_returns_200_for_single_alert(webhook_client: TestClient) -> None:
    """单个 firing 告警返回 200 + received=1."""
    payload = _make_alertmanager_payload()
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["received"] == 1


def test_webhook_returns_200_for_multiple_alerts(webhook_client: TestClient) -> None:
    """多个告警返回 200 + received=N."""
    alerts = [
        _make_alert(alertname="MAOPHighLatency", severity="warning"),
        _make_alert(alertname="MAOPNoActiveAgents", severity="critical"),
        _make_alert(alertname="MAOPMemoryStoreGrowing", severity="info"),
    ]
    payload = _make_alertmanager_payload(alerts=alerts)
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 3


# ── B.2 严重级别覆盖 ──────────────────────────────────────────


def test_webhook_critical_severity(webhook_client: TestClient) -> None:
    """critical 级别告警被正确接收（receiver=critical fan-out 场景）."""
    alert = _make_alert(
        alertname="MAOPDown",
        severity="critical",
        summary="MAOP Dashboard is down",
        description="MAOP has been down for more than 1 minute.",
    )
    payload = _make_alertmanager_payload(receiver="critical", alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_warning_severity(webhook_client: TestClient) -> None:
    """warning 级别告警被正确接收."""
    alert = _make_alert(alertname="MAOPHighMemory", severity="warning")
    payload = _make_alertmanager_payload(alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_info_severity(webhook_client: TestClient) -> None:
    """info 级别告警被正确接收."""
    alert = _make_alert(
        alertname="MAOPMemoryStoreGrowing",
        severity="info",
        summary="Memory store exceeds 100K entries and growing",
    )
    payload = _make_alertmanager_payload(alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


# ── B.3 状态覆盖（firing / resolved）──────────────────────────


def test_webhook_firing_status(webhook_client: TestClient) -> None:
    """firing 状态告警被正确接收."""
    alert = _make_alert(status="firing")
    payload = _make_alertmanager_payload(status="firing", alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_resolved_status(webhook_client: TestClient) -> None:
    """resolved 状态告警被正确接收（send_resolved=true 场景）."""
    alert = _make_alert(status="resolved")
    payload = _make_alertmanager_payload(status="resolved", alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_mixed_status_alerts(webhook_client: TestClient) -> None:
    """同一批次混合 firing + resolved 告警."""
    alerts = [
        _make_alert(alertname="MAOPHighLatency", status="firing"),
        _make_alert(alertname="MAOPHighMemory", status="resolved"),
    ]
    payload = _make_alertmanager_payload(alerts=alerts)
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 2


# ── B.4 真实 alertmanager v0.27 payload 兼容性 ───────────────


def test_webhook_real_alertmanager_payload_shape(webhook_client: TestClient) -> None:
    """完整 alertmanager v0.27 payload（含 version/groupKey/truncatedAlerts）被接受."""
    payload = _make_alertmanager_payload()
    # 确保包含 alertmanager v0.27 的所有顶层字段
    assert payload["version"] == "4"
    assert "groupKey" in payload
    assert "truncatedAlerts" in payload
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200


def test_webhook_slo_burn_alert(webhook_client: TestClient) -> None:
    """SLO burn-rate 告警（含 slo/burn_rate 标签）被正确接收."""
    alert = _make_alert(
        alertname="MAOPAvailabilitySLOBurnFast",
        severity="critical",
        summary="SLO-1 Availability: fast burn rate (page)",
        description="Availability error budget burning 14.4x faster than allowed.",
    )
    alert["labels"]["slo"] = "availability"
    alert["labels"]["burn_rate"] = "fast"
    payload = _make_alertmanager_payload(receiver="critical", alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_circuit_breaker_alert(webhook_client: TestClient) -> None:
    """CircuitBreakerOpen 告警被正确接收."""
    alert = _make_alert(
        alertname="MAOPCircuitBreakerOpen",
        severity="critical",
        summary="Circuit breaker is OPEN",
        description="An agent circuit breaker is OPEN and not accepting requests.",
        agent="code-reviewer",
    )
    payload = _make_alertmanager_payload(receiver="critical", alerts=[alert])
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


# ── B.5 边界与异常 ─────────────────────────────────────────────


def test_webhook_minimal_payload(webhook_client: TestClient) -> None:
    """最小 payload（仅 alerts 字段）仍被接受."""
    resp = webhook_client.post("/api/alerts/webhook", json={"alerts": []})
    assert resp.status_code == 200
    assert resp.json()["received"] == 0


def test_webhook_alert_missing_labels(webhook_client: TestClient) -> None:
    """alert 缺少 labels 时仍返回 200（使用 <unknown> 默认值）."""
    payload = {
        "receiver": "webhook",
        "status": "firing",
        "alerts": [{"status": "firing", "labels": {}, "annotations": {}}],
    }
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


def test_webhook_alert_missing_annotations(webhook_client: TestClient) -> None:
    """alert 缺少 annotations 时仍返回 200."""
    payload = {
        "receiver": "webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "MAOPDown", "severity": "critical"},
            "annotations": {},
        }],
    }
    resp = webhook_client.post("/api/alerts/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["received"] == 1


# ── B.6 WebhookPayload 模型 schema ────────────────────────────


def test_webhook_payload_model_fields() -> None:
    """WebhookPayload 模型包含 alertmanager webhook 必需字段."""
    from maop.dashboard.routers.alerts import WebhookPayload

    fields = WebhookPayload.model_fields
    for name in ("receiver", "status", "alerts", "commonLabels", "commonAnnotations"):
        assert name in fields, f"WebhookPayload 缺少字段: {name}"


def test_webhook_alert_model_fields() -> None:
    """_Alert 模型包含 status / labels / annotations 字段."""
    from maop.dashboard.routers.alerts import _Alert

    fields = _Alert.model_fields
    for name in ("status", "labels", "annotations"):
        assert name in fields, f"_Alert 缺少字段: {name}"


# ── B.7 端点公开性（无需认证）─────────────────────────────────


def test_webhook_public_path_in_server() -> None:
    """/api/alerts/webhook 在 server.py 的 public_paths 列表中（无需认证）."""
    server_path = _PROJECT_ROOT / "py" / "maop" / "dashboard" / "server.py"
    assert server_path.exists()
    src = server_path.read_text(encoding="utf-8")
    assert "/api/alerts/webhook" in src, (
        "server.py 未将 /api/alerts/webhook 列入 public_paths"
    )


def test_webhook_router_prefix() -> None:
    """alerts router prefix=/api/alerts，路由 path=/webhook 或完整路径 /api/alerts/webhook."""
    from maop.dashboard.routers.alerts import router

    assert router.prefix == "/api/alerts"
    # FastAPI 不同版本中 route.path 可能是 "/webhook" 或完整路径 "/api/alerts/webhook"
    post_webhook = [
        r for r in router.routes
        if hasattr(r, "path") and r.path in ("/webhook", "/api/alerts/webhook")
        and hasattr(r, "methods") and "POST" in r.methods
    ]
    assert len(post_webhook) >= 1, "alerts router 未注册 POST /webhook"


# ── B.8 端到端：alertmanager.yml → webhook URL 一致性 ─────────


def test_webhook_url_in_alertmanager_yml_matches_endpoint(alertmanager_cfg: dict) -> None:
    """alertmanager.yml 中 webhook url 的默认值指向 dashboard /api/alerts/webhook."""
    webhook = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "webhook")
    url = webhook["webhook_configs"][0]["url"]
    # 提取 ${WEBHOOK_URL:default} 中的 default 部分
    match = re.match(r"\$\{WEBHOOK_URL:(.*)\}$", url)
    assert match, f"webhook url 格式异常: {url}"
    default_url = match.group(1)
    assert "/api/alerts/webhook" in default_url, (
        f"webhook 默认 url 未指向 dashboard endpoint: {default_url}"
    )


def test_critical_receiver_webhook_url_matches_endpoint(alertmanager_cfg: dict) -> None:
    """critical receiver 的 webhook url 默认值同样指向 dashboard endpoint."""
    critical = next(r for r in alertmanager_cfg["receivers"] if r["name"] == "critical")
    url = critical["webhook_configs"][0]["url"]
    match = re.match(r"\$\{WEBHOOK_URL:(.*)\}$", url)
    assert match
    assert "/api/alerts/webhook" in match.group(1)