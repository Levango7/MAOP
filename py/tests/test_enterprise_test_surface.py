"""个人版测试面的守卫：企业清单必须与真实代码一致。

两条**互相独立**的断言，分别堵住一类历史事故：

1. `test_declared_manifest_matches_module_level_imports`
   清单（tests/enterprise_test_manifest.py）与真实「模块顶层 import 企业包」的文件
   集合双向相等。旧的子串扫描让 20 个只在注释里提到企业包的文件被静默忽略
   （个人版 CI 少跑 456 条用例，报告里零痕迹）；反过来，新写的企业测试若忘了登记，
   个人版收集会直接 ImportError 崩掉整轮。两个方向都必须判红。

2. `test_non_manifest_enterprise_tests_carry_their_own_guard`
   未进清单、但在用例体里惰性 import（或 patch 字符串里点名）企业包的文件，必须自带
   跳过守卫。这类文件在个人版不会崩收集，而是运行时 ModuleNotFoundError / HTTP 500，
   历史事故正是「守卫被删掉」（4 处注释写着 `guard removed`：开发机装了企业包，于是
   个人版永远没人发现）。

本文件自身**不依赖**企业包，两个版本都必须跑。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.enterprise_test_manifest import (
    ENTERPRISE_INDIRECT,
    ENTERPRISE_MODULE_IMPORT_TESTS,
    ENTERPRISE_ONLY_TEST_FILES,
    TESTS_ROOT,
    module_scope_importers,
)

_ENTERPRISE_PREFIX = "maop.enterprise"
# 「带子模块」的写法才构成依赖：`maop.enterprise.audit`、`maop.enterprise.quota`。
# 裸 `"maop.enterprise"` 出现在 edition 判断、契约表、sys.modules 存取里，不代表依赖。
_ENTERPRISE_DEP_PREFIX = _ENTERPRISE_PREFIX + "."


def _enterprise_references(tree: ast.Module) -> list[str]:
    """文件里所有指向企业包的引用（任意作用域的 import + patch/setattr 目标字符串）。

    **只认「会真的去加载这个模块」的引用**。裸字符串常量不算：像
    ``assert "maop.enterprise.tenant" in mods`` 这种是把路径当**数据**比较
    （静态分析型测试的常见写法），运行时并不 import，个人版也不会崩。
    早先的实现把任意以 ``maop.enterprise.`` 开头的常量都算引用，于是这类
    文件被判成"缺跳过守卫"——守卫误报一次，就会逼人删掉真守卫或加假 skip。
    """
    refs: list[str] = []
    patch_targets: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        # mock.patch / monkeypatch.setattr 的第一个参数是模块路径，会在运行时解析。
        if name in {"patch", "setattr"} and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                patch_targets[id(arg)] = arg.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend(a.name for a in node.names if a.name.startswith(_ENTERPRISE_PREFIX))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            _ENTERPRISE_PREFIX
        ):
            refs.append(node.module or "")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(_ENTERPRISE_DEP_PREFIX)
            and id(node) in patch_targets
        ):
            refs.append(node.value)
    return refs


def _has_skip_mechanism(tree: ast.Module) -> bool:
    """文件里是否有任何跳过机制：`importorskip(...)` 调用或 `mark.skip*` 引用。

    只认机制、不认文案 —— 按 reason 字符串里有没有企业包字样判断，会被
    「reason 写成 maop-enterprise（全角括号）」这类正常写法骗过。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "importorskip":
                return True
        elif isinstance(node, ast.Attribute):
            if node.attr in {"skip", "skipif"}:
                return True
    return False


def test_declared_manifest_matches_module_level_imports():
    actual = module_scope_importers()
    declared = set(ENTERPRISE_MODULE_IMPORT_TESTS)

    missing = sorted(set(actual) - declared)
    assert missing == [], (
        "这些测试文件在模块顶层 import 了 maop.enterprise，但没登记到 "
        f"tests/enterprise_test_manifest.py: {missing}。个人版环境会在收集阶段 "
        "ImportError（整轮测试崩掉）。要么登记进清单，要么把 import 挪进用例体"
        "并自行加跳过守卫。"
    )

    stale = sorted(declared - set(actual))
    assert stale == [], (
        f"清单里这些文件已不再是「模块顶层 import 企业包」或已删除: {stale}。"
        "请从 ENTREPRISE_MODULE_IMPORT_TESTS 里删掉（否则个人版会无谓跳过它们）。"
    )


def test_manifest_entries_exist_on_disk():
    for rel in ENTERPRISE_ONLY_TEST_FILES:
        assert (TESTS_ROOT / rel).is_file(), f"清单条目指向不存在的文件: {rel}"


def test_indirect_entries_are_not_module_level_importers():
    """间接依赖条目不该同时也是「模块顶层 import」文件（否则归类错了）。"""
    module_level = set(module_scope_importers())
    for rel in ENTERPRISE_INDIRECT:
        assert rel not in module_level, (
            f"{rel} 已改为模块顶层 import 企业包，应移进 "
            "ENTERPRISE_MODULE_IMPORT_TESTS（否则注释与实际行为不符）"
        )


def test_bare_string_constant_is_not_a_dependency():
    """把模块路径当**数据**比较不算依赖；只有 import 与 patch 目标算。

    这条是守卫自身的精度回归：``test_tenant_authority.py`` 用静态 AST 断言
    dashboard 导入了 ``maop.enterprise.tenant``，字面量只出现在比较表达式里，
    运行时零 import。旧实现把它判成"缺跳过守卫"，属于误报。
    """
    data_only = ast.parse('assert "maop.enterprise.tenant" in mods\n')
    assert _enterprise_references(data_only) == []

    patched = ast.parse('with patch("maop.enterprise.license.LicenseValidator"):\n    pass\n')
    assert _enterprise_references(patched) == ["maop.enterprise.license.LicenseValidator"]

    imported = ast.parse("from maop.enterprise.tenant import TenantManager\n")
    assert _enterprise_references(imported) == ["maop.enterprise.tenant"]


def test_non_manifest_enterprise_tests_carry_their_own_guard():
    declared = set(ENTERPRISE_ONLY_TEST_FILES)
    this_file = Path(__file__).name
    unguarded: dict[str, list[str]] = {}
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel in declared or rel == this_file:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="ignore"))
        refs = _enterprise_references(tree)
        if refs and not _has_skip_mechanism(tree):
            unguarded[rel] = refs[:3]
    assert unguarded == {}, (
        "以下文件引用了企业包、却没进清单也没有自带跳过守卫 —— 个人版会在运行时报 "
        f"ModuleNotFoundError / 500: {unguarded}。请在文件（或相关用例）顶部加 "
        'pytest.importorskip("maop.enterprise...", reason=...)。'
    )
