"""Response envelopes for the notifications router.

Kept out of :mod:`maop.dashboard.routers.notifications` so the router stays
under the 800-line file-size gate; every route declares one of these so the
OpenAPI schema is a typed contract instead of ``dict[str, Any]``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from maop.enterprise.notification.models import (
    ChannelResponse,
    NotificationResponse,
    PreferenceResponse,
    RuleResponse,
    TemplateResponse,
)


class ChannelListEnvelope(BaseModel):
    status: str = "ok"
    channels: list[ChannelResponse]
    count: int


class ChannelEnvelope(BaseModel):
    status: str = "ok"
    channel: ChannelResponse


class RuleListEnvelope(BaseModel):
    status: str = "ok"
    rules: list[RuleResponse]
    count: int


class RuleEnvelope(BaseModel):
    status: str = "ok"
    rule: RuleResponse


class TemplateListEnvelope(BaseModel):
    status: str = "ok"
    templates: list[TemplateResponse]
    count: int


class TemplateEnvelope(BaseModel):
    status: str = "ok"
    template: TemplateResponse


class DeleteEnvelope(BaseModel):
    status: str = "ok"
    deleted: bool


class NotificationEnvelope(BaseModel):
    status: str = "ok"
    notification: NotificationResponse


class NotificationListEnvelope(BaseModel):
    status: str = "ok"
    notifications: list[NotificationResponse]
    count: int
    total: int


class MarkAllReadEnvelope(BaseModel):
    status: str = "ok"
    marked_read: int


class MarkReadEnvelope(BaseModel):
    status: str = "ok"
    read: bool


class UnreadCountEnvelope(BaseModel):
    status: str = "ok"
    unread_count: int


class PreferenceEnvelope(BaseModel):
    status: str = "ok"
    preference: PreferenceResponse | None


class DeadLetterListEnvelope(BaseModel):
    status: str = "ok"
    dead_letters: list[NotificationResponse]
    count: int


class StatsEnvelope(BaseModel):
    status: str = "ok"
    stats: dict[str, Any]


class PublishEventEnvelope(BaseModel):
    status: str = "ok"
    # EventBus.publish returns the number of handlers invoked, not a list of
    # target IDs — the service docstring said otherwise until P1-b.
    delivered_to: int
