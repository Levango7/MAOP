"""`.env.example` 必须覆盖代码实际读取的 MAOP_* 变量（2026-10-04）。

背景：`ROADMAP.md` 把"`.env.example` 与代码 `MAOP_*` 变量集合差异为零（或差异均有
明确注释说明）"勾成了 `- [x]`，但实测代码读取 118 个、`.env.example` 少 18 个 ——
含 v5.2.0 的旗舰开关 `MAOP_EVOLUTION_LOOP_ENABLED`。**一个被勾选却没有任何守卫的
验收项**，而且按它去配环境的客户会以为开关名是别的。

同一份 ROADMAP 里给这条验收撑腰的那份交付物（"env-audit-4.4.2.md"）本身也不在
仓库里（`deliverables/` 被 .gitignore 忽略），所以那条勾没有任何机械证据。

## 口径

变量的来源有**两条**，两条都得算 —— 只扫其中一条就会得出"差异为零"的假结论：

1. 直接读：``os.getenv("X")`` / ``os.environ.get("X")`` / ``os.environ["X"]``；
2. pydantic Settings：``MAOPSettings`` 的每个字段经 ``env_prefix="MAOP_"``
   映射成 ``MAOP_<FIELD_NAME_UPPER>``，外加 ``AliasChoices`` 里显式写出的别名。

（第 2 条是实测踩出来的：`MAOP_BUDGET_DAILY_LIMIT_USD` 这类只走 Settings 的变量
在只看 os.getenv 的扫描里"不存在"，于是"未读取"的反向断言会误报一大片。）

环境变量名两侧共用同一个正则，避免口径漂移。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "py" / "maop"
SETTINGS = ROOT / "py" / "maop" / "config" / "settings.py"
ENV_EXAMPLE = ROOT / ".env.example"

_NAME_RE = re.compile(r"MAOP_[A-Z0-9_]+")

# `.env.example` 里这些变量在本仓**确实没有读者** —— 消费方在私有仓 MAOS 的
# `maop.enterprise` 包里（ADR-017 起企业代码移出本仓）。它们必须留在这里：
# 企业版用户拿到的就是这个 .env.example。逐条写明理由，新增一条要有人负责。
ENTERPRISE_SIDE_VARS = {
    "MAOP_LICENSE_KEY": "商业 License 密钥；读取方在 maop.enterprise（MAOS）",
    "MAOP_CRL_URL": "License 吊销列表地址；企业版 CRL 链路读取",
    "MAOP_CRL_STRICT": "CRL 严格模式；企业版读取（本仓只在 tests 里出现）",
    "MAOP_CRL_CACHE_TTL_S": "CRL 缓存 TTL；企业版读取",
    "MAOP_CRL_MAX_CACHE_AGE_S": "CRL 缓存最长寿命；企业版读取",
    "MAOP_HA_BACKEND": "企业版 HA 后端（redis/etcd）；本仓只在 tests 与 ADR 里出现",
}


def _enclosing_env_object(func: ast.expr) -> bool:
    """``os.environ.get`` / ``environ.get`` 形式的调用才算"读环境变量"。"""
    if not isinstance(func, ast.Attribute):
        return False
    value = func.value
    if isinstance(value, ast.Attribute):          # os.environ.get
        return value.attr == "environ"
    if isinstance(value, ast.Name):               # environ.get（from os import environ）
        return value.id == "environ"
    return False


def _direct_reads() -> dict[str, set[str]]:
    """扫描 py/maop，找出所有直读的环境变量及其位置。

    只认三种真读法。不做 globals()/getattr 一类动态匹配 —— 那类写法扫不准，硬扫
    反而会造出"看着覆盖了、实际没覆盖"的假象。
    """
    found: dict[str, set[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法坏掉的模块交给 lint/编译类测试
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                func_name = func.attr if isinstance(func, ast.Attribute) else ""
                first = node.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                if (func_name == "getenv" and isinstance(func, ast.Attribute)) or (
                    func_name == "get" and _enclosing_env_object(func)
                ):
                    name = first.value
            elif isinstance(node, ast.Subscript):
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "environ"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    name = node.slice.value
            if name and name.startswith("MAOP_"):
                found.setdefault(name, set()).add(f"{rel}:{node.lineno}")
    return found


def _settings_reads() -> dict[str, set[str]]:
    """``MAOPSettings`` 字段 + 显式别名映射出的环境变量名。"""
    found: dict[str, set[str]] = {}
    tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
    rel = SETTINGS.relative_to(ROOT).as_posix()

    prefix = "MAOP_"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MAOPSettings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    found.setdefault(prefix + stmt.target.id.upper(), set()).add(
                        f"{rel}:{stmt.lineno}(字段 {stmt.target.id})"
                    )
    # AliasChoices("MAOP_X", "MAOP_Y") 里显式写的名字（字段名未必等于环境变量名）。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AliasChoices":
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith("MAOP_")
                ):
                    found.setdefault(arg.value, set()).add(f"{rel}:{node.lineno}(别名)")
    return found


def _all_read_vars() -> dict[str, set[str]]:
    merged = _direct_reads()
    for name, locs in _settings_reads().items():
        merged.setdefault(name, set()).update(locs)
    return merged


def test_selector_is_not_vacuous() -> None:
    """先证明两条来源都扫到了东西，否则下面的断言会静默变成空检查。

    守卫必须自证"能红"—— 本仓栽过同形的坑（见 docs/ci-gates.md §7）。
    """
    direct = _direct_reads()
    settings = _settings_reads()
    assert len(direct) >= 80, f"直读扫描只找到 {len(direct)} 个变量，选择器可能失效"
    assert "MAOP_DASH_PORT" in direct, "已知会被直读的 MAOP_DASH_PORT 没扫到"
    # Settings 这条来源必须单独自证：漏了它，"差异为零"就会是个假结论。
    assert "MAOP_BUDGET_DAILY_LIMIT_USD" in settings, (
        "Settings 字段映射没扫到 —— 只走 pydantic 的变量会被整类漏掉"
    )
    assert "MAOP_AUTH_ENABLED" in settings, "AliasChoices 别名没扫到"


def test_every_read_var_is_documented_in_env_example() -> None:
    """代码读的每一个 MAOP_* 变量都要能在 .env.example 里找到。"""
    found = _all_read_vars()
    declared = set(_NAME_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    missing = sorted(set(found) - declared)
    detail = "\n".join(f"  {name}  ← {sorted(found[name])[:2]}" for name in missing)
    assert not missing, (
        f"有 {len(missing)} 个被代码读取的 MAOP_* 变量没写进 .env.example：\n{detail}\n"
        "请补进 .env.example（含默认值）。ROADMAP 已把这条勾成完成，"
        "但它此前没有任何守卫 —— 别再让它退回去。"
    )


def test_env_example_has_no_unexplained_unread_vars() -> None:
    """反向：.env.example 里的 MAOP_* 若代码全不读，附近必须有说明。

    允许的例外是在文件里被显式标注过的（DEPRECATED 短名、别名、docker-compose
    专用等）—— 目的是防止 .env.example 变成"列了一堆没人认的开关"的清单，
    那与漏写同样会误导客户。
    """
    found = set(_all_read_vars())
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    lines = text.splitlines()
    declared = set(_NAME_RE.findall(text))
    allowed_markers = (
        "DEPRECATED", "deprecated", "alias", "别名", "docker-compose", "compose",
    )
    unexplained = []
    for name in sorted(declared - found - set(ENTERPRISE_SIDE_VARS)):
        line_no = next((i for i, ln in enumerate(lines, 1) if name in ln), None)
        if line_no is None:
            continue
        window = "\n".join(lines[max(0, line_no - 5):line_no + 3])
        if not any(marker in window for marker in allowed_markers):
            unexplained.append(f"{name} (行 {line_no})")
    assert not unexplained, (
        "这些声明在 .env.example 里但代码全不读，且附近没有 DEPRECATED/别名 等说明："
        f"{unexplained}。若它们由企业版（maop.enterprise，见 MAOS 仓）消费，"
        "请在 ENTERPRISE_SIDE_VARS 里登记并写明理由。"
    )


def test_enterprise_side_allowlist_is_not_stale() -> None:
    """登记过但已被本仓读到的条目要删掉，避免豁免清单变成僵尸。"""
    found = set(_all_read_vars())
    stale = sorted(name for name in ENTERPRISE_SIDE_VARS if name in found)
    assert not stale, (
        f"这些变量已经被本仓代码读到，不再需要企业版豁免：{stale}。"
        "请从 ENTERPRISE_SIDE_VARS 移除。"
    )


def test_enterprise_side_allowlist_entries_are_declared() -> None:
    """豁免清单里的名字必须在 .env.example 里真的存在（否则豁免毫无意义）。"""
    declared = set(_NAME_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    missing = sorted(set(ENTERPRISE_SIDE_VARS) - declared)
    assert not missing, f"豁免清单里的变量在 .env.example 中不存在：{missing}"
