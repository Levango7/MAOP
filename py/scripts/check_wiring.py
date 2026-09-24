"""接线检查 —— 找出「写了但没接进运行时」的模块（死代码 / 仅供测试使用）。

## 为什么需要这个脚本

本项目反复出现同一类问题：**模块实现完整、测试齐全，但零运行时调用方**。
实例（2026-09-24 排查，AST 取证）：

  - ``core/marketplace/signing.py``      349+ 行 —— 零导入方
  - ``core/marketplace/key_management.py`` 545 行 —— 零导入方
  - ``core/marketplace/sandbox.py``      507 行  —— 零导入方
  - ``core/mcp/tool_signing.py``         207 行  —— 仅被下一条导入
  - ``core/mcp/tool_discovery.py``       293 行  —— 零导入方

合计 **1,901 行**，全部有通过测试，全部从未被任何运行时路径调用。

2026-09-25 后续：``tool_signing.py`` 与 ``tool_discovery.py``（共 500 行）
经逐项比对确认与 ``core/marketplace/*`` **功能完全重叠**且无任何独有补充，
已删除；唯一有价值的补充（PEM 一站式密钥生成）已移植为
``marketplace.signing.generate_keypair``。现剩三件套 1,401 行待排期接入。

危害不在于"代码白写了"，而在于**制造虚假信心**：
  - 测试全绿 ⇒ 读者以为该能力已上线
  - 审计者看到 1,901 行安全代码 ⇒ 可能误判这些控制项已生效
  - ROADMAP 标注"待排期" ⇒ 与代码现状（已完成大半）脱节，可能被重复实现

**测试通过 ≠ 功能可用。二者正交。**

## 判定方法

对 ``maop/core/`` 下的每个非 ``__init__`` 模块，判断是否存在**非测试导入方**：

  1. 解析所有 ``maop/`` 文件的 AST，收集 ``import`` / ``from ... import``
     （含函数体内的延迟导入）。
  2. **额外**识别字符串式 re-export 映射 —— 本项目大量使用
     ``_LAZY_EXPORTS = {"SemanticCacheEntry": "semantic_cache", ...}``
     这种 ``__getattr__`` 惰性导出。只做 AST import 分析会把它误判为死代码
     （实测：漏掉这类边会让假阳性率显著上升）。
  3. 无任何非测试导入方 → 记为未接线，再按有无测试导入方细分：
     - ``ORPHAN``    —— 无任何导入方
     - ``TEST_ONLY`` —— 仅被 ``tests/`` 导入（最隐蔽：测试全绿但用户碰不到）

## 作用域与局限（重要 —— 决定本工具**不是**门禁）

本工具是**人工分诊辅助**，不是 CI 门禁。它**恒返回 0**，输出仅供人工复核。

### 为什么不能当门禁

实测迭代 5 轮，每修掉一类假阳性又暴露下一类：

  1. ``__init__.py`` 的包解析（``a/b/__init__.py`` 的包是 ``a.b`` 而非 ``a``）
  2. 带类型注解的赋值（``x: dict[str,str] = {...}`` 是 ``AnnAssign``）
  3. 字符串式 re-export（``_SYMBOL_TO_MODULE`` + ``__getattr__`` 惰性导出）
  4. re-export **不等于**被使用（导出只让符号可导入，不构成接线）
  5. 传递性死链（仅被其他未接线模块导入）

剩余假阳性来自**三套动态加载机制**，静态导入分析看不到：

  - **dashboard 路由注册表** —— ``include_router`` 由加载器按目录/注册表装载
  - **reliability 服务工厂** —— ``services.py`` 的 ``_make_*`` 按名注册后惰性构造
  - **Edition 门控** —— 企业版模块由运行时 edition 检测激活

实测若把这些一并当作"未接线"，会把 ``core/tenant/*``、``core/vector/*``、
``core/security/*`` 等**正在生效**的模块全部误报（占比可达 30%+）。

要真正判定需要「根节点集合 + 上述 3 套动态机制感知」的完整可达性分析，
那是一个独立项目，不在本脚本范围内。

**宁可工具窄而结论可靠，也不要覆盖广而结论不可信。**

## 用法

    python scripts/check_wiring.py

输出三类候选供人工分诊：

  - ``ORPHAN``        —— 无任何导入方（既无运行时、无测试、也不被包 re-export）
  - ``TEST_ONLY``     —— 仅被 tests/ 导入（最隐蔽：测试全绿但用户碰不到）
  - ``EXPORTED_ONLY`` —— 仅被包的 ``__init__`` 真实 import（可通过包名导入，
                         但**没有任何直接消费者**）。与 ORPHAN 必须区分：
                         前者"能用但没人用"，后者"彻底无人引用"。
  - ``DEAD_CHAIN``    —— 仅被其他未接线模块导入

``KNOWN_UNWIRED`` 中登记的是**已人工取证确认**的未接线模块（含原因与后续打算）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PY_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = PY_ROOT / "maop" / "core"
TESTS_ROOT = PY_ROOT / "tests"

# 合法模块名的形状（用于从字符串映射中识别"这是模块名"）
_MODULE_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*$")


# ── 已知未接线模块（已决策，不触发失败）────────────────────────────
# 每条必须写明**为什么留着**与**后续打算**，否则退化成"用豁免掩盖问题"。
KNOWN_UNWIRED: dict[str, str] = {
    "maop.core.marketplace.signing": (
        "Marketplace 包签名（G-01：以 Ed25519 非对称签名替代 HMAC-SHA256）。"
        "设计与测试均完整（test_marketplace_f301.py），零运行时导入方。"
        "待决策：接入 MCP marketplace 安装路径，或删除。"
    ),
    "maop.core.marketplace.key_management": (
        "签名密钥分发 / 轮换 / 吊销 / 黑名单（545 行，含完整测试）。零导入方。"
        "**接入签名校验前必须先接这个** —— 否则密钥无法轮换，"
        "一次泄露就只能改代码。"
    ),
    "maop.core.marketplace.sandbox": (
        "G-02：以白名单环境替换 os.environ.copy()，防止 JWT_SECRET / DB_PASSWORD / "
        "API_KEY 泄漏进沙箱子进程。零导入方。"
        "**已复核：线上代码未使用 os.environ.copy()**（全仓仅本模块注释提及），"
        "故当前无实际泄漏 —— 但也意味着该防护并未生效。"
    ),
    # 2026-09-25 已删除（不再需要登记）：maop.core.mcp.tool_signing 与
    # maop.core.mcp.tool_discovery。逐项比对后确认与 marketplace 三件套
    # 完全重叠，无独有补充；唯一有价值的 PEM 一站式生成已移植进 signing.py。
}


# ── AST 分析 ─────────────────────────────────────────────────────


def _module_name(path: Path) -> str:
    rel = path.relative_to(PY_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(path: Path) -> list[str]:
    """模块所在的**包**路径分量。

    ``__init__.py`` 是特例：``a/b/__init__.py`` 的模块名是 ``a.b``，
    它的包**就是** ``a.b``（其内的相对导入 ``from .x import`` 指向 ``a.b.x``）。
    若一律取 ``模块名[:-1]``，``__init__.py`` 会被算成 ``a``，
    于是 ``from .x`` / 符号映射都解析到错误位置 —— 实测这会让
    ``__getattr__`` 惰性导出的模块被误判为死代码。
    """
    mod = _module_name(path)
    return mod.split(".") if path.name == "__init__.py" else mod.split(".")[:-1]


def _lazy_export_targets(tree: ast.AST, pkg: list[str]) -> set[str]:
    """从 ``_LAZY_EXPORTS`` 这类「字符串→模块名」映射中提取被导出的模块。

    本项目用 ``__getattr__`` + 字符串映射做惰性导出，例如::

        _LAZY_EXPORTS = {"SemanticCacheEntry": "semantic_cache", ...}

    这类导出**是真实的公共 API**（``from maop.core.memory import SemanticCacheEntry``
    可用），但没有任何 ``import`` 语句，纯 AST import 分析会把它误判为死代码。
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        # 只看 {str: str} 形状的字典字面量。
        # 必须同时处理 ``x = {...}``（Assign）与 ``x: dict[str,str] = {...}``
        # （AnnAssign）—— 本项目用的是后者，只认 Assign 会漏掉全部映射，
        # 导致 __getattr__ 惰性导出的模块被误判为死代码（实测踩过）。
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        values = value.values
        if not values or not all(isinstance(v, ast.Constant) for v in values):
            continue
        for v in values:
            name = v.value  # type: ignore[attr-defined]
            if not isinstance(name, str) or not _MODULE_RE.match(name):
                continue
            # 相对本文件所在包解析；同包内的兄弟模块最常见
            if "." in name:
                found.add(f"maop.{name}" if not name.startswith("maop") else name)
            else:
                found.add(".".join([*pkg, name]))
    return found


