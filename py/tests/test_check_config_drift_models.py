"""check_config_drift.py 的 models.yaml 判据钉测 + 变异电池（批 7）。

变异电池纪律（记忆「变异电池的判据强度」）：每例＝真实 config/ 的字节级
副本（先比 sha256 证明夹具＝真文件而非合成品）+ 一处外科手术式编辑（先
断言待替换字节在副本中真实存在且唯一），再跑门禁断言按绑定形态报错。
任何变异未触发＝判据逃逸，须收紧判据后重跑同一电池。
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "py" / "scripts" / "check_config_drift.py"

_spec = importlib.util.spec_from_file_location("check_config_drift", SCRIPT)
assert _spec is not None
drift = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(drift)


def _sandbox(base: Path) -> Path:
    """真实 config/ 的字节级副本；sha256 对账证明夹具来源。"""
    cfg = base / "config"
    cfg.mkdir(parents=True)
    for name in ("models.yaml", "agents.yaml"):
        real = REPO / "config" / name
        copy = cfg / name
        shutil.copyfile(real, copy)
        assert hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(
            real.read_bytes()
        ).digest(), f"{name} 副本与真文件不一致"
    return base


def _edit(path: Path, old: str, new: str) -> None:
    """单处替换；待替换字节必须真实存在且唯一（防判据被合成夹具蒙混）。"""
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"变异锚点不唯一或不存在（{count} 处）: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _problems(root: Path) -> list[str]:
    problems: list[str] = drift.models_yaml_drift(root)[0]
    return problems


# ── 真树常态 ──────────────────────────────────────────────


def test_real_tree_clean():
    assert _problems(REPO) == []


def test_reference_census_pinned():
    """agents.yaml model 引用 census 钉死（批 7 CLAIM 口径）：
    14 distinct = 4 控制值 + 5 已定价引用 + 4 未核登记 + 1 agnes 豁免。"""
    data = yaml.safe_load((REPO / "config" / "agents.yaml").read_text(encoding="utf-8"))
    refs = set()
    for group in ("agents", "workflows"):
        for entry in (data.get(group) or {}).values():
            if isinstance(entry, dict) and entry.get("model"):
                refs.add(str(entry["model"]))
    assert refs == {
        "auto", "native", "built-in", "n/a",
        "step-3.7-flash", "qwen3.5-plus", "yi-large", "gpt-5.4", "MiniMax-M3",
        "deepseek-v4-flash-free", "omniroute-auto-coding", "minimax-m2.7",
        "mimo-v2.5", "agnes-2.0-flash",
    }


# ── M1 引用覆盖 ───────────────────────────────────────────


def test_m1_unregistered_ref_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(root / "config" / "agents.yaml", "    model: agnes-2.0-flash\n", "    model: ghost-model-x\n")
    assert any(p.startswith("M1") and "ghost-model-x" in p for p in _problems(root))


def test_m1_control_values_pass(tmp_path):
    for i, cv in enumerate(drift.CONTROL_MODEL_VALUES):
        root = _sandbox(tmp_path / f"ctrl{i}")
        _edit(root / "config" / "agents.yaml", "    model: agnes-2.0-flash\n", f"    model: {cv}\n")
        assert _problems(root) == []


def test_m1_registered_unpriced_passes(tmp_path):
    root = _sandbox(tmp_path)
    _edit(root / "config" / "agents.yaml", "    model: agnes-2.0-flash\n", "    model: minimax-m2.7\n")
    assert _problems(root) == []


def test_m1_exemption_line_removed_fires(tmp_path):
    """豁免登记行是 agnes-2.0-flash 的唯一放行依据：删行即红（绑定段级注释形态）。"""
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "  # agnes-2.0-flash：agnes 族未计价保留（早前用户裁定），agents.yaml:271 引用\n"
        "  # 的豁免登记，不入表（批 7 门禁 M1 认此行）。\n",
        "",
    )
    assert any(p.startswith("M1") and "agnes-2.0-flash" in p for p in _problems(root))


# ── M2 结构硬规则 ─────────────────────────────────────────


def test_m2_key_model_id_mismatch_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "  yi-large:\n    model_id: yi-large\n",
        "  yi-large-renamed:\n    model_id: yi-large\n",
    )
    assert any(p.startswith("M2") and "键≠model_id" in p for p in _problems(root))


def test_m2_missing_model_id_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(root / "config" / "models.yaml", "    model_id: yi-large\n", "")
    assert any(p.startswith("M2") and "缺 model_id" in p and "yi-large" in p for p in _problems(root))


def test_m2_half_cost_pair_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(root / "config" / "models.yaml", "    cost_per_1k_output: 0.06\n", "")
    assert any(p.startswith("M2") and "成对" in p and "yi-large" in p for p in _problems(root))


def test_m2_nonpositive_cost_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(root / "config" / "models.yaml", "    cost_per_1k_input: 0.02\n", "    cost_per_1k_input: 0\n")
    assert any(p.startswith("M2") and "正数" in p and "yi-large" in p for p in _problems(root))


def test_m2_provider_undefined_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "    provider: stepfun\n    family: yi\n",
        "    provider: madhouse\n    family: yi\n",
    )
    assert any(p.startswith("M2") and "provider 未定义" in p and "madhouse" in p for p in _problems(root))


def test_m2_missing_provider_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "    provider: stepfun\n    family: yi\n",
        "    family: yi\n",
    )
    assert any(p.startswith("M2") and "缺 provider" in p and "yi-large" in p for p in _problems(root))


def test_m2_source_only_provider_note_exempts(tmp_path):
    """gpt-5.4/openai 特例：块内豁免注是唯一放行依据，删注即红。"""
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "    # copilot CLI 自带模型，无对应 ProviderConfig；provider 仅作来源标注。\n",
        "",
    )
    assert any(
        p.startswith("M2") and "gpt-5.4" in p and "openai" in p for p in _problems(root)
    )


# ── M3 来源注释棘轮 ───────────────────────────────────────


def test_m3_new_entry_without_source_fires(tmp_path):
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "  MiniMax-M3:\n",
        "  fake-new-model:\n    model_id: fake-new-model\n    provider: agnes\n"
        "    family: agnes\n    cost_per_1k_input: 0.001\n    cost_per_1k_output: 0.002\n"
        "  MiniMax-M3:\n",
    )
    assert any(p.startswith("M3") and "fake-new-model" in p for p in _problems(root))


def test_m3_sourced_annotation_deleted_fires(tmp_path):
    """删 qwen3.5-plus 的 source 行：实名缺口增长 + 基线棘轮双触发。"""
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "    # source: https://www.alibabacloud.com/help/zh/model-studio/model-pricing (fetched_at 2026-11-09)\n",
        "",
    )
    ps = _problems(root)
    assert any(p.startswith("M3") and "qwen3.5-plus" in p for p in ps)
    assert any(p.startswith("M3") and "棘轮" in p for p in ps)


def test_m3_legacy_gap_resolved_note(tmp_path):
    """yi-large 补来源：判绿，但提示更新钉表（诚实三态：既知缺口可销账）。"""
    root = _sandbox(tmp_path)
    _edit(
        root / "config" / "models.yaml",
        "    cost_per_1k_input: 0.02\n    cost_per_1k_output: 0.06\n",
        "    cost_per_1k_input: 0.02\n    cost_per_1k_output: 0.06\n"
        "    # source: https://example.invalid/yi-large (fetched_at 2026-11-13)\n",
    )
    problems, notes = drift.models_yaml_drift(root)
    assert problems == []
    assert any("yi-large" in n for n in notes)


def test_m3_marker_comment_not_attributed(tmp_path, monkeypatch):
    """反向钉：批 6 段级登记块若被误计入前一条目（step-3.7-flash）的 source
    标注，删钉后它该判红——实际仍红，证明段级/字段级注释分流正确。"""
    root = _sandbox(tmp_path)
    monkeypatch.setattr(drift, "KNOWN_UNSOURCED_LEGACY", ())
    assert any(p.startswith("M3") and "step-3.7-flash" in p for p in _problems(root))


# ── M4 未知键 ─────────────────────────────────────────────


def test_m4_typo_key_fires(tmp_path):
    """单字节变异：cost_per_1k_input → cost_per_1k_inpu（loader 静默丢弃）。"""
    root = _sandbox(tmp_path)
    _edit(root / "config" / "models.yaml", "    cost_per_1k_input: 0.0004\n", "    cost_per_1k_inpu: 0.0004\n")
    assert any(p.startswith("M4") and "cost_per_1k_inpu" in p for p in _problems(root))


def test_m4_field_mirror_matches_schema():
    """M4 的字段镜像必须与 ModelDef.model_fields 同步（schema 增删字段即红）。"""
    from maop.model.schema import ModelDef

    assert set(drift.MODEL_DEF_FIELDS) == set(ModelDef.model_fields.keys())
