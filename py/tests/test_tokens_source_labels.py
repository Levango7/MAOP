"""tokens_source 必须区分 provider 实测与调用方估算。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from maop.core.agent.billing.billing_abstraction import BillingEngine
from maop.core.agent.llm_chat import llm_factory
from maop.core.agent.llm_chat.llm_models import LLMResponse
from maop.core.agent.llm_chat.react_loop import _usage_from_response
from maop.core.agent.registry.agent_catalog import BillingModel


class AsyncSpyTracker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_async(self, **kwargs) -> None:
        self.calls.append(kwargs)


class SyncSpyTracker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _record_provider_response(monkeypatch, resp: LLMResponse, tracker) -> None:
    monkeypatch.setattr("maop.core.cost_tracker.get_cost_tracker", lambda: tracker)
    asyncio.run(llm_factory._record_cost(resp, {"session_id": "s1", "agent": "a1"}))


def _billing_engine(tmp_path, tracker) -> BillingEngine:
    catalog = SimpleNamespace(
        get=lambda name: SimpleNamespace(
            billing_model=BillingModel.FREE,
            billing_config={},
        )
    )
    return BillingEngine(
        catalog=catalog,
        quota_bucket=SimpleNamespace(),
        cost_tracker=tracker,
        db_path=tmp_path / "billing.db",
    )


@pytest.mark.parametrize("async_tracker", [True, False], ids=["async", "sync-fallback"])
def test_provider_usage_is_measured(monkeypatch, async_tracker):
    tracker = AsyncSpyTracker() if async_tracker else SyncSpyTracker()
    resp = LLMResponse(
        content="ok",
        model="m1",
        provider="openai",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    _record_provider_response(monkeypatch, resp, tracker)

    assert len(tracker.calls) == 1
    assert tracker.calls[0]["metadata"] == {
        "provider": "openai",
        "tokens_source": "measured",
    }


@pytest.mark.parametrize(
    "resp",
    [
        LLMResponse(
            content="[LLM Error] boom",
            model="m1",
            provider="openai",
            error="boom",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        LLMResponse(content="", model="m1", provider="openai", total_tokens=15),
        LLMResponse(content="ok", model="m1", provider="openai"),
        LLMResponse(content="ok", model="m1", provider="openai", total_tokens=-1),
    ],
    ids=["structured-error", "empty-error-response", "missing-usage", "negative-usage"],
)
def test_provider_response_without_trustworthy_usage_is_not_measured(monkeypatch, resp):
    tracker = AsyncSpyTracker()

    _record_provider_response(monkeypatch, resp, tracker)

    assert len(tracker.calls) == 1
    assert tracker.calls[0]["metadata"] == {"provider": "openai"}


def test_provider_response_without_provider_or_usage_has_no_metadata(monkeypatch):
    tracker = AsyncSpyTracker()

    _record_provider_response(monkeypatch, LLMResponse(content="ok", model="m1"), tracker)

    assert len(tracker.calls) == 1
    assert tracker.calls[0]["metadata"] is None


def test_billing_caller_tokens_are_estimated(tmp_path):
    tracker = SyncSpyTracker()
    engine = _billing_engine(tmp_path, tracker)

    result = engine.charge("a1", tokens=10, calls=1, session_id="s1", model="m1")

    assert result.success
    assert len(tracker.calls) == 1
    assert tracker.calls[0]["metadata"] == {"tokens_source": "estimated"}


def test_billing_without_tokens_has_no_source_label(tmp_path):
    tracker = SyncSpyTracker()
    engine = _billing_engine(tmp_path, tracker)

    result = engine.charge("a1", calls=1, session_id="s1", model="m1")

    assert result.success
    assert len(tracker.calls) == 1
    assert tracker.calls[0]["metadata"] is None


# ── 回填口径（react_loop → MaopResult.usage）必须与入账口径同判据 ──


@pytest.mark.parametrize(
    "resp",
    [
        LLMResponse(
            content="[LLM Error] boom",
            model="m1",
            provider="openai",
            error="boom",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        LLMResponse(content="", model="m1", provider="openai", total_tokens=15),
        LLMResponse(content="ok", model="m1", provider="openai"),
        LLMResponse(content="ok", model="m1", provider="openai", total_tokens=-1),
    ],
    ids=["structured-error", "empty-error-response", "missing-usage", "negative-usage"],
)
def test_react_loop_backfill_ignores_untrustworthy_usage(resp):
    """与 llm_factory._usage_metadata 同一判据：不诚实响应不回填 usage。"""
    assert llm_factory.has_trustworthy_usage(resp) is False
    assert _usage_from_response(resp) is None


def test_react_loop_backfill_keeps_trustworthy_usage():
    resp = LLMResponse(
        content="ok",
        model="m1",
        provider="openai",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    assert llm_factory.has_trustworthy_usage(resp) is True
    assert _usage_from_response(resp) == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
