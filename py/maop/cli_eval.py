"""``maop eval`` — quality self-checks for the multimodal space (M2.2 B4).

Split out of ``cli.py`` so that file stays under the 800-line gate: the body is
a report printer, not argument plumbing. ``cmd_eval`` is the production entry
point for :mod:`maop.core.memory.retrieval_eval` (which is also why that
module is not an orphan for the dead-wiring gate).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


def cmd_eval(args: list[str]) -> Any:
    """``maop eval multimodal`` — 跨模态检索质量自检（M2.2 B4）。

    用内置的标注语料（12 张确定性合成图 + 真 CLIP 冻结夹具）对**当前进程解析出的
    provider** 跑 NDCG@k。回答的是"我这个部署的多模态记忆是真的在语义检索吗"：
    hash 回退会拿到接近随机的分数，真 CLIP 显著更高。

    Subcommands
        multimodal [--k 5] [--provider auto|clip|hash]
    """
    if not args or args[0] != "multimodal":
        print("usage: maop eval multimodal [--k 5] [--provider auto|clip|hash]")
        return None
    from maop.core.memory.retrieval_eval import (
        DEFAULT_K,
        evaluate_retrieval,
        format_report_markdown,
        load_dataset,
    )

    k = DEFAULT_K
    provider = "auto"
    rest = args[1:]
    for index, token in enumerate(rest):
        if token == "--k" and index + 1 < len(rest):
            k = int(rest[index + 1])
        elif token == "--provider" and index + 1 < len(rest):
            provider = rest[index + 1]

    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "clip_eval.json"
    dataset = load_dataset(fixture)
    from maop.core.memory.multimodal_embed import resolve_multimodal_provider
    from maop.core.memory.multimodal_store import MultimodalStore

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "multimodal.db"

        def _make_isolated():
            return MultimodalStore(
                db_path=db_path,
                embedding=resolve_multimodal_provider(force=provider or None),
            )

        report = evaluate_retrieval(_make_isolated, dataset, k=k)
    print(f"# multimodal retrieval quality — provider={report.provider_id} "
          f"semantic={report.is_semantic} model={dataset.model_id}")
    print(format_report_markdown([report]))
    print()
    for q in report.queries:
        print(f"  {q.ndcg:5.3f}  [{q.kind:10s}] {q.text}")
    if not report.is_semantic:
        print()
        print("NOTE: this deployment is on the NON-semantic hash fallback — the "
              "score above is near-random by design. Install the multimodal "
              "extra (pip install maop-orchestrator[multimodal]) for real "
              "cross-modal retrieval.")
    return report


