"""前后端 API 契约检查 —— 前端调用的 /api/* 是否在后端存在。

Runs in CI to catch path mismatches between the Vue frontend and the
FastAPI backend. A mismatch is invisible to unit tests: the frontend tests
mock whatever path the frontend itself writes, so a wrong path is validated
against itself. Only comparing the two real route sets catches it.

## 为什么需要这个检查（2026-09-23）

真实渲染验证时发现 `/operate/alerts` 页面报
``Unexpected token '<', "<!DOCTYPE "... is not valid JSON``。
排查结果：**后端有实现，前端调错了路径** ——
前端 `/api/alerts/rules`，后端 `/api/audit/alert/rules`。

同类错配共 19 处（告警规则 / 审计规则 / SSO 提供商），
其中 SSO 是多了 ``/v1/`` 前缀。

更麻烦的是：未注册的 API 路径会被 SPA 兜底路由接住返回
``200 + index.html``（见 ``_register_routes.register_static_routes``），
**所以只检查 HTTP 状态码完全发现不了**，必须看 content-type。

## 检查内容

1. 解析后端：``APIRouter(prefix=...)`` + 各 ``@router.<method>`` 装饰器
   （含别名导入 ``from ... import audit as audit_router``）
2. 解析前端：``api.get/post/put/delete`` 的字面量与模板字符串调用
   （``${expr}`` 归一为路径参数占位）
3. 比对方法 + 路径模板，报告前端调用但后端不存在者

## 已知盲区（脚本会显式打印，不静默）

- 纯变量调用（如 ``api.get(url)``）无法静态解析。当前 5 处 / 272 处，
  解析覆盖率 98%。若该数字上升，说明前端新增了动态拼接调用，
  需要人工确认那些路径。

Usage: python scripts/check_api_contract.py
Exit: 0 = 通过（无新增错配）；1 = 发现未登记的前端→后端错配
"""

from __future__ import annotations

import pathlib
import re
import sys

# 仓库根：py/scripts/check_api_contract.py → py/ → <repo>
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
BACKEND = REPO / "py" / "maop"
FRONTEND = REPO / "dashboard-enterprise" / "src"

# ── 已知「前端调用但后端无实现」的端点 ────────────────────────────────
# 这些是**功能未实现**（非路径错配），需要产品决策，故登记豁免。
# 语义：新增错配会让 CI 失败；修好一项后请从本表删除（脚本会提示）。
# key = (METHOD, 路径)，路径中路径参数写作 {}
KNOWN_MISSING: dict[tuple[str, str], str] = {
    # ── 真实功能缺口 ───────────────────────────────────────────────
    # 2026-09-23 已实现的 7 项已全部从本表移除：
    #   DELETE /api/model-gateway/usage        （clear_daily_usage，清内存+SQLite）
    #   PUT    /api/mcp/servers/{}             （hub.update_server，保留 id）
    #   PUT    /api/model-gateway/permissions/{}（复用 add_permission 的 upsert）
    #   GET    /api/mcp/topology               （servers/tools/agents/edges 聚合）
    #   GET    /api/mcp/stats                  （MCPCallStats 按小时分桶，call_tool 采集）
    #   PUT    /api/mcp/tools/{}               （mcp_tool_limits 表 + call_tool 真实限流）
    #   GET    /api/hooks/{}/history           （hook_logs 表 + response_code 结构化字段）
    #
    # ── 假阳性（动态拼接，非真实错配）──────────────────────────────
    ("POST", "/api/control/{}"):
        "假阳性：ControlPanel.vue 用 `/api/control/${action}` 动态派发，"
        "action 取值来自 control.py 已注册的 run/pause/resume/stop 等。"
        "静态解析无法得知运行时取值。",
}

# 解析前端调用：模板字符串 / 单引号 / 双引号
_CALL = re.compile(
    r"api\.(get|post|put|delete)\(\s*(?:`([^`]*)`|'([^']*)'|\"([^\"]*)\")",
    re.IGNORECASE | re.DOTALL,
)
# 任意 api.xxx( 调用（用于统计盲区）
_ANY_CALL = re.compile(r"api\.(get|post|put|delete)\(", re.IGNORECASE)
# 后端：APIRouter(prefix="...")
_PREFIX = re.compile(r"APIRouter\([^)]*?prefix\s*=\s*[\"']([^\"']*)[\"']", re.DOTALL)
# 后端：@router.<method>("...")
_DECORATOR = re.compile(
    r"@\w+\.(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']"
)


