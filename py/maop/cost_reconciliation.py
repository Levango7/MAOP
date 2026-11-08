"""执行后的成本对账 —— 从 ``maop_loop_phases`` 拆出的独立 mixin。

2026-11-08 拆出：``maop_loop_phases.py`` 已卡在 800 行规模门禁线上
（``py/scripts/check_file_size.py``；``ALLOWED_OVERSIZE`` 已归零，既定方向是
不加白名单），任何新增都会判红。``_budget_reconciliation`` 与 phase 编排无
耦合，只依赖宿主提供的 ``_root``，因此独立成 mixin。

职责：把一次执行的 token 用量写成 ``cost_entries`` 行 + ``run.cost`` 事件，
并标注来源（measured / estimated / mixed）与缓存明细（cache_read / cache_write）。
"""

from __future__ import annotations

import logging
from typing import Any

from maop.core.run_events import record_run_event

logger = logging.getLogger(__name__)


class CostReconciliationMixin:
    """成本对账 mixin —— 宿主必须提供 ``self._root``（项目根目录）。"""

    _root: str

    def _budget_reconciliation(self, exec_result: Any, trace_id: str) -> None:
        """Record actual cost after execution (best-effort).

        P2-1 成本双写统一: writes to ``CostTracker`` (SQLite ``maop.db``)
        directly instead of the legacy JSON ``budget_ledger.json`` via
        ``model.budget.BudgetGuard``, eliminating the dual-write split.
        """
        if not exec_result:
            return
        # GAP-5 硬化（2026-11-05，M-SIM M1 Temporal 腿）：此前 model 为空即
        # 整条放弃——即便实测 usage 在手也静默缺行（run_events 无 run.cost、
        # 成本表缺行且无任何线索）。改为：有实测 usage 就记账（model 留空时
        # 与启发式路径的 ``model_name or ""`` 同形归档）；两者皆缺才返回。
        _usage = getattr(exec_result, "usage", None)
        _has_usage = isinstance(_usage, dict) and bool(_usage)
        if not exec_result.model and not _has_usage:
            return
        try:
            from maop.core.cost_tracker import CostTracker
            from maop.model.registry import ModelRegistry

            model_name = exec_result.model or ""
            usage = getattr(exec_result, "usage", None) or {}
            if isinstance(usage, dict):
                est_tokens_in = usage.get("prompt_tokens", 0)
                est_tokens_out = usage.get("completion_tokens", 0)
                # 缓存明细只在 CLI 原生 usage 出口提供（见 usage_protocol.
                # parse_usage_file）；协议行与启发式路径没有该键，取 0 即可。
                cache_read = int(usage.get("cache_read_tokens", 0) or 0)
                cache_write = int(usage.get("cache_write_tokens", 0) or 0)
            else:
                est_tokens_in = 0
                est_tokens_out = 0
                cache_read = 0
                cache_write = 0
            # 来源标记（2026-11-08）：实测与启发式估算必须可区分。此前两种
            # 数值都写进 cost_entries 的 prompt_tokens/completion_tokens，
            # 台账上无法判断真伪；同一次运行控制台打
            # `[USAGE] tokens=0 source=estimated`、台账却写 5/31，看上去是
            # 自相矛盾（其实前者是「实测口径为 0」，后者是「估算值」）。
            measured_in = bool(est_tokens_in)
            measured_out = bool(est_tokens_out)
            if not est_tokens_in:
                logger.warning("[maop_loop] No prompt_tokens reported by provider — using heuristic estimate, cost accuracy is UNKNOWN")
                cjk = sum(1 for ch in exec_result.task if '\u4e00' <= ch <= '\u9fff')
                est_tokens_in = cjk * 2 + (len(exec_result.task) - cjk) // 4
            if not est_tokens_out:
                logger.warning("[maop_loop] No completion_tokens reported by provider — using heuristic estimate, cost accuracy is UNKNOWN")
                cjk = sum(1 for ch in exec_result.stdout if '\u4e00' <= ch <= '\u9fff')
                est_tokens_out = cjk * 2 + (len(exec_result.stdout) - cjk) // 4
            if measured_in and measured_out:
                tokens_source = "measured"
            elif measured_in or measured_out:
                tokens_source = "mixed"
            else:
                tokens_source = "estimated"

            registry = ModelRegistry(project_root=self._root)
            model_def = registry.get_model(model_name)
            provider = model_def.provider if model_def else ""
            estimated_cost = 0.0
            if model_def:
                estimated_cost = (
                    model_def.cost_per_1k_input * est_tokens_in / 1000
                    + model_def.cost_per_1k_output * est_tokens_out / 1000
                )

            tracker = CostTracker(root_dir=self._root)
            tracker.record_actual_cost(
                trace_id=trace_id, model=model_name, provider=provider,
                actual_tokens_in=est_tokens_in, actual_tokens_out=est_tokens_out,
                estimated_cost=estimated_cost,
                # M-X3 归属：成本条目带上 agent 与执行阶段，供三维成本归因聚合；
                # 此前这两维在账面上是空的（聚合侧只能记 unattributed）。
                agent=exec_result.agent or "", phase="execute",
                tokens_source=tokens_source,
                # priced 判据见下方 run.cost 事件（models.yaml 未收录 ⇒ False）。
                priced=bool(model_def),
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )
            record_run_event(
                trace_id, "run.cost", agent=exec_result.agent or "",
                payload={
                    "model": model_name,
                    "provider": provider,
                    "tokens_in": est_tokens_in,
                    "tokens_out": est_tokens_out,
                    # 金额是否可信：models.yaml 未收录该模型时 priced=False，
                    # cost_usd 只能是 0.0，不能当成"这次真的免费"。
                    "priced": bool(model_def),
                    "tokens_source": tokens_source,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "estimated_cost": round(estimated_cost, 6),
                },
            )
        except Exception as exc:
            logger.warning("[MAOP-loop] Budget reconciliation failed: %s", exc)
