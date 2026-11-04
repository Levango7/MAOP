"""``[USAGE]`` 协议行解析 —— CLI agent 自报用量的权威读取点（GAP-2 方案 A）。

背景（M-SIM 2026-11-04，``docs/audits/business-sim-evidence-2026-11-04.md``）：
CLI agent 路径存在双层计量口径分歧——loop 层成本对账
（``maop_loop_phases._budget_reconciliation``）在 ``MaopResult.usage`` 缺席时
退回 CJK 文本启发式，而 agent 经 stdout 自报的 ``[USAGE]`` 协议行被整体丢弃，
同一批调用出现两个互不一致的 token 总数（M-SIM 实测 3185 vs 6924）。

本模块定义协议行 schema（新字段追加在 ``source=`` 之后——既有解析器
``operator/controller.py``、``run_sim.py`` 的正则均非行尾锚定，向后兼容）::

    [USAGE] turns=N [tokens=T] [source=S] [prompt=P] [completion=C]

计费取值链（权威口径，GAP-2 方案 A）：provider 实测（LLM 直连路径回填
``MaopResult.usage``）→ 协议行（CLI agent 自报，本模块产出）→ CJK 启发式
（``_budget_reconciliation`` 既有兜底，仅在 usage 缺席时触发）。
不打印协议行的老 agent 行为逐字节不变（usage=None）。

语义约定：prompt+completion 同时在位时 total 取二者之和（拆分是更细的契约，
优先于粗粒度 ``tokens=``）；仅有 ``tokens=`` 时只申报 total——in/out 拆分
未知，凭空拆分是不诚实的计费，成本侧继续走启发式并打 accuracy UNKNOWN 警告。
范围注记：接线面 = ``delegate/drivers`` 的 ``_run_cli``（2 处成功路径）与
``_run_wrapper``（4 处：JSON 封装路径从 JSON 的 ``stdout`` 字段解析、raw
回退路径从原文解析——协议行内嵌 JSON 时同样被读出）。
"""

import re

_USAGE_RE = re.compile(
    r"\[USAGE\]\s*turns=(\d+)"
    r"(?:\s+tokens=(\d+))?"
    r"(?:\s+source=(\w+))?"
    r"(?:\s+prompt=(\d+))?"
    r"(?:\s+completion=(\d+))?"
)


def parse_usage_line(stdout: str | None) -> dict[str, int] | None:
    """从 agent stdout 提取最后一条 ``[USAGE]`` 协议行，产出 usage dict。

    键名与 ``MaopResult.usage`` 合同一致（M3.1）：
    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``。

    返回 ``None`` 表示无可信申报（调用方保持 ``usage=None`` 原语义，成本侧
    自然落到启发式）。多行输出取最后一条（agent 可能多轮打印，最后一条是
    最终权威；与 run_sim harness 的 matches[-1] 口径一致）。
    """
    if not stdout:
        return None
    matches = _USAGE_RE.findall(stdout)
    if not matches:
        return None
    _turns, tokens, _source, prompt, completion = matches[-1]
    usage: dict[str, int] = {}
    if prompt or completion:
        p, c = int(prompt or 0), int(completion or 0)
        usage["prompt_tokens"] = p
        usage["completion_tokens"] = c
        usage["total_tokens"] = p + c
    elif tokens:
        usage["total_tokens"] = int(tokens)
    return usage or None
