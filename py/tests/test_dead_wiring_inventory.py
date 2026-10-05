"""死接线清单：**机器校验**"哪些模块在本仓没有任何生产导入方"。

## 为什么要有它

评估这个仓时最容易出的两种错都是"凭印象数接线"：

* 把**活的**当成死的（我第一次扫就漏了相对导入 `from .routers.agents import crud`，
  于是把一批正常注册的路由误判成孤儿；也漏了 `python -m` 入口 ——
  `worker/agent_executor.py`、`worker/queue_worker.py` 是 docker-compose 直接拉起的）；
* 把**死的**当成活的（`dashboard/lifespan.py` 看着像"应用生命周期钩子"，实际
  `server.py:118` 自内联了一个 `lifespan()`，模块版本从来没被引用过）。

所以这里把"孤儿"变成一个**可执行的事实**：每次跑测试都用 AST 重算一遍，
与下面的 `KNOWN_ORPHANS` 逐条对齐。三种情况都会红：

1. 出现**新孤儿** —— 有人加了模块却没人调用；
2. 清单里的模块**被接上线** —— 该把它从清单里删掉；
3. 清单里的模块**被删了** —— 僵尸条目要清。

分类只表达"为什么它可以暂时留着"，不表达"它没问题"。

## 口径（与实现一致，改口径必须同步改注释）

* 只统计 `py/maop/**/*.py`，**排除包 `__init__.py`**（包由子模块/包路径隐含引用）
  与 `migrations/`（alembic 按文件路径加载，不走 import）；
* "被引用" = 非测试代码里出现绝对导入、**相对导入**（按定义文件所在包解析）、
  或 `from pkg import submodule` 形式；
* 另外把**外部拉起/字符串引用**也算被引用：仓库级配置文件里出现该模块点分路径
  （docker-compose / Dockerfile / pyproject / scripts / ecosystem.config.js / workflows）；
* **测试文件不算引用方** —— "只在测试里被用到"正是本清单要暴露的状态。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "py" / "maop"

#: 被外部拉起或按名字解析的模块点分路径会出现在这些位置。
EXTERNAL_REF_FILES = (
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "py/Dockerfile",
    "py/pyproject.toml",
    "start.sh",
    "ecosystem.config.js",
    ".github/workflows/ci.yml",
    ".github/workflows/nightly.yml",
)

#: 由**外部命令拉起**、因此本仓没有 import 方的模块。它们不算孤儿（外部引用扫描会
#: 把它们排除），但必须记在这里：否则下一个人重跑扫描会以为"模块被删了"或"扫描漏了"。
#: 这张表同时是外部引用机制的**正对照** —— 见 `test_external_launch_entries_are_excluded`。
EXTERNALLY_LAUNCHED: dict[str, str] = {
    "maop.worker.agent_executor": "docker-compose `agent-exec` 服务 entrypoint（python -m）",
    "maop.worker.queue_worker": "docker-compose `queue-worker` 服务 entrypoint（python -m）",
    "maop.__main__": "`python -m maop` 的入口，由解释器按约定加载，无 import 方",
}

#: 已知"零生产导入方"的模块。键=模块点分路径，值=(分类, 理由)。
#: 改动分类必须同时改理由 —— 这份清单的价值全在理由上。
KNOWN_ORPHANS: dict[str, tuple[str, str]] = {
    # ── 与已有实现重复、已被取代（留着会误导读者以为它在生效） ──
    "maop.dashboard.lifespan": (
        "superseded",
        "server.py:118 自内联了 lifespan()，本模块从未被引用；改这个文件不会生效",
    ),
    # ── 写了但没接线（真正待决策的一批） ──
    "maop.ab_test_framework": ("unwired", "生产 A/B 走 core/evolution/ab_test.py，本模块仅测试引用"),
    "maop.concurrency": ("unwired", "TaskPool/TaskQueue 无生产调用方"),
    "maop.config.agents_validator": ("unwired", "agents.yaml 校验未接入启动/CI 路径"),
    "maop.control.plane": ("unwired", "ControlPlane 仅测试引用；dashboard 控制面走 system_service"),
    "maop.core.agent.auth.harness_auth": ("unwired", "harness 鉴权未接入调度链"),
    "maop.core.agent.billing.quota_alert": ("unwired", "配额告警阈值/通知链无生产调用方"),
    "maop.core.agent.ops.capability_probe": ("unwired", "agent ops 子包整组未接线"),
    "maop.core.agent.ops.result_cache": ("unwired", "agent ops 子包整组未接线"),
    "maop.core.agent.ops.sandbox_executor": ("unwired", "agent ops 子包整组未接线"),
    "maop.core.agent.ops.tenant_isolation": ("unwired", "agent ops 子包整组未接线"),
    "maop.core.agent.ops.usage_statistics": ("unwired", "agent ops 子包整组未接线"),
    "maop.core.agent.registry.preset_agents": ("unwired", "预置 agent 未接入注册表初始化"),
    "maop.core.agent.router.health_check_scheduler": (
        "unwired",
        "健康检查调度器无生产调用方（本批修过它的 flake 测试，但没人启动它）",
    ),
    "maop.core.evolution.prompt_version": ("unwired", "prompt 版本链未接入演化闭环的回滚路径"),
    "maop.core.evolution.regression": ("unwired", "Persona 回归未接入演化闭环"),
    "maop.core.marketplace.key_management": ("unwired", "marketplace 密钥管理未接线"),
    "maop.core.marketplace.sandbox": (
        "superseded",
        (
            "core/security/sandbox.py 是在跑的沙箱；本模块的 env 白名单已于 2026-10-05 移植过去，"
            "剩下的 SandboxManager 是重复实现"
        ),
    ),    "maop.core.marketplace.signing": ("unwired", "marketplace 签名未接线"),
    "maop.core.mcp.mcp_adapter": ("unwired", "MCPAdapter 仅测试实例化；adapter_factory 不产出它"),
    "maop.core.memory.hybrid_search": ("unwired", "混合检索未接入记忆读路径"),
    "maop.core.memory.semantic_cache": ("unwired", "语义缓存未接入记忆读路径"),
    "maop.core.reliability.dag_scheduler": ("unwired", "DAG 调度器未接线"),
    "maop.core.reliability.pipeline_checkpoint": ("unwired", "pipeline checkpoint 未接线"),
    "maop.core.reliability.preemptable_worker_pool": ("unwired", "抢占式 worker 池未接线"),
    "maop.core.security.byok": ("unwired", "BYOK 未接线"),
    "maop.core.security.ldap_provider": ("unwired", "LDAP provider 未接线（SSO 走 SAML/OIDC）"),
    "maop.core.subagent_delegation": ("unwired", "v5.0 起已废弃，双实现说明仍留着"),
    "maop.core.vector.factory": ("dynamic", "向量后端按名字解析（需保持字符串契约）"),
    "maop.dashboard.provider": ("unwired", "dashboard 旧 provider 入口（v4.0.0 起 create_app 已废弃）"),
    "maop.dashboard.routers.error_handler": ("unwired", "路由层错误处理未挂到 app"),
    "maop.dashboard.static": ("unwired", "静态文件挂载未接入 server.py"),
    "maop.dashboard.ws_dag": ("unwired", "DAG WebSocket 未挂到 app"),
    "maop.delegate.doc_pipeline_adapter": ("unwired", "doc-pipeline 适配器未接线"),
    "maop.maop_execute": ("unwired", "全仓零生产调用方（权限门已改由 dispatch_gate 承担）"),
    "maop.maop_loop_config": ("unwired", "主循环配置走 loop_models.LoopConfig，本模块为并存实现"),
    "maop.memory.unified": ("unwired", "统一记忆协议未接入读路径"),
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(PKG).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["maop"] + parts)


def _resolve_relative(base_pkg: str, level: int, module: str | None) -> str | None:
    """把 `from ... import` 的 module 解析成绝对模块名（level=0 = 绝对导入）。"""
    if level == 0:
        return module
    parts = base_pkg.split(".")
    if level - 1 > len(parts):
        return None
    keep = parts[: len(parts) - (level - 1)]
    return ".".join(keep + ([module] if module else []))


def _modules() -> dict[Path, str]:
    return {p: _module_name(p) for p in sorted(PKG.rglob("*.py"))}


def _external_ref_text() -> str:
    chunks = []
    for rel in EXTERNAL_REF_FILES:
        f = REPO / rel
        if f.is_file():
            chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
    scripts = REPO / "scripts"
    if scripts.is_dir():
        for f in sorted(scripts.rglob("*")):
            if f.is_file() and f.suffix in {".py", ".sh", ".js", ".yml", ".yaml"}:
                chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def compute_orphans() -> dict[str, str]:
    """返回 {模块: 证据}，模块 = 本仓非测试代码里无人导入、且外部配置也没提到。"""
    files = _modules()
    importers: dict[str, set[str]] = {m: set() for m in files.values()}

    for p, m in files.items():
        pkg = m if p.name == "__init__.py" else m.rsplit(".", 1)[0]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法坏掉的模块由 lint 负责
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in importers:
                        importers[a.name].add(m)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_relative(pkg, node.level, node.module)
                if base is None:
                    continue
                if base in importers:
                    importers[base].add(m)
                for a in node.names:
                    sub = f"{base}.{a.name}"
                    if sub in importers:
                        importers[sub].add(m)

    external = _external_ref_text()
    out: dict[str, str] = {}
    for p, m in files.items():
        if importers.get(m) or m == "maop" or p.name == "__init__.py":
            continue
        if "migrations" in p.parts:          # alembic 按路径加载
            continue
        if p.name == "__main__.py":          # 解释器按约定加载的包入口（见 EXTERNALLY_LAUNCHED）
            continue
        if m in external:
            continue                          # 被 compose/Dockerfile/scripts 等拉起
        out[m] = f"零非测试导入方（{p.relative_to(REPO).as_posix()}）"
    return out


# ── 自证：选择器不能是空转的 ──────────────────────────────────────


def test_scanner_is_not_vacuous() -> None:
    """先证明扫描器确实在工作 —— 否则下面两条会变成"永远通过"的空断言。

    两个方向都要自证：已知活模块必须**不在**孤儿里；已知死模块必须**在**。
    """
    orphans = compute_orphans()
    assert "maop.cli" not in orphans, "已知被 pyproject [project.scripts] 引用的 cli 被误判为孤儿"
    assert "maop.delegate.dispatch_core" not in orphans, "主派发链被误判为孤儿"
    assert "maop.dashboard.lifespan" in orphans, (
        "已知死模块（server.py 自内联了 lifespan，本模块从未被引用）没被扫出来 —— 扫描器失效"
    )
    assert len(orphans) < 120, f"孤儿数 {len(orphans)} 明显异常，扫描口径可能坏了"


def test_external_launch_entries_are_excluded() -> None:
    """外部引用机制的正对照：按 `-m` 拉起的 worker 不许被算成孤儿。

    没有这条，`EXTERNAL_REF_FILES` 那份扫描坏掉（例如 docker-compose 改名）时会**静默**
    把 worker 当成新孤儿冒出来，看起来像"扫描更严格了"，实际是引用面漏了。
    """
    orphans = compute_orphans()
    for mod, how in EXTERNALLY_LAUNCHED.items():
        assert mod not in orphans, (
            f"{mod} 被算成孤儿了，但它是 {how} —— 外部引用扫描（EXTERNAL_REF_FILES）漏了它"
        )


# ── 主断言：实际孤儿集合必须与清单逐条一致 ────────────────────────


def test_orphan_set_matches_the_inventory() -> None:
    """三种漂移都要红：新孤儿 / 被接线 / 已删除。"""
    actual = set(compute_orphans())
    declared = set(KNOWN_ORPHANS)

    new_orphans = sorted(actual - declared)
    assert not new_orphans, (
        "这些模块没有任何生产导入方，也不在 KNOWN_ORPHANS 里：\n  "
        + "\n  ".join(new_orphans)
        + "\n要么接上调用方，要么加进清单并写明分类与理由。"
    )

    wired = sorted(declared - actual)
    assert not wired, (
        "这些模块已经被接上线（或已被删除），请从 KNOWN_ORPHANS 移除：\n  "
        + "\n  ".join(wired)
    )


def test_inventory_entries_are_specific() -> None:
    """清单条目必须给出可用的分类与理由 —— 空理由等于没有清单。"""
    allowed = {"dynamic", "superseded", "unwired"}
    for mod, (category, reason) in KNOWN_ORPHANS.items():
        assert category in allowed, f"{mod}: 未知分类 {category!r}"
        assert len(reason.strip()) >= 8, f"{mod}: 理由太短，等于没写"
    # superseded 的必须点名"被谁取代" —— 这类最容易让读者白改代码
    for mod, (category, reason) in KNOWN_ORPHANS.items():
        if category == "superseded":
            assert any(tok in reason for tok in (".py", "server.py")), (
                f"{mod}: superseded 条目要点名取代者文件，否则读者不知道该改哪里"
            )
