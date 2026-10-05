"""租户身份收口：唯一实现 + 上下文原语 + 防"再抄一份"的守卫。

## 背景

`request.state.tenant_id` 是请求侧租户身份的唯一来源，但读它的小函数在各路由里
**手抄了 7 份**（`agent_versions_helpers` / `compliance` / `data` / `feedback` /
`memory` / `notifications` / `rbac`）。抄写已经出现分歧：`compliance.py` 与 `rbac.py`
是同名同注释的 "G-07 fix"，但前者**无租户即 403（fail-closed）**、后者回退空串 ——
"没租户怎么办"取决于走到哪个路由。

本文件钉三件事：软读/硬读两种语义各自正确；上下文原语可嵌套且会恢复；
**路由层不许再出现直接读 `request.state.tenant_id` 的第四种写法**。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from maop.core.tenant.context import (
    current_tenant,
    require_tenant_id,
    tenant_context,
    tenant_id_from_request,
)

ROUTERS_DIR = Path(__file__).resolve().parents[1] / "maop" / "dashboard" / "routers"

#: 已收口的 7 个转发函数（函数名 → 所在文件）。它们必须存在（调用点在用），
#: 且实现必须是转发 —— 见 `test_legacy_wrapper_functions_still_delegate`。
LEGACY_WRAPPERS = {
    "agent_versions_helpers.py": "_tenant_id_from_request",
    "compliance.py": "_tenant_id_from_jwt",
    "data.py": "_request_tenant_id",
    "feedback.py": "_tenant_id_from_request",
    "memory.py": "_request_tenant_id",
    "notifications.py": "_tenant_id_from_request",
    "rbac.py": "_tenant_id_from_jwt",
}


def _request(tenant_id: str = "", **state: object) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id, **state))


# ── 两种语义 ─────────────────────────────────────────────────────────


def test_soft_read_returns_empty_when_absent() -> None:
    """单租户 / 个人版：没有租户身份时软回退成空串（不是 None，也不是异常）。"""
    assert tenant_id_from_request(_request("")) == ""
    assert tenant_id_from_request(_request("acme")) == "acme"


def test_soft_read_treats_none_as_empty() -> None:
    """`request.state.tenant_id` 被显式设成 None 也要回退成空串。"""
    assert tenant_id_from_request(_request(None)) == ""


def test_require_is_fail_closed_with_403() -> None:
    """合规/审计这类"没租户就没法正确回答"的接口必须 403，不许退化成"看到空/看到全部"。"""
    with pytest.raises(HTTPException) as ei:
        require_tenant_id(_request(""))
    assert ei.value.status_code == 403
    assert "tenant_id" in str(ei.value.detail)


def test_require_returns_tenant_when_present() -> None:
    assert require_tenant_id(_request("acme")) == "acme"


def test_only_source_is_request_state() -> None:
    """身份只认 `request.state.tenant_id` —— 绝不从 body/query 取。

    （G-07 修过的越权口子：调用方能随便声明自己属于哪个租户。这里用"给了 body/query
    也不影响结果"来钉住。）
    """
    req = _request("from-jwt")
    req.query_params = {"tenant_id": "attacker"}
    req.body = {"tenant_id": "attacker"}
    assert tenant_id_from_request(req) == "from-jwt"


# ── 上下文原语 ───────────────────────────────────────────────────────


def test_current_tenant_defaults_to_empty() -> None:
    assert current_tenant() == ""


def test_tenant_context_sets_and_restores() -> None:
    assert current_tenant() == ""
    with tenant_context("acme"):
        assert current_tenant() == "acme"
    assert current_tenant() == "", "退出作用域后必须恢复，否则会串到后续请求"


def test_tenant_context_nests() -> None:
    with tenant_context("outer"), tenant_context("inner"):
        assert current_tenant() == "inner"
    with tenant_context("outer"):
        assert current_tenant() == "outer"
    assert current_tenant() == ""


def test_tenant_context_yields_the_value() -> None:
    with tenant_context("acme") as tid:
        assert tid == "acme"


def test_tenant_context_restores_even_on_exception() -> None:
    with pytest.raises(RuntimeError), tenant_context("acme"):
        raise RuntimeError("boom")
    assert current_tenant() == "", "异常路径也必须恢复（否则一次失败会污染整条链）"


def test_empty_tenant_is_explicitly_set_not_absence() -> None:
    """空串也会被设置 —— "明确知道没有租户"与"没人设过"对下游是两回事。"""
    with tenant_context("acme"):
        with tenant_context(""):
            assert current_tenant() == ""
        assert current_tenant() == "acme"


# ── 防再抄一份 ───────────────────────────────────────────────────────


def _is_request_state(node: ast.expr) -> bool:
    """`request.state` 或 `getattr(request, "state", ...)` 两种写法都算。"""
    if isinstance(node, ast.Attribute) and node.attr == "state":
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "state"
    )


def _direct_state_reads(path: Path) -> list[int]:
    """找出直接读 `request.state.tenant_id`（或 getattr 等价写法）的行号。"""
    # utf-8-sig：仓里有三个路由文件带 BOM（__init__ / error_handler / v4_misc），
    # 用 utf-8 读会把 ﻿ 留在开头、ast.parse 直接 SyntaxError —— 扫描器因此
    # 会把"解析失败"静默当成"没有直接读取"。BOM 是合法 Python 源，必须正常读。
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    hits: list[int] = []
    for node in ast.walk(tree):
        # request.state.tenant_id
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "tenant_id"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "state"
        ):
            hits.append(node.lineno)
        # getattr(<request.state>, "tenant_id", ...) —— 只认**请求状态**上的，
        # 不认随便哪个对象上的 tenant_id：`getattr(result/notif/payload, "tenant_id")`
        # 读的是认证结果 / 通知模型 / JWT payload，与本收口无关。（早先版本一律命中，
        # 一下报出 3 处误报 —— 判据太宽的扫描器比没有更糟，会让人去"修"无关代码。）
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "tenant_id"
            and _is_request_state(node.args[0])
        ):
            hits.append(node.lineno)
    return hits


def test_no_router_reads_request_state_directly() -> None:
    """路由层不许再手抄一份租户读取 —— 一律走 `core/tenant/context`。

    没有这条，下次有人加路由时会照抄隔壁那份（这正是当初长出 7 份的原因），
    而"没租户怎么办"的语义就会再分叉一次。
    """
    offenders: list[str] = []
    for f in sorted(ROUTERS_DIR.rglob("*.py")):
        hits = _direct_state_reads(f)
        if hits:
            offenders.append(f"{f.relative_to(ROUTERS_DIR.parent.parent.parent)}:{hits}")
    assert not offenders, (
        "这些路由直接读了 request.state.tenant_id，请改用 "
        "maop.core.tenant.context.tenant_id_from_request / require_tenant_id：\n  "
        + "\n  ".join(offenders)
    )


def test_legacy_wrapper_functions_still_delegate() -> None:
    """7 个既有函数名必须还在，且实现是**转发**（不许又被填回手抄实现）。"""
    for filename, fn_name in LEGACY_WRAPPERS.items():
        src = (ROUTERS_DIR / filename).read_text(encoding="utf-8")
        assert f"def {fn_name}(" in src, f"{filename}: 转发函数 {fn_name} 不见了（调用点会断）"
        tree = ast.parse(src)
        node = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == fn_name),
            None,
        )
        assert node is not None
        calls = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert calls & {"tenant_id_from_request", "require_tenant_id"}, (
            f"{filename}:{fn_name} 没有转发到共享实现 —— 手抄实现又回来了"
        )


def test_scanner_is_not_vacuous() -> None:
    """先证明扫描器能真的抓到直接读取，否则上面那条会变成空断言。

    两个方向都要自证 —— 只证"能抓"会让判据越放越宽：
    * 正样本 `request.state.tenant_id` 必须抓到；
    * 反样本 `getattr(result, "tenant_id")`（认证结果 / 通知模型 / JWT payload 上是
      同一个属性名，但**不是**请求状态）必须**不**被抓 —— 早先版本一律命中，
      一次报出 3 处误报，会把人引去"修"无关代码。
    """
    sample = Path(__file__).parent / "_tenant_drift_probe_sample.py"
    sample.write_text(
        "from fastapi import Request\n\n"
        "def f(request: Request) -> str:\n"
        "    return request.state.tenant_id\n",
        encoding="utf-8",
    )
    other = Path(__file__).parent / "_tenant_drift_probe_other_object.py"
    other.write_text(
        "# 别的对象上的 tenant_id 不是本收口的目标，必须不被判红\n"
        "def g(result):\n"
        '    return getattr(result, "tenant_id", "")\n',
        encoding="utf-8",
    )
    try:
        assert _direct_state_reads(sample), "扫描器连明显的直接读取都抓不到"
        assert not _direct_state_reads(other), (
            "扫描器把别的对象的 tenant_id 也判红了（判据太宽 = 会误导人去改无关代码）"
        )
    finally:
        sample.unlink(missing_ok=True)
        other.unlink(missing_ok=True)
