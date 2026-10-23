"""Response models for ``/api/blackboard/*`` — the 7 endpoints the dashboard calls.

Every producer here is a plain dict built by ``Blackboard``
(``maop/core/reliability/blackboard.py``): ``BlackboardEntry.to_dict()`` for
entries, ``_record_history()`` for history rows, and ``blackboard_service``
for the aggregates. The field lists below are those producers verbatim —
``entry_id`` in particular, which the Vue view was reading as ``id``.

Unlike most routers in this package, ``blackboard.py`` passes no
``error_value=`` to ``@handle_api_errors``, so failures come back as a
``JSONResponse`` (``error_handler.error_response``) and bypass serialization
altogether. That means these envelopes only ever see the success shape, and
``status`` has no default: an envelope without an explicit ``"ok"`` is a bug,
and it should fail loudly instead of being labelled a success.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BlackboardEntryOut(BaseModel):
    """``BlackboardEntry.to_dict()`` — 7 keys, no ``id``."""

    entry_id: str = ""
    domain: str = ""
    content: Any = None
    contributor: str = ""
    timestamp: str = ""
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SnapshotEnvelope(BaseModel):
    """``Blackboard.get_snapshot()`` — ``{domain: [entry...]}``."""

    status: str
    data: dict[str, list[BlackboardEntryOut]] = Field(default_factory=dict)


class DomainsPayload(BaseModel):
    #: Flattened from ``BlackboardDomain`` via ``.value`` by the service.
    allowed: list[str] = []
    active: list[str] = []


class DomainsEnvelope(BaseModel):
    status: str
    data: DomainsPayload = Field(default_factory=DomainsPayload)


class DomainEntriesEnvelope(BaseModel):
    status: str
    data: list[BlackboardEntryOut] = []
    count: int = 0


class WriteEntryEnvelope(BaseModel):
    status: str
    data: BlackboardEntryOut | None = None


class ClearDomainEnvelope(BaseModel):
    """Flat on purpose: ``AgentGateway``-style ``data.cleared`` is the real read."""

    status: str
    domain: str = ""
    cleared: int = 0


class HistoryRowOut(BaseModel):
    """``Blackboard._record_history()`` — plus ``extra=allow``.

    ``clear`` rows carry ``cleared_count``, and the producer's ``extra``
    argument is open-ended, so unknown keys must survive rather than be
    filtered out by the response model.
    """

    model_config = ConfigDict(extra="allow")

    action: str = ""
    domain: str = ""
    entry_id: str = ""
    contributor: str = ""
    timestamp: str = ""


class HistoryEnvelope(BaseModel):
    status: str
    data: list[HistoryRowOut] = []


class StatsPayload(BaseModel):
    total_entries: int = 0
    active_domains: list[str] = []
    #: A ``@property`` on Blackboard, read explicitly by the service — so it
    #: does reach the wire, unlike properties on a Pydantic model.
    event_bus_enabled: bool = False
    allowed_domains: list[str] = []


class StatsEnvelope(BaseModel):
    status: str
    data: StatsPayload = Field(default_factory=StatsPayload)
