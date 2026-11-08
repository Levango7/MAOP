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

import json
import re
from collections.abc import Callable
from typing import Any

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


# ── 原生 usage 出口解析（GAP-2 方案 A 之外的第二条取值链）────────────────
#
# 方案 A（``parse_usage_line``）读的是 agent 主动打印的 ``[USAGE]`` 协议行。
# 那是 MAOP 自定义的约定，只有配合它的 stub / 自研 agent 会输出；真实第三方
# CLI（copilot / codex / claude …）从不输出，因此生产环境该路径恒为 None，
# 成本侧只能退回 CJK 启发式（``tokens_source=estimated``）。本组函数改读
# CLI 自己的机器可读出口，不依赖任何约定。


def _copilot_usage(data: dict[str, Any]) -> dict[str, int] | None:
    """copilot ``--usage-output-file`` 的会话级 ``tokenDetails``。

    结构（v1.0.107 实测）::

        {"tokenDetails": {"input": {"tokenCount": 525},
                          "cache_read": {"tokenCount": 19328},
                          "cache_write": {"tokenCount": 0},
                          "output": {"tokenCount": 5}}}

    **prompt 口径**：copilot 的提示侧消耗 = 未缓存输入 + 缓存读 + 缓存写，
    与其 stderr 的 ``Tokens ↑ 19.9k`` 一致（525 + 19328 = 19853）。只取
    ``input`` 会低估 37 倍——那正是"看着像实测、其实是错的"陷阱。
    """
    details = data.get("tokenDetails")
    if not isinstance(details, dict):
        return None

    def _count(key: str) -> int:
        entry = details.get(key)
        if not isinstance(entry, dict):
            return 0
        value = entry.get("tokenCount")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return max(int(value), 0)

    raw_input = _count("input")
    cache_read = _count("cache_read")
    cache_write = _count("cache_write")
    output = _count("output")
    prompt = raw_input + cache_read + cache_write
    if prompt == 0 and output == 0:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": output,
        "total_tokens": prompt + output,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


_UsageFileParser = Callable[[dict[str, Any]], "dict[str, int] | None"]

_USAGE_FILE_PARSERS: dict[str, _UsageFileParser] = {
    "copilot": _copilot_usage,
}


def parse_usage_file(path: str | None, fmt: str = "") -> dict[str, int] | None:
    """解析 CLI 落盘的 usage JSON（``--usage-output-file`` 等原生出口）。

    ``fmt`` 由 agents.yaml 的 ``usage_format`` 声明，决定用哪个解析器；未声明
    或未注册的格式一律返回 None（调用方保持既有行为）。

    返回键与 :func:`parse_usage_line` 的契约一致，另加 ``cache_read_tokens``
    / ``cache_write_tokens`` 两个扩展键——它们是"token 节省"唯一可验证的
    指标（缓存命中率）；消费方按白名单取三个标准键，扩展键不影响既有聚合。

    任何异常（文件缺失、非 JSON、结构不符）都返回 None：宁可退回启发式，
    也不把 0 冒充实测。
    """
    if not path or not fmt:
        return None
    parser = _USAGE_FILE_PARSERS.get(fmt.strip().lower())
    if parser is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return parser(data)
