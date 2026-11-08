"""agents.yaml 的 usage 声明必须自洽 —— 三种漂移都是**静默**失效。

``prepare_argv`` 只对其中一种发 warning：

* 有 ``{usage_file}`` 没 ``usage_format`` → 一条 warning，占位符原样传给 CLI
  （CLI 会去写一个名字就叫 ``{usage_file}`` 的文件）；
* 有 ``usage_format`` 没 ``{usage_file}`` → **连 warning 都没有**，
  ``resolve_usage`` 拿到的 ``usage_file`` 是空串，直接走协议行 / 启发式；
* 格式名拼错 → ``parse_usage_file`` 对未注册格式返回 ``None``，同样静默。

还有一条不是"声明不全"而是"顺序"：Windows 上 npm 的 ``.CMD`` shim 用 ``%*``
传参，cmd.exe 在换行处重新分词，**排在 prompt 后面的参数会被整段丢弃**。
实测后果不是少一个 flag，而是 ``--allow-all-tools`` 与 ``--usage-output-file``
一起消失、prompt 本身被截断，而 rc 仍是 0、输出看起来正常。

这三条都改过 ``config/agents.yaml`` 才会触发，没有编译期信号，所以放在门禁里。
"""

from __future__ import annotations

import json
from pathlib import Path

from maop.config.loader import load_config
from maop.core.agent.usage_protocol import parse_usage_file

REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``drivers`` 认的 task 占位符（与 ``cli_invocation._TASK_SENTINELS`` 同口径）。
TASK_SENTINELS = ("'{task}'", "{task}", "{{safePrompt}}")


def _agents():
    cfg = load_config(REPO_ROOT)
    assert cfg.agents, "agents.yaml 没加载出任何 agent —— 判据失去被测对象"
    return cfg.agents


def test_usage_declarations_are_complete():
    """``{usage_file}`` 与 ``usage_format`` 必须同时出现或同时缺席。"""
    broken = [
        (name, "{usage_file}" in (d.cli_args or ""), bool((d.usage_format or "").strip()))
        for name, d in _agents().items()
        if ("{usage_file}" in (d.cli_args or "")) != bool((d.usage_format or "").strip())
    ]
    assert broken == [], f"usage 声明不完整 (name, 有占位符, 有格式): {broken}"


def test_declared_usage_format_actually_parses(tmp_path: Path):
    """声明的格式名必须真的有解析器 —— 拼错等于没写。

    用行为判据而不是查内部字典：给一份合法的 ``tokenDetails``，能解析出来才算数。
    """
    payload = {"tokenDetails": {"input": {"tokenCount": 10}, "output": {"tokenCount": 5}}}
    path = tmp_path / "usage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    declared = {n: d.usage_format for n, d in _agents().items() if d.usage_format}
    assert declared, "没有任何 agent 声明 usage_format —— 判据失去被测对象"

    broken = []
    for name, fmt in declared.items():
        parsed = parse_usage_file(str(path), fmt)
        if not parsed or parsed.get("prompt_tokens") != 10 or parsed.get("completion_tokens") != 5:
            broken.append((name, fmt, parsed))
    assert broken == [], f"usage_format 没有可用解析器 (agent, format, 实际解析结果): {broken}"


def test_usage_file_placeholder_precedes_the_task_placeholder():
    """``{usage_file}`` 必须排在 task 占位符之前（cmd.exe 的换行分词）。"""
    checked = 0
    for name, d in _agents().items():
        args = d.cli_args or ""
        if "{usage_file}" not in args:
            continue
        positions = [args.index(s) for s in TASK_SENTINELS if s in args]
        if not positions:
            continue
        checked += 1
        assert args.index("{usage_file}") < min(positions), (
            f"{name}: {{usage_file}} 必须排在 task 占位符之前，否则 Windows 上 "
            f"cmd.exe 会在 prompt 的换行处截断后续参数（rc 仍为 0，静默失效）：{args!r}"
        )
    assert checked, "没有任何 agent 同时使用 {usage_file} 与 task 占位符 —— 判据空转"
