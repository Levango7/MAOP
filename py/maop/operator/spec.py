"""MaopTask spec 解析与校验（纯函数，与 CRD schema 对齐）.

对齐对象：``deploy/k8s/operator/crds/maoptask.yaml`` v1alpha1 ——
required ``[agent, prompt]``、``maxTurns >= 1``（默认 20）、``tools`` 数组、
``tenant`` 可选。CRD 后续改动必须同步本模块与
``tests/test_operator_m0.py::test_spec_matches_crd_schema``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 与 CRD 对齐的默认 maxTurns（crds/maoptask.yaml 的 default）。
DEFAULT_MAX_TURNS = 20

#: CRD 的 required 字段。
REQUIRED_FIELDS = ("agent", "prompt")


class InvalidSpecError(ValueError):
    """MaopTask spec 缺必填字段或字段值不合法。"""


@dataclass(frozen=True)
class MaopTaskSpec:
    """已校验的 MaopTask 执行规格。"""

    name: str
    namespace: str
    agent: str
    prompt: str
    tenant: str = ""
    max_turns: int = DEFAULT_MAX_TURNS
    tools: tuple[str, ...] = field(default=())


def _require_str(spec: dict[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidSpecError(f"spec.{key} is required and must be a non-empty string")
    return value.strip()


def _positive_int(spec: dict[str, Any], key: str, default: int) -> int:
    value = spec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidSpecError(f"spec.{key} must be an integer >= 1, got {value!r}")
    return int(value)


def _str_list(spec: dict[str, Any], key: str) -> tuple[str, ...]:
    value = spec.get(key) or []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise InvalidSpecError(f"spec.{key} must be a list of strings")
    return tuple(value)


def parse_task_spec(
    body: dict[str, Any], *, name: str = "", namespace: str = "default"
) -> MaopTaskSpec:
    """把 CR body（``dict``）解析为已校验的 :class:`MaopTaskSpec`.

    CRD 的 additionalProperties 行为交给 k8s，这里只取已知键；未知键忽略
    （向前兼容小版本）。
    """
    if not isinstance(body, dict):
        raise InvalidSpecError("CR body must be a mapping")
    spec = body.get("spec")
    if not isinstance(spec, dict):
        raise InvalidSpecError("spec is required and must be a mapping")
    metadata = body.get("metadata") or {}
    return MaopTaskSpec(
        name=str(name or metadata.get("name") or ""),
        namespace=str(namespace or (metadata.get("namespace") or "default")),
        agent=_require_str(spec, "agent"),
        prompt=_require_str(spec, "prompt"),
        tenant=str(spec.get("tenant") or ""),
        max_turns=_positive_int(spec, "maxTurns", DEFAULT_MAX_TURNS),
        tools=_str_list(spec, "tools"),
    )


def command_for(
    spec: MaopTaskSpec, *, pin_agent: bool = False, apply_turn_budget: bool = False
) -> list[str]:
    """构造执行命令.

    M0 形状：``maop run <prompt>``；M1 起 ``pin_agent=True`` 插入
    ``--agent <spec.agent>`` —— 由 controller 依据 MaopAgent CR 解析结果决定
    （CR 存在即 pin）。``maop run --agent`` 把该名字落为 MaopLoop 的
    forced_agent（F6a），使 ``spec.agent`` 真正参与执行路由。

    M2：``apply_turn_budget=True`` 插入 ``--max-turns <spec.max_turns>`` ——
    任务级 turn 上限（一个 turn = 一次 dispatch 尝试）；operator 总是透传。
    实际消耗经 ``[USAGE] turns=N`` 行回写 ``status.turns``。

    诚实边界：``tenant`` 仍只校验与透传记录；tokensUsed/配额是后续里程碑。
    """
    command = ["maop", "run"]
    if pin_agent:
        command += ["--agent", spec.agent]
    if apply_turn_budget:
        command += ["--max-turns", str(spec.max_turns)]
    command.append(spec.prompt)
    return command
