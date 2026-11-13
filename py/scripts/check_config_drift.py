"""Config drift audit — ensures os.getenv count doesn't grow.

Runs in CI to prevent new os.getenv/os.environ calls from being added
without going through the settings module. The baseline is the current
count; any increase fails the check.

Also checks version sync: 版本号必须在 **四处** 站点一致 —— ``maop/__init__.py``、
``pyproject.toml``、``py/Dockerfile`` 的 ``LABEL version``、
``dashboard-enterprise/package.json``（能力自根 scripts/doc_reconcile.py
并入，2026-09-29；该文件本身从未被 CI 执行。四处站点清单来自 CHANGELOG.md
发布前 checklist 第 3 条，2026-10-06 补齐此前只比前两处的盲区）。

models.yaml 定价/配置漂移门禁（批 7，2026-11-13，板面 AGENT-COORD.md 批 7
CLAIM）：M1 引用覆盖 / M2 结构硬规则 / M3 来源注释棘轮 / M4 未知键。
判据绑定形态与变异电池见 py/tests/test_check_config_drift_models.py。

Usage: python scripts/check_config_drift.py
Exit: 0 = 无漂移；1 = getenv 增长 / 版本不一致 / models.yaml 定价或配置漂移
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml

# 2026-08-15: 148→226。8-01 后两周迭代新增的直接 env 读取
# （server.py 31 / sso_store 12 / backends 10 等存量配置读取 + T1 工具白名单
# 有意设计的 MAOP_TOOL_POLICY_* 覆盖接口）。门禁语义：只允许减少，防未来新增。
# 2026-08-17: 226→227。628dd56 的 P2 安全修复（db_backup.py VACUUM INTO 路径
# 白名单）新增 MAOP_BACKUP_DIR 读取 —— 有意配置，非 drift。
# 2026-10-04: 227→228。drivers.py::_agent_env() 把 AgentConfig.env 接到 delegate 链的
# 5 个 driver 上。合并口径 `{**os.environ, **config.env}` 是**既有约定**（cli_adapter.py、
# lifecycle/runtime.py ×2、mcp_hub_transport.py 已是同款 4 处）—— 本次只是让漏掉的这条
# 路对齐，不是新模式。这是**向子进程传环境**的 OS 级操作，不是配置读取，未走 settings
# 是有意为之（subprocess 没有"继承 + 覆盖"的 API，只能自己拼映射）。
# 另外把 `_agent_env` 说明文字里的字面量 os.environ 改成了"进程环境"：计数器是逐行文本
# 匹配，散文里的提法不该占额度（本仓在 test_ci_workflow_hygiene 栽过同形）。
# 228→229（2026-10-13）：migrations/pg/versions/003_multimodal_entries.py 的
# downgrade 守卫读 MAOP_MIGRATION_DESTRUCTIVE（M2.2 B2 新增迁移）。与 001/002/003
# 既有迁移的同款守卫同形态（破坏性迁移的显式确认开关），属有意配置非 drift。
# 229→240（2026-10-20，M0 首跑闭环）：+11，逐条如下——
#   cli.py +6：`maop start` 的引导层。要在 import server（settings 实例化）之前
#     决定 owner/auth 策略并把根目录传给子进程，只能直接读写进程环境；
#     `_personal_edition_requested` 读 MAOP_EDITION / MAOP_LICENSE_KEY 是
#     edition 检测的同款口径（config/edition.py 亦直读）。
#   config/home.py +2：home/根解析器本身，必须先于 settings 存在（循环依赖），
#     读 MAOP_HOME 与 MAOP_ROOT_DIR / 旧 MAOP_ROOT。
#   core/agent/delegation/depth.py +2：跨进程委托深度守卫（fork 炸弹修复）。
#     与 `_agent_env` 同一先例——向子进程传环境是 OS 级操作，不是配置读取；
#     深度标记必须在 spawn 点递增，走 settings 反而丢掉父进程值。
#   core/security/middleware.py +1：auth-disabled 分支读 MAOP_OWNER_MODE，
#     每个请求都要现值（owner 只允许回环），不能进 settings 缓存。
# 已刻意压掉的：`cmd_start` 复用 `_runtime_root()` 而非自己再写一次 env、
# 不再 setdefault MAOP_HOME（与 default_home() 同值）、owner+auth 两个写入
# 合成一行 `os.environ.update(...)`、cli_evolution 不再重复导出根目录。
# 240→241（2026-10-28）：config/edition.py 新增 `MAOP_SKIP_MAOS_VERSION_CHECK`
# 逃生阀（MAOS 版本地板不满足时允许联调继续启动）。edition 检测发生在 settings
# 实例化之前、且必须读"当前进程"的现值（与同文件既有 MAOP_EDITION / MAOP_ENV /
# MAOP_LICENSE_KEY 同口径），走 settings 会引入循环依赖与缓存语义 —— 属有意
# 配置读取，非 drift。
BASELINE = 241


def count_getenv_calls(root: pathlib.Path) -> int:
    """Count os.getenv/os.environ calls in non-test Python files."""
    count = 0
    for f in root.rglob("*.py"):
        if "test" in f.name or "test" in str(f.parent):
            continue
        if f.name == "settings.py":
            continue  # settings.py is the canonical config entry point
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "os.getenv" in line or "os.environ" in line:
                    count += 1
        except Exception:
            pass
    return count


# 版本站点清单（仓库根相对路径 → 提取正则）。新增站点只改这里：
# version_drift 与守卫用例 py/tests/test_version_sync_guard.py 都读这一份。
VERSION_SITES: tuple[tuple[str, str], ...] = (
    ("py/maop/__init__.py", r"""__version__\s*=\s*["']([^"']+)["']"""),
    ("py/pyproject.toml", r"""^version\s*=\s*["']([^"']+)["']"""),
    ("py/Dockerfile", r"""^LABEL\s+version=["']?([0-9][^"'\s]*)"""),
    ("dashboard-enterprise/package.json", ""),  # 空正则 = 按 JSON 取 version 字段
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def _site_version(root: pathlib.Path, rel: str, pattern: str) -> str | None:
    path = root / rel
    if not path.is_file():
        return None
    if pattern:
        m = re.search(pattern, _read(path), re.MULTILINE)
        return m.group(1) if m else None
    return _package_json_version(path)


def version_drift(py_dir: pathlib.Path) -> str:
    """比对全部版本站点，返回 "" = 一致。

    站点清单来自 CHANGELOG.md 发布前 checklist 的第 3 条（"版本号在 pyproject.toml /
    __init__.py / Dockerfile / package.json 同步"）。此前本函数只比前两个，
    后两处（``py/Dockerfile`` 的 ``LABEL version``、``dashboard-enterprise/package.json``
    的 ``version``）半抬不会有任何检查发现 —— 发版时会出现"装出来的包和跑起来的镜像
    不是同一个版本"这类难查故障。

    解析不到某站点也返回非空串，避免"门禁没跑"与"门禁全绿"同形。
    """
    root = py_dir.parent
    found: dict[str, str] = {}
    missing: list[str] = []
    for rel, pattern in VERSION_SITES:
        if (value := _site_version(root, rel, pattern)) is None:
            missing.append(rel)
        else:
            found[rel] = value

    if missing:
        return "无法解析版本站点: " + ", ".join(missing)
    distinct = set(found.values())
    if len(distinct) > 1:
        rendered = "  ".join(f"{name}={value}" for name, value in found.items())
        return f"版本漂移（{len(distinct)} 个不同值）: {rendered}"
    return ""


def _package_json_version(path: pathlib.Path) -> str | None:
    try:
        data = json.loads(_read(path))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


# ── models.yaml 定价/配置漂移门禁（批 7，2026-11-13）──────────────────
# 判据依据：AGENT-COORD.md 批 7 CLAIM（用户「可以，那就做」授权）。
#   M1 引用覆盖：agents.yaml 的 model 引用 ∈ models 键 ∪ 控制值
#      {auto,native,built-in,n/a} ∪ models.yaml 段级注释登记的未核豁免。
#   M2 结构硬规则：键 == model_id；成本成对且为正；provider ∈ providers 段，
#      或块内带「provider 仅作来源标注」豁免注（gpt-5.4 先例，providers 段
#      无 openai）。
#   M3 来源注释棘轮：带价条目块内须含 source: 与 fetched_at；已标注基线
#      只增不减；既知缺口实名钉死，防静默增长（批 6「本批不复核」存量）。
#   M4 未知键：ModelDef 无 extra=allow（schema.py:162），条目里拼错的键会被
#      registry.py:159 ModelDef(**entry) 静默丢弃（ProviderDef 反为
#      extra=allow 数据保全，schema.py:135，故不查 providers 段）。
MODELS_YAML = "config/models.yaml"
AGENTS_YAML = "config/agents.yaml"
CONTROL_MODEL_VALUES: tuple[str, ...] = ("auto", "native", "built-in", "n/a")
# 段级注释（2 空格缩进 #）＝未核登记/豁免登记的载体（批 6 块与 agnes 豁免行
# 同形态）；字段级注释（4 空格）是 source 标注所在。M1/M3 据此分流，
# 混同会把批 6 登记块里的 "source URL + fetched_at" 口径误判给相邻条目
# （step-3.7-flash 假绿陷阱）。
SECTION_COMMENT_RE = re.compile(r"^  #")
SOURCE_MARKERS = ("source:", "fetched_at")
SOURCE_ONLY_PROVIDER_NOTE = "provider 仅作来源标注"
# 既知缺口实名钉死：带价但无来源标注的存量条目（批 6 明文「本批不复核」）。
KNOWN_UNSOURCED_LEGACY: tuple[str, ...] = ("yi-large", "step-3.7-flash")
# 已钉 source 的基线条目数（只增不减；删标注即红）。
SOURCED_ANNOTATION_BASELINE = 4
# ModelDef 声明字段镜像（py/maop/model/schema.py，2026-11-13 快照）。
# 测试交叉核对 ModelDef.model_fields；schema 增删字段时此处必须同步。
MODEL_DEF_FIELDS = frozenset(
    {
        "name", "model_id", "provider", "family", "context_window", "max_output",
        "cost_per_1k_input", "cost_per_1k_output", "capabilities", "capability_matrix",
        "thinking", "default_temperature", "max_temperature", "latency_tier",
        "quality_tier", "enabled",
    }
)


def _section_text(raw: str, name: str) -> str:
    """抽 models/providers 段的原始文本（含注释）；段尾=下一个顶层键。"""
    m = re.search(rf"^{name}:\s*$", raw, re.MULTILINE)
    if not m:
        return ""
    nxt = re.compile(r"^[A-Za-z_][\w-]*:", re.MULTILINE).search(raw, m.end())
    return raw[m.end() : (nxt.start() if nxt else len(raw))]


def _entry_blocks(section: str) -> dict[str, str]:
    """按 2 空格缩进键切条目块（字段级注释随块，段级注释不入块）。"""
    blocks: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in section.splitlines():
        m = re.match(r"^  ([^\s#][^:]*):", line)
        if m:
            if key:
                blocks[key] = "\n".join(buf)
            key, buf = m.group(1), [line]
        elif key is not None and not SECTION_COMMENT_RE.match(line):
            buf.append(line)
    if key:
        blocks[key] = "\n".join(buf)
    return blocks


def models_yaml_drift(root: pathlib.Path) -> tuple[list[str], list[str]]:
    """models.yaml 定价/配置漂移判据。返回 (问题列表, 提示列表)，问题空=无漂移。"""
    problems: list[str] = []
    notes: list[str] = []
    models_path = root / MODELS_YAML
    if not models_path.is_file():
        return [f"无法解析 {MODELS_YAML}：文件缺失（门禁没跑与全绿不同形）"], notes
    raw_models = _read(models_path)
    data = yaml.safe_load(raw_models) or {}
    models = data.get("models") or {}
    providers = data.get("providers") or {}
    if not models:
        problems.append("M1 models.yaml 无 models 段（引用覆盖无从谈起）")

    # M1 引用覆盖：agents.yaml 的 model 引用必须落在已定价键/控制值/未核登记内
    agents_path = root / AGENTS_YAML
    if agents_path.is_file():
        agents_data = yaml.safe_load(_read(agents_path)) or {}
        refs: list[tuple[str, str]] = []
        for group in ("agents", "workflows"):
            for name, entry in (agents_data.get(group) or {}).items():
                if isinstance(entry, dict) and entry.get("model"):
                    refs.append((f"{group}.{name}", str(entry["model"])))
        registered = "\n".join(
            ln for ln in _section_text(raw_models, "models").splitlines()
            if SECTION_COMMENT_RE.match(ln)
        )
        priced = set(models)
        for where, ref in refs:
            if ref in CONTROL_MODEL_VALUES or ref in priced or ref in registered:
                continue
            problems.append(
                f"M1 引用未登记：{where} model={ref!r} 不在 models.yaml 键/控制值/"
                "未核登记注释内（补定价条目，或追加批 6 式未核登记注释）"
            )
    else:
        problems.append(f"无法解析 {AGENTS_YAML}：文件缺失")

    # M2 结构硬规则 + M4 未知键 + M3 来源注释棘轮
    blocks = _entry_blocks(_section_text(raw_models, "models"))
    sourced = 0
    for key, entry in models.items():
        if not isinstance(entry, dict):
            problems.append(f"M2 条目 {key} 不是映射")
            continue
        if "model_id" not in entry:
            problems.append(f"M2 缺 model_id：{key}（批 6 口径：逐字一致）")
        elif entry["model_id"] != key:
            problems.append(
                f"M2 键≠model_id：{key} 的 model_id={entry['model_id']!r}"
                "（registry 精确匹配以键为准，registry.py:158-159 先例）"
            )
        has_in = "cost_per_1k_input" in entry
        has_out = "cost_per_1k_output" in entry
        if has_in != has_out:
            problems.append(f"M2 成本字段须成对：{key} input={has_in}/output={has_out}")
        for side in ("cost_per_1k_input", "cost_per_1k_output"):
            v = entry.get(side)
            if v is not None and (
                isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0
            ):
                problems.append(f"M2 成本须为正数：{key}.{side}={v!r}")
        prov = entry.get("provider")
        if not prov:
            problems.append(f"M2 缺 provider：{key}")
        elif prov not in providers and SOURCE_ONLY_PROVIDER_NOTE not in blocks.get(key, ""):
            problems.append(
                f"M2 provider 未定义：{key}.provider={prov!r} 不在 providers 段，"
                "且块内无「provider 仅作来源标注」豁免注（gpt-5.4 先例）"
            )
        for fk in entry:
            if fk not in MODEL_DEF_FIELDS:
                problems.append(
                    f"M4 未知键 {key}.{fk}：ModelDef 无 extra=allow，"
                    "loader（registry.py:159 ModelDef(**entry)）会静默丢弃它"
                )
        block = blocks.get(key, "")
        if (has_in or has_out) and not all(mk in block for mk in SOURCE_MARKERS):
            if key not in KNOWN_UNSOURCED_LEGACY:
                problems.append(
                    f"M3 带价条目缺来源标注：{key} 块内须含 source: 与 fetched_at"
                    f"（基线 {SOURCED_ANNOTATION_BASELINE} 条只增不减）"
                )
        elif has_in or has_out:
            sourced += 1
    if sourced < SOURCED_ANNOTATION_BASELINE:
        problems.append(
            f"M3 来源标注棘轮：当前 {sourced} 条 < 基线 {SOURCED_ANNOTATION_BASELINE}——"
            "有人删了 source/fetched_at 注释，或新增条目未补标注"
        )
    for legacy in KNOWN_UNSOURCED_LEGACY:
        if legacy not in models:
            notes.append(f"M3 钉表 {legacy} 已不在 models.yaml，请移除 KNOWN_UNSOURCED_LEGACY 对应行")
        elif legacy in blocks and all(mk in blocks[legacy] for mk in SOURCE_MARKERS):
            notes.append(f"M3 钉表 {legacy} 已补 source/fetched_at，请移出 KNOWN_UNSOURCED_LEGACY")
    return problems, notes


def main() -> int:
    py_dir = pathlib.Path(__file__).resolve().parent.parent
    root = py_dir / "maop"
    current = count_getenv_calls(root)
    print(f"os.getenv/os.environ calls (excluding tests + settings): {current}")
    print(f"Baseline: {BASELINE}")
    failed = False
    if current > BASELINE:
        print(f"FAIL: +{current - BASELINE} new os.getenv calls detected.")
        print("Use maop.config.settings.get_settings() instead of os.getenv().")
        failed = True
    elif current < BASELINE:
        print(f"GOOD: -{BASELINE - current} calls removed since baseline. Update BASELINE.")
    else:
        print("OK: no drift.")

    drift = version_drift(py_dir)
    if drift:
        print(f"FAIL: {drift}")
        failed = True
    else:
        print("OK: version sync across all 4 sites (__init__ / pyproject / Dockerfile / package.json).")

    problems, notes = models_yaml_drift(py_dir.parent)
    for note in notes:
        print(f"NOTE: {note}")
    if problems:
        print(f"FAIL: models.yaml 定价/配置漂移 {len(problems)} 项:")
        for p in problems:
            print(f"  - {p}")
        failed = True
    else:
        print("OK: models.yaml 引用覆盖/结构/来源棘轮/未知键 四项无漂移。")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