def _normalize(path: str) -> str:
    """去查询串，把路径参数与模板插值统一成 {}。"""
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path.rstrip("/") or "/"


def parse_backend() -> set[tuple[str, str]]:
    """解析后端全部路由为 (METHOD, 路径模板) 集合。"""
    routes: set[tuple[str, str]] = set()
    for f in BACKEND.rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        prefixes = _PREFIX.findall(text) or [""]
        for method, sub in _DECORATOR.findall(text):
            for prefix in prefixes:
                full = (prefix.rstrip("/") + "/" + sub.lstrip("/")).rstrip("/") or "/"
                routes.add((method.upper(), _normalize(full)))
    return routes


def parse_frontend() -> tuple[set[tuple[str, str]], int, list[str]]:
    """解析前端调用，返回 (路由集合, 无法解析的调用数, 样例)。"""
    calls: set[tuple[str, str]] = set()
    unresolved = 0
    samples: list[str] = []
    for f in FRONTEND.rglob("*.vue"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        total = len(_ANY_CALL.findall(text))
        resolved = 0
        for m in _CALL.finditer(text):
            raw = m.group(2) or m.group(3) or m.group(4) or ""
            if raw.startswith("/api"):
                calls.add((m.group(1).upper(), _normalize(raw)))
                resolved += 1
        missing = total - resolved
        if missing > 0:
            unresolved += missing
            for mm in re.finditer(
                r"api\.(get|post|put|delete)\(\s*([^`'\"]\S*)", text, re.IGNORECASE
            ):
                if len(samples) < 8:
                    samples.append(f"{f.name}: api.{mm.group(1)}({mm.group(2)[:30]}")
    return calls, unresolved, samples


def is_covered(method: str, path: str, backend: set[tuple[str, str]]) -> bool:
    """前端路径是否被后端某条路由覆盖（支持路径参数与前缀省略）。"""
    norm = _normalize(path)
    if (method, norm) in backend:
        return True
    base = norm.rstrip("/")
    for bm, bp in backend:
        if bm != method:
            continue
        b = bp.rstrip("/")
        if b == base:
            return True
        # 前端省略了路径参数（如 '/api/agents/' 对应 '/api/agents/{}'）
        if b.startswith(base + "/") and "{}" in b:
            return True
    return False


def main() -> int:
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        print(f"FAIL: 目录不存在 —— backend={BACKEND} frontend={FRONTEND}")
        return 1

    backend = parse_backend()
    frontend, unresolved, samples = parse_frontend()

    print(f"后端路由: {len(backend)}")
    print(f"前端调用: {len(frontend)}")
    total_calls = len(frontend) + unresolved
    print(f"解析覆盖率: {len(frontend)}/{total_calls} "
          f"({len(frontend) * 100 // max(total_calls, 1)}%)")
    if unresolved:
        print(f"  ⚠ 无法静态解析的调用: {unresolved} 处（纯变量形式，需人工确认）")
        for s in samples:
            print(f"      {s}")

    missing = sorted(
        (m, p) for m, p in frontend if not is_covered(m, p, backend)
    )

    new = [x for x in missing if x not in KNOWN_MISSING]
    fixed = [x for x in KNOWN_MISSING if x not in missing]

    print()
    if new:
        print(f"FAIL: {len(new)} 处前端调用了后端不存在的端点：")
        for method, path in new:
            print(f"  {method:<7} {path}")
        print()
        print("修法二选一：")
        print("  a) 改前端路径，指向后端真实端点")
        print("  b) 若确属功能未实现，加入本脚本的 KNOWN_MISSING 并写明原因")
        print()
        print("提示：未注册的 /api/* 会被 SPA 兜底返回 200 + HTML，")
        print("      前端 res.json() 报 \"Unexpected token '<'\" —— 只查状态码发现不了。")

    if fixed:
        print(f"NOTE: {len(fixed)} 处已登记的缺失端点现已可用，请从 KNOWN_MISSING 删除：")
        for method, path in sorted(fixed):
            print(f"  {method:<7} {path}")

    if new:
        return 1

    if missing:
        print(f"OK: 无新增错配。已知未实现端点 {len(missing)} 处（已登记豁免）。")
    else:
        print("OK: 前端调用的端点全部在后端存在。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
