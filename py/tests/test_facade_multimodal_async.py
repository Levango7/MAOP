"""M2.2 B2 余项收口：MemoryFacade 层异步摄入入口（``store_multimodal_async``）.

PR-3（进程内队列）与 #160（crossprocess 进程间接线）交付的异步能力此前只在
``MultimodalMemory`` 服务层可达——「Facade 侧按需再接」是章程登记的最后一个
尾巴。本组测试钉住 Facade 层入口与既有语义一致：submit 不等嵌入、drain 后
可检索、排空幂等。
"""

from __future__ import annotations

import pytest

from maop.memory.facade import MemoryFacade


@pytest.mark.parametrize("mode", ["agent", "chat"])
def test_facade_store_async_then_drain_makes_entry_searchable(
    tmp_path, mode
) -> None:
    """submit 立即返回 ID；drain 排空后条目进入检索空间。"""
    facade = MemoryFacade(tmp_path / f"root-{mode}", mode=mode)
    entry_id = facade.store_multimodal_async(
        "async runbook for the billing service",
        entry_id="mm-async-1",
    )
    assert entry_id == "mm-async-1"
    # submit 不等嵌入：此刻 stats 还未计入（批处理在后台/未 flush）
    flushed = facade.drain_multimodal_ingest()
    assert flushed >= 1
    hits = facade.retrieve_multimodal("async runbook billing", top=5)
    assert hits and hits[0]["id"] == "mm-async-1"


def test_facade_drain_is_idempotent(tmp_path) -> None:
    facade = MemoryFacade(tmp_path / "root", mode="agent")
    facade.store_multimodal_async("second entry", entry_id="mm-async-2")
    first = facade.drain_multimodal_ingest()
    second = facade.drain_multimodal_ingest()
    assert first >= 1
    assert second == 0
