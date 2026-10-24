"""Agent 推荐 service —— 把已有的评分能力暴露给 Dashboard。

为什么单独成文件：``agent_service.py`` 已 836 行（超出 ADR-023 的 800 行门禁，
靠 allowlist 放行），不应再往里追加。本模块只做"推荐"这一件事。

背景（2026-10-24 产品全景审查）：``UsageStatistics.recommend_agent`` 早就实现了
完整的加权评分——成功率 40% + 成本 30% + 速度 20% + 能力匹配 10%，并支持按
``task_type`` / ``capabilities`` / ``prefer_free`` 过滤——**但它没有任何 API 暴露**。
``agent-catalog`` 的 5 个端点只有 list / get / register / search / stats。

结果是：用户面对 ``agents.yaml`` 里 23 个开箱适配器时，没有任何依据选择第一个
该用哪个。能力实现远好于能力暴露，这是本模块要补的那一格。

评分为空时（没有任何历史调用记录）返回空列表而不是猜测——**没有数据就不推荐**，
否则等于凭空的"推荐"误导用户。
"""

from __future__ import annotations

from typing import Any

__all__ = ["recommend_agents"]


def recommend_agents(
    task_type: str = "",
    capabilities: list[str] | None = None,
    prefer_free: bool = True,
    limit: int = 10,
) -> dict[str, Any]:
    """按历史使用统计推荐 Agent，按综合评分降序。

    Parameters
    ----------
    task_type : str
        任务类型标签，用于过滤有相关经验的 Agent（空则不过滤）。
    capabilities : list[str] | None
        要求的能力列表（如 ``["code_generation"]``），空则不考量。
    prefer_free : bool
        是否优先免费 / 包月计费模式的 Agent，默认 True。
    limit : int
        最多返回多少条，默认 10（评分已降序，取头部即最优）。

    Returns
    -------
    dict
        ``{"agents": [{"agent_name", "score", "reason", "estimated_cost"}...],
        "count": int, "scored": bool}``。

        ``scored=False`` 表示**当前没有任何历史统计数据**（例如全新部署），
        此时 ``agents`` 为空 —— 调用方应据此提示"先跑几次任务"，而不是
        展示一个空的推荐列表让用户以为出错了。
    """
    from maop.core.agent.ops.usage_statistics import UsageStatistics

    # 复用 agent_service 的 catalog 访问方式（同包内部协作，避免重复实现）。
    from maop.dashboard.services.agent_service import _get_catalog

    stats = UsageStatistics(catalog=_get_catalog())
    recs = stats.recommend_agent(
        task_type=task_type,
        capabilities=capabilities or [],
        prefer_free=prefer_free,
    )

    return {
        "agents": [
            {
                "agent_name": r.agent_name,
                "score": r.score,
                "reason": r.reason,
                "estimated_cost": r.estimated_cost,
            }
            for r in recs[:limit]
        ],
        "count": len(recs[:limit]),
        "scored": bool(recs),
    }