def _imports_of(path: Path) -> set[str]:
    """该文件引用的 maop 内部模块名（含延迟导入与字符串式 re-export）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()

    pkg = _package_of(path)
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join([*base, node.module]) if node.module else ".".join(base)
            else:
                mod = node.module or ""
            if mod.startswith("maop"):
                found.add(mod)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("maop"):
                    found.add(alias.name)

    found |= _lazy_export_targets(tree, pkg)
    return {m for m in found if m.startswith("maop")}


def _collect(root: Path, skip_inits: bool) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if skip_inits and path.name == "__init__.py":
            continue
        out[_module_name(path)] = _imports_of(path)
    return out


# ── 主流程 ────────────────────────────────────────────────────────


def main() -> int:
    core = _collect(CORE_ROOT, skip_inits=True)
    if not core:
        print(f"FAIL: 未找到 core 模块（{CORE_ROOT}）")
        return 1

    # 运行时侧导入方。
    #
    # **严格定义：只有「非 __init__ 的运行时文件」的真实 import 才算接线。**
    #
    # 为什么不把 __init__.py 的 re-export 算作接线：re-export（无论
    # `from .x import Y` 还是 `__getattr__` + 字符串映射）只让符号**可被导入**，
    # 不等于**被使用**。实测反例：`ToolDiscovery` / `HybridSearch` / `MCPAdapter`
    # 都被 mcp/__init__.py、memory/__init__.py 导出，但全仓没有任何运行时
    # 代码使用它们 —— 若把 re-export 算作接线，这类模块会被漏报，
    # 恰好漏掉本工具要抓的那一类（"可导出但无人用"）。
    runtime_importers: dict[str, set[str]] = {}
    for path in sorted((PY_ROOT / "maop").rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        src = _module_name(path)
        for dep in _imports_of(path):
            if dep != src:
                runtime_importers.setdefault(dep, set()).add(src)

    test_imported: set[str] = set()
    if TESTS_ROOT.exists():
        for path in sorted(TESTS_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            test_imported |= _imports_of(path)

    # 只被包的 __init__ 真实 import 的模块（re-export 但无直接消费者）。
    # 单列一类而不是并进 ORPHAN：前者"可通过包名导入"，后者"彻底无人引用"，
    # 分诊结论完全不同。实测若混为一谈，会把 `core/tenant/*` 误报为死代码 ——
    # 而 `maop.core.tenant` 实际被 routers/compliance.py 与
    # services/rbac_service.py 使用，只是用的不是这几个子模块。
    exported_only: set[str] = set()
    for path in sorted((PY_ROOT / "maop").rglob("__init__.py")):
        if "__pycache__" in path.parts:
            continue
        for dep in _imports_of(path):
            if dep != _module_name(path):
                exported_only.add(dep)

    unwired: dict[str, str] = {}
    for mod in sorted(core):
        if runtime_importers.get(mod):
            continue
        if mod in test_imported:
            unwired[mod] = "TEST_ONLY"
        elif mod in exported_only:
            unwired[mod] = "EXPORTED_ONLY"
        else:
            unwired[mod] = "ORPHAN"

    # 传递性分析（迭代到不动点）：
    # 若某模块的**全部**运行时导入方本身都未接线，那它同样不可达
    # —— 即 DEAD_CHAIN。漏掉这一步会放过整条死代码链：
    # 实测 `tool_signing` 被 `tool_discovery` 导入，而后者零导入方，
    # 不做传递分析时 `tool_signing` 会被误判为"已接线"。
    changed = True
    while changed:
        changed = False
        for mod in core:
            if mod in unwired:
                continue
            importers = runtime_importers.get(mod, set())
            # 只看 core 范围内的导入方（core 外的可达性不在此工具作用域内）
            core_importers = {i for i in importers if i in core}
            if core_importers and core_importers <= set(unwired):
                unwired[mod] = "DEAD_CHAIN"
                changed = True

    print(f"作用域: maop/core/（{len(core)} 个模块，不含 __init__）")
    print(f"未接线: {len(unwired)}")
    print()

    known_hit = {m: k for m, k in unwired.items() if m in KNOWN_UNWIRED}
    new_unwired = {m: k for m, k in unwired.items() if m not in KNOWN_UNWIRED}

    if known_hit:
        print(f"已知未接线 {len(known_hit)} 个（已登记豁免）:")
        for mod, kind in sorted(known_hit.items()):
            print(f"  · [{kind}] {mod}")
        print()

    if new_unwired:
        print(f"候选 {len(new_unwired)} 个（未登记，需人工分诊）:")
        for mod, kind in sorted(new_unwired.items()):
            print(f"  · [{kind}] {mod}")
        print()
        print("提示: 上述为**候选**，含已知假阳性（见模块 docstring 的『作用域与局限』）。")
        print("      人工确认确实未接线后，登记到 KNOWN_UNWIRED 并写明原因与后续打算。")
        print()

    stale = [m for m in KNOWN_UNWIRED if m not in unwired]
    if stale:
        print("提示: 以下豁免条目已不再未接线，可从 KNOWN_UNWIRED 移除：")
        for mod in sorted(stale):
            print(f"  · {mod}")
        print()

    print(f"人工取证确认的未接线: {len(known_hit)} 处（已登记豁免）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
