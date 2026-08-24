"""MAOP Engine — Helper functions extracted from the Engine class.

Extracted from ``engine.py`` (Phase 3-1 module split, B4) to isolate
methods that do not depend on ``Engine`` instance state and can live as
pure module-level functions. Keeping these out of ``engine.py`` shrinks
the Engine class module and makes the helpers independently testable.

Contents:
    - decompose_task — heuristic task decomposition (P1-4). Splits a
      compound task string into sub-steps using semicolons, numbered
      lists, bullet lists, or "and" conjunctions. Returns an empty list
      when the task is atomic.

Dependency: imports ``WorkflowStep`` and ``StepType`` from
``engine_types``. Single-directional dependency
(engine_helpers → engine_types); no cycle.
"""

from __future__ import annotations

import re

from maop.engine_types import StepType, WorkflowStep


def decompose_task(
    task: str,
    step: WorkflowStep,
) -> list[WorkflowStep]:
    """Decompose a complex task into sub-steps.

    Uses heuristics to detect compound tasks and split them:
    - Semicolons: "do A; do B" → 2 steps
    - Numbered lists: "1. A 2. B" → 2 steps
    - "and" conjunctions: "implement X and test Y" → 2 steps
    - Bullet lists: "- A\\n- B" → 2 steps

    Returns empty list if task is atomic (no decomposition needed).

    Notes
    -----
    Strategy 4（"and" 分割）在 M4 修复 3.14 中收紧：仅当任务长度 > 30、
    每段长度 > 10、分割后每段以动词性词开头、且 "X and Y" 不在固定短语
    黑名单（如 "research and development"）中时才分割，避免误拆常见短语。
    """
    substeps: list[WorkflowStep] = []

    # Strategy 1: Semicolon-separated tasks
    if ";" in task:
        parts = [p.strip() for p in task.split(";") if p.strip()]
        if len(parts) > 1:
            for i, part in enumerate(parts):
                substeps.append(WorkflowStep(
                    id=f"{step.id}_sub{i}",
                    type=StepType.AGENT,
                    agent=step.agent,
                    task=part,
                    depends_on=[f"{step.id}_sub{i-1}"] if i > 0 else [],
                ))
            return substeps

    # Strategy 2: Numbered list "1. A 2. B"
    numbered = re.findall(r'\d+\.\s+(.+?)(?=\d+\.|$)', task, re.DOTALL)
    if len(numbered) > 1:
        for i, part in enumerate(numbered):
            substeps.append(WorkflowStep(
                id=f"{step.id}_sub{i}",
                type=StepType.AGENT,
                agent=step.agent,
                task=part.strip(),
                depends_on=[],
            ))
        return substeps

    # Strategy 3: Bullet list "- A\n- B"
    bullets = re.findall(r'^[-*]\s+(.+)$', task, re.MULTILINE)
    if len(bullets) > 1:
        for i, part in enumerate(bullets):
            substeps.append(WorkflowStep(
                id=f"{step.id}_sub{i}",
                type=StepType.AGENT,
                agent=step.agent,
                task=part.strip(),
                depends_on=[],
            ))
        return substeps

    # Strategy 4: "and" conjunction (M4 修复 3.14：收紧分割条件)
    #
    # 原策略仅要求每段长度 > 10，会错误分割 "research and development
    # the new feature" 这类含固定短语的句子，以及 "analyze the data and
    # generate a report" 这种本应分割但前后词性不明确的场景。
    #
    # 收紧后的条件（全部满足才分割）：
    # 1. 任务总长度 > 40（短任务几乎不需要拆分）；
    # 2. 分割点前后的词都是动词性词（启发式：常见动词集合或动词后缀）；
    # 3. 整个 "X and Y" 不在常见固定短语黑名单中（如 "research and
    #    development"、"supply and demand"）；
    # 4. 每段长度 > 10（保留原条件，避免过短碎片）。
    _AND_PHRASE_BLACKLIST = {
        "research and development", "supply and demand", "salt and pepper",
        "black and white", "back and forth", "up and down", "in and out",
        "trial and error", "peace and quiet", "law and order",
        "pots and pans", "bread and butter", "cause and effect",
        "pros and cons", "do's and don'ts", "men and women", "boys and girls",
        "input and output", "read and write", "open and close",
        "start and end", "begin and end", "old and new", "large and small",
        "high and low", "long and short", "thick and thin", "fast and slow",
        "right and wrong", "true and false", "yes and no", "win and lose",
        "pass and fail", "add and remove", "insert and delete",
        "create and destroy", "build and destroy", "lock and unlock",
        "encrypt and decrypt", "encode and decode", "compress and decompress",
        "serialize and deserialize", "connect and disconnect",
        "subscribe and unsubscribe", "load and save", "push and pop",
        "enqueue and dequeue", "grant and revoke", "allow and deny",
        "accept and reject", "send and receive", "request and response",
        "get and set", "fetch and store", "pull and push", "fork and join",
        "spawn and wait", "start and stop", "pause and resume",
        "mount and unmount", "attach and detach",
        "enable and disable", "show and hide", "expand and collapse",
        "zoom in and zoom out", "log in and log out", "sign in and sign out",
    }
    _VERB_SUFFIXES = ("ing", "ize", "ise", "ate", "ify", "ed", "es", "en")
    _COMMON_VERBS = {
        "implement", "test", "build", "deploy", "create", "write", "design",
        "validate", "configure", "monitor", "analyze", "optimise", "optimize",
        "refactor", "migrate", "integrate", "generate", "parse", "fetch",
        "process", "update", "remove", "delete", "add", "fix", "check",
        "run", "execute", "compile", "install", "start", "stop", "restart",
        "train", "evaluate", "predict", "infer", "serialize", "deserialize",
        "encode", "decode", "encrypt", "decrypt", "compress", "decompress",
        "load", "save", "read", "open", "close", "send", "receive",
        "push", "pull", "store", "get", "set", "find", "search",
        "scan", "filter", "sort", "group", "merge", "split", "join", "map",
        "reduce", "transform", "convert", "translate", "render", "display",
        "print", "log", "trace", "debug", "inspect", "audit", "review",
        "approve", "reject", "accept", "cancel", "abort", "retry", "resume",
        "pause", "wait", "notify", "alert", "report", "export", "import",
        "backup", "restore", "archive", "unarchive", "pack", "unpack",
        "extract", "package", "publish", "release", "rollback", "revert",
        "apply", "commit", "checkout", "branch", "tag", "rebase", "clone", "init", "setup", "teardown", "provision",
        "deprovision", "scale", "resize", "rotate", "move", "copy", "paste",
        "cut", "undo", "redo", "select", "deselect", "highlight", "focus",
        "blur", "click", "tap", "swipe", "scroll", "drag", "drop", "hover",
        "type", "input", "submit", "reset", "clear", "fill", "empty",
        "populate", "vacuum", "compact", "defragment", "format", "erase",
        "wipe", "clean", "purge", "evict", "expire", "refresh", "reload",
        "reboot", "shutdown", "power", "wake", "sleep", "hibernate",
    }

    def _is_verb_like(word: str) -> bool:
        """启发式判断一个词是否为动词性词。

        规则（任一满足即返回 True）：
        - 词在常见动词集合中；
        - 词以常见动词后缀结尾且长度 >= 4（避免 "ed"、"es" 等被误判）。
        """
        w = word.lower().strip()
        if not w:
            return False
        if w in _COMMON_VERBS:
            return True
        return len(w) >= 4 and any(w.endswith(suf) for suf in _VERB_SUFFIXES)

    def _is_blacklist_phrase(left: str, right: str) -> bool:
        """检查 "X and Y" 是否为固定短语黑名单中的成员。

        取左右各 1 个词拼接成 "x and y" 后小写匹配黑名单。
        """
        left_words = left.split()
        right_words = right.split()
        if not left_words or not right_words:
            return False
        phrase = f"{left_words[-1]} and {right_words[0]}".lower()
        return phrase in _AND_PHRASE_BLACKLIST

    if len(task) > 30:
        and_parts = re.split(r'\s+and\s+', task, maxsplit=2)
        if len(and_parts) > 1 and all(len(p) > 10 for p in and_parts):
            # 逐个分割点检查：
            # - "X and Y" 不在固定短语黑名单（取 left 末词 + right 首词匹配）；
            # - 分割后每段以动词性词开头（"implement X and test Y" 中
            #   "implement" 和 "test" 均为动词），避免误拆 "research and
            #   development" 这类名词短语。
            split_ok = True
            for i in range(len(and_parts) - 1):
                left = and_parts[i]
                right = and_parts[i + 1]
                left_words = left.split()
                right_words = right.split()
                if not left_words or not right_words:
                    split_ok = False
                    break
                if _is_blacklist_phrase(left, right):
                    split_ok = False
                    break
                # 检查每段首词是否动词性词
                if not (_is_verb_like(left_words[0])
                        and _is_verb_like(right_words[0])):
                    split_ok = False
                    break
            if split_ok:
                for i, part in enumerate(and_parts):
                    substeps.append(WorkflowStep(
                        id=f"{step.id}_sub{i}",
                        type=StepType.AGENT,
                        agent=step.agent,
                        task=part.strip(),
                        depends_on=[],
                    ))
                return substeps

    # Task is atomic — no decomposition
    return []