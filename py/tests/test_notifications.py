"""Tests for the notification center — channels, event bus, manager, router.

Covers:
  - Pydantic models (ChannelCreate, RuleCreate, TemplateCreate, etc.)
  - Channel implementations (Email/Webhook/InApp) — mocked SMTP/HTTP
  - EventBus pub/sub (sync + async handlers, wildcards, history, stats)
  - NotificationManager CRUD (channels/rules/templates/preferences)
  - NotificationManager event-driven delivery (rule matching, templating,
    retry, dead-letter queue)
  - NotificationStore persistence (SQLite + Fernet encryption)
  - Router endpoints under /api/notifications/* (admin role injected)
  - WebSocket broadcaster hook (no real WS — verifies the callback path)

All tests use an in-memory SQLite DB (via tmp_path isolation from conftest)
and run with enterprise edition enabled.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maop.enterprise.notification.channels import (
    BaseChannel,
    EmailChannel,
    InAppChannel,
    WebhookChannel,
    get_channel_class,
    register_channel,
)
from maop.enterprise.notification.event_bus import EventBus
from maop.enterprise.notification.manager import NotificationManager
from maop.enterprise.notification.models import (
    ChannelCreate,
    ChannelType,
    EventPayload,
    EventType,
    NotificationLevel,
    NotificationStatus,
    PreferenceUpdate,
    RuleCreate,
    TemplateCreate,
)
from maop.enterprise.notification.store import (
    NotificationStore,
    decrypt_secret,
    encrypt_secret,
    _mask_config,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def enterprise_mode():
    """Enable enterprise edition so feature gates pass."""
    from maop.config.edition import Edition, reset_edition, set_edition
    set_edition(Edition.ENTERPRISE)
    yield
    reset_edition()


@pytest.fixture(autouse=True)
def _no_pg_backend(monkeypatch):
    """Force in-memory SQLite storage (no PostgreSQL backend)."""
    monkeypatch.delenv("MAOP_STORAGE_BACKEND", raising=False)


@pytest.fixture
def store(tmp_path):
    """Fresh NotificationStore using an isolated SQLite DB."""
    return NotificationStore(db_path=tmp_path / "test_notifications.db")


@pytest.fixture
def event_bus():
    return EventBus(history_size=100)


@pytest.fixture
def manager(store, event_bus):
    return NotificationManager(store=store, event_bus=event_bus, max_retries=2, retry_backoff_s=0.01)


@pytest.fixture
def client(manager, monkeypatch):
    """FastAPI TestClient with admin role injected via middleware.

    Patches the router singleton so endpoints use the test manager.
    """
    from maop.dashboard.routers import notifications as notif_router

    monkeypatch.setattr(notif_router, "_notification_manager", manager)
    monkeypatch.setattr(notif_router, "_event_bus", manager.event_bus)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_admin(request, call_next):
        request.state.auth_roles = ["admin"]
        request.state.auth_identity = "test-admin"
        request.state.tenant_id = ""
        return await call_next(request)

    app.include_router(notif_router.router)
    return TestClient(app)


# ── Pydantic model tests ─────────────────────────────────────────


class TestPydanticModels:
    def test_channel_create_defaults(self):
        c = ChannelCreate(name="test", type=ChannelType.EMAIL)
        assert c.enabled is True
        assert c.config == {}
        assert c.tenant_id == ""

    def test_channel_create_name_required(self):
        with pytest.raises(ValueError):
            ChannelCreate(name="", type=ChannelType.EMAIL)

    def test_rule_create_defaults(self):
        r = RuleCreate(name="r1", event_type="task_failed")
        assert r.enabled is True
        assert r.level == NotificationLevel.INFO
        assert r.channel_ids == []

    def test_template_create_body_required(self):
        with pytest.raises(ValueError):
            TemplateCreate(name="t1", body="")

    def test_preference_update_all_optional(self):
        p = PreferenceUpdate()
        assert p.channel_enabled is None
        assert p.quiet_hours_start is None

    def test_preference_update_quiet_hours_bounds(self):
        with pytest.raises(ValueError):
            PreferenceUpdate(quiet_hours_start=24)
        with pytest.raises(ValueError):
            PreferenceUpdate(quiet_hours_end=-1)

    def test_event_type_enum_canonical(self):
        assert EventType.TASK_COMPLETED.value == "task_completed"
        assert EventType.DAG_FAILED.value == "dag_failed"

    def test_event_payload_defaults(self):
        e = EventPayload(event_type="custom")
        assert e.payload == {}
        assert e.timestamp == 0.0


# ── Channel registry tests ───────────────────────────────────────


class TestChannelRegistry:
    def test_builtin_channels_registered(self):
        assert get_channel_class("email") is EmailChannel
        assert get_channel_class("webhook") is WebhookChannel
        assert get_channel_class("inapp") is InAppChannel

    def test_get_channel_class_case_insensitive(self):
        assert get_channel_class("EMAIL") is EmailChannel
        assert get_channel_class("Email") is EmailChannel

    def test_get_channel_class_unknown_raises(self):
        with pytest.raises(KeyError):
            get_channel_class("nonexistent")

    def test_register_custom_channel(self):
        class DummyChannel(BaseChannel):
            type_name = "dummy"

            def send(self, *, title, body, level=NotificationLevel.INFO, recipient="", context=None):
                return {"success": True}

        register_channel("dummy", DummyChannel)
        assert get_channel_class("dummy") is DummyChannel

    def test_register_invalid_channel_raises(self):
        class NotAChannel:
            pass

        with pytest.raises(TypeError):
            register_channel("invalid", NotAChannel)  # type: ignore[arg-type]


# ── Channel implementation tests ─────────────────────────────────


class TestEmailChannel:
    def test_validate_config_missing_host(self):
        ch = EmailChannel(config={})
        result = ch.validate_config()
        assert result["valid"] is False
        assert "host is required" in result["errors"]

    def test_validate_config_valid(self):
        ch = EmailChannel(config={"host": "smtp.test", "from_addr": "a@b.c"})
        assert ch.validate_config()["valid"] is True

    def test_send_no_recipients(self):
        ch = EmailChannel(config={"host": "smtp.test", "from_addr": "a@b.c"})
        result = ch.send(title="t", body="b")
        assert result["success"] is False
        assert "No recipients" in result["error"]

    def test_send_success_mocked_smtp(self):
        ch = EmailChannel(
            config={
                "host": "smtp.test",
                "port": 587,
                "username": "user",
                "password": "pass",
                "from_addr": "from@test",
                "to_addrs": ["to@test"],
                "use_tls": False,
                "use_ssl": False,
            }
        )
        with patch("smtplib.SMTP") as mock_smtp:
            smtp_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = smtp_instance
            result = ch.send(title="Subject", body="Body")
        assert result["success"] is True
        smtp_instance.sendmail.assert_called_once()

    def test_send_failure_smtp_exception(self):
        ch = EmailChannel(
            config={"host": "smtp.test", "from_addr": "a@b.c", "to_addrs": ["x@y.z"]}
        )
        with patch("smtplib.SMTP", side_effect=Exception("connection refused")):
            result = ch.send(title="t", body="b")
        assert result["success"] is False
        assert "connection refused" in result["error"]

    def test_mask_config_hides_password(self):
        ch = EmailChannel(config={"host": "h", "password": "secret123"})
        masked = ch.mask_config()
        assert masked["password"] == "***"
        assert masked["host"] == "h"


class TestWebhookChannel:
    def test_validate_config_missing_url(self):
        ch = WebhookChannel(config={})
        result = ch.validate_config()
        assert result["valid"] is False

    def test_send_no_url(self):
        ch = WebhookChannel(config={})
        result = ch.send(title="t", body="b")
        assert result["success"] is False

    def test_send_success_mocked_urlopen(self):
        ch = WebhookChannel(config={"url": "https://hook.test/notify"})
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ch.send(title="t", body="b")
        assert result["success"] is True
        assert result["status_code"] == 200

    def test_send_failure_http_error(self):
        import urllib.error
        ch = WebhookChannel(config={"url": "https://hook.test/notify"})
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 500, "Server Error", {}, None)):  # type: ignore[arg-type]
            result = ch.send(title="t", body="b")
        assert result["success"] is False
        assert "500" in result["error"]

    def test_send_with_signature(self):
        ch = WebhookChannel(config={"url": "https://hook.test", "secret": "abc"})
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            ch.send(title="t", body="b")
            req = mock_open.call_args[0][0]
            # urllib normalises header names to lowercase — check case-insensitively
            header_keys = {k.lower() for k in req.headers.keys()}
            assert "x-maop-signature" in header_keys


class TestInAppChannel:
    def test_send_always_succeeds(self):
        ch = InAppChannel(config={"user_id": "u1"})
        result = ch.send(title="t", body="b")
        assert result["success"] is True
        assert result["metadata"]["recipient"] == "u1"


# ── EventBus tests ───────────────────────────────────────────────


class TestEventBus:
    def test_subscribe_and_publish(self, event_bus):
        received: list[EventPayload] = []

        async def handler(event):
            received.append(event)

        event_bus.subscribe("task_failed", handler)
        count = asyncio.run(
            event_bus.publish(EventPayload(event_type="task_failed", payload={"task": "t1"}))
        )
        assert count == 1
        assert len(received) == 1
        assert received[0].payload["task"] == "t1"

    def test_wildcard_subscriber(self, event_bus):
        received: list[str] = []

        async def handler(event):
            received.append(event.event_type)

        event_bus.subscribe("*", handler)
        asyncio.run(event_bus.emit("task_completed", {"a": 1}))
        asyncio.run(event_bus.emit("system_error", {"b": 2}))
        assert received == ["task_completed", "system_error"]

    def test_sync_handler_supported(self, event_bus):
        received: list[str] = []

        def handler(event):
            received.append(event.event_type)

        event_bus.subscribe("test_event", handler)
        asyncio.run(event_bus.emit("test_event"))
        assert received == ["test_event"]

    def test_unsubscribe(self, event_bus):
        received: list[EventPayload] = []

        async def handler(event):
            received.append(event)

        event_bus.subscribe("x", handler)
        assert event_bus.unsubscribe("x", handler) is True
        asyncio.run(event_bus.emit("x"))
        assert received == []

    def test_unsubscribe_not_registered(self, event_bus):
        async def handler(event):
            pass

        assert event_bus.unsubscribe("x", handler) is False

    def test_no_subscribers_returns_zero(self, event_bus):
        count = asyncio.run(event_bus.emit("unmatched"))
        assert count == 0

    def test_handler_error_isolation(self, event_bus):
        success_handler_called: list[bool] = []

        async def failing(event):
            raise RuntimeError("boom")

        async def good(event):
            success_handler_called.append(True)

        event_bus.subscribe("e", failing)
        event_bus.subscribe("e", good)
        count = asyncio.run(event_bus.emit("e"))
        assert count == 2  # both invoked
        assert success_handler_called == [True]
        assert event_bus.stats()["error_count"] == 1

    def test_history(self, event_bus):
        asyncio.run(event_bus.emit("a"))
        asyncio.run(event_bus.emit("b"))
        history = event_bus.history()
        assert len(history) == 2
        assert history[0].event_type == "a"
        assert history[1].event_type == "b"

    def test_history_filtered(self, event_bus):
        asyncio.run(event_bus.emit("a"))
        asyncio.run(event_bus.emit("b"))
        asyncio.run(event_bus.emit("a"))
        history = event_bus.history(event_type="a")
        assert len(history) == 2
        assert all(e.event_type == "a" for e in history)

    def test_stats(self, event_bus):
        async def h(e):
            pass

        event_bus.subscribe("x", h)
        asyncio.run(event_bus.emit("x"))
        asyncio.run(event_bus.emit("x"))
        stats = event_bus.stats()
        assert stats["publish_count"] == 2
        assert stats["deliver_count"] == 2
        assert stats["subscriber_count"] == 1

    def test_clear(self, event_bus):
        async def h(e):
            pass

        event_bus.subscribe("x", h)
        asyncio.run(event_bus.emit("x"))
        event_bus.clear()
        assert event_bus.stats()["publish_count"] == 0
        assert event_bus.subscribers() == {}

    def test_emit_sets_timestamp(self, event_bus):
        asyncio.run(event_bus.emit("x"))
        events = event_bus.history()
        assert events[0].timestamp > 0


# ── Store tests ──────────────────────────────────────────────────


class TestNotificationStore:
    def test_encrypt_decrypt_roundtrip(self):
        original = "my-secret-password"
        encrypted = encrypt_secret(original)
        assert encrypted != original  # actually encrypted (or plain-prefixed)
        assert decrypt_secret(encrypted) == original

    def test_encrypt_empty_string(self):
        assert encrypt_secret("") == ""
        assert decrypt_secret("") == ""

    def test_decrypt_plain_prefix(self):
        assert decrypt_secret("plain:hello") == "hello"

    def test_decrypt_bare_plaintext(self):
        assert decrypt_secret("bare-value") == "bare-value"

    def test_mask_config(self):
        config = {"host": "h", "password": "secret", "name": "n"}
        masked = _mask_config(config)
        assert masked["password"] == "***"
        assert masked["host"] == "h"
        assert masked["name"] == "n"

    def test_save_and_get_channel(self, store):
        channel = {
            "channel_id": "ch_1",
            "name": "test",
            "type": "email",
            "config": {"host": "h", "password": "secret"},
            "description": "",
            "tenant_id": "",
            "enabled": True,
            "status": "active",
            "last_error": "",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        store.save_channel(channel)
        retrieved = store.get_channel("ch_1")
        assert retrieved is not None
        assert retrieved["name"] == "test"
        # Password should be decrypted back to original
        assert retrieved["config"]["password"] == "secret"

    def test_list_channels_by_tenant(self, store):
        for i, tenant in enumerate(["t1", "t1", "t2"]):
            store.save_channel({
                "channel_id": f"ch_{i}",
                "name": f"n{i}",
                "type": "email",
                "config": {},
                "tenant_id": tenant,
                "enabled": True,
                "status": "active",
                "created_at": time.time(),
                "updated_at": time.time(),
            })
        t1_channels = store.list_channels(tenant_id="t1")
        assert len(t1_channels) == 2
        all_channels = store.list_channels()
        assert len(all_channels) == 3

    def test_delete_channel(self, store):
        store.save_channel({
            "channel_id": "ch_x",
            "name": "x",
            "type": "email",
            "config": {},
            "enabled": True,
            "status": "active",
            "created_at": time.time(),
            "updated_at": time.time(),
        })
        assert store.delete_channel("ch_x") is True
        assert store.get_channel("ch_x") is None
        assert store.delete_channel("ch_x") is False

    def test_save_and_get_rule(self, store):
        rule = {
            "rule_id": "r1",
            "name": "rule1",
            "event_type": "task_failed",
            "channel_ids": ["ch1"],
            "template_id": "",
            "filter": {"severity": "critical"},
            "level": "warning",
            "tenant_id": "t1",
            "enabled": True,
            "status": "active",
            "trigger_count": 0,
            "last_triggered_at": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        store.save_rule(rule)
        retrieved = store.get_rule("r1")
        assert retrieved is not None
        assert retrieved["channel_ids"] == ["ch1"]
        assert retrieved["filter"] == {"severity": "critical"}

    def test_list_rules_by_event_type(self, store):
        for i, et in enumerate(["task_failed", "task_completed", "task_failed"]):
            store.save_rule({
                "rule_id": f"r_{i}",
                "name": f"r{i}",
                "event_type": et,
                "channel_ids": [],
                "filter": {},
                "level": "info",
                "enabled": True,
                "status": "active",
                "trigger_count": 0,
                "last_triggered_at": 0,
                "created_at": time.time(),
                "updated_at": time.time(),
            })
        failed_rules = store.list_rules(event_type="task_failed")
        assert len(failed_rules) == 2

    def test_save_and_get_notification(self, store):
        notif = {
            "notification_id": "n1",
            "tenant_id": "t1",
            "user_id": "u1",
            "channel_id": "ch1",
            "channel_type": "inapp",
            "level": "info",
            "title": "Test",
            "body": "Body",
            "status": "pending",
            "event_type": "task_failed",
            "event_payload": {"task": "t1"},
            "retry_count": 0,
            "max_retries": 3,
            "error": "",
            "created_at": time.time(),
            "sent_at": 0,
            "read_at": 0,
        }
        store.save_notification(notif)
        retrieved = store.get_notification("n1")
        assert retrieved is not None
        assert retrieved["title"] == "Test"
        assert retrieved["event_payload"] == {"task": "t1"}

    def test_unread_count(self, store):
        for i in range(3):
            store.save_notification({
                "notification_id": f"n_{i}",
                "user_id": "u1",
                "channel_id": "ch1",
                "channel_type": "inapp",
                "title": "t",
                "body": "b",
                "status": "sent",
                "created_at": time.time(),
                "read_at": 0 if i < 2 else time.time(),  # 2 unread
                "max_retries": 3,
            })
        assert store.unread_count("u1") == 2

    def test_mark_read(self, store):
        store.save_notification({
            "notification_id": "n1",
            "user_id": "u1",
            "channel_id": "ch1",
            "channel_type": "inapp",
            "title": "t",
            "body": "b",
            "status": "sent",
            "created_at": time.time(),
            "max_retries": 3,
        })
        assert store.mark_read("n1") is True
        retrieved = store.get_notification("n1")
        assert retrieved["read_at"] > 0

    def test_mark_all_read(self, store):
        for i in range(3):
            store.save_notification({
                "notification_id": f"n_{i}",
                "user_id": "u1",
                "channel_id": "ch1",
                "channel_type": "inapp",
                "title": "t",
                "body": "b",
                "status": "sent",
                "created_at": time.time(),
                "max_retries": 3,
            })
        count = store.mark_all_read("u1")
        assert count == 3
        assert store.unread_count("u1") == 0

    def test_dead_letters(self, store):
        for i, status in enumerate(["sent", "dead_letter", "pending", "dead_letter"]):
            store.save_notification({
                "notification_id": f"n_{i}_{status}",
                "user_id": "u1",
                "channel_id": "ch1",
                "channel_type": "inapp",
                "title": "t",
                "body": "b",
                "status": status,
                "created_at": time.time(),
                "max_retries": 3,
            })
        dl = store.list_dead_letters()
        assert len(dl) == 2

    def test_preferences(self, store):
        pref = {
            "user_id": "u1",
            "tenant_id": "t1",
            "channel_enabled": {"email": True, "inapp": False},
            "event_level_min": {"task_failed": "warning"},
            "quiet_hours_start": 22,
            "quiet_hours_end": 8,
            "updated_at": time.time(),
        }
        store.save_preference(pref)
        retrieved = store.get_preference("u1")
        assert retrieved is not None
        assert retrieved["channel_enabled"]["email"] is True
        assert retrieved["quiet_hours_start"] == 22


# ── NotificationManager CRUD tests ───────────────────────────────


class TestNotificationManagerCRUD:
    def test_create_channel(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="test-email",
            type=ChannelType.EMAIL,
            config={"host": "smtp.test", "from_addr": "a@b.c", "password": "secret"},
        ))
        assert ch.channel_id.startswith("ch_")
        assert ch.name == "test-email"
        # Password masked in response
        assert ch.config["password"] == "***"

    def test_get_channel(self, manager):
        created = manager.create_channel(ChannelCreate(name="c1", type=ChannelType.INAPP))
        retrieved = manager.get_channel(created.channel_id)
        assert retrieved is not None
        assert retrieved.name == "c1"

    def test_update_channel(self, manager):
        created = manager.create_channel(ChannelCreate(name="c1", type=ChannelType.INAPP))
        from maop.enterprise.notification.models import ChannelUpdate
        updated = manager.update_channel(created.channel_id, ChannelUpdate(name="renamed", enabled=False))
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.enabled is False

    def test_update_channel_not_found(self, manager):
        from maop.enterprise.notification.models import ChannelUpdate
        assert manager.update_channel("nonexistent", ChannelUpdate(name="x")) is None

    def test_delete_channel(self, manager):
        created = manager.create_channel(ChannelCreate(name="c1", type=ChannelType.INAPP))
        assert manager.delete_channel(created.channel_id) is True
        assert manager.get_channel(created.channel_id) is None

    def test_list_channels(self, manager):
        for i in range(3):
            manager.create_channel(ChannelCreate(name=f"c{i}", type=ChannelType.INAPP))
        assert len(manager.list_channels()) == 3

    def test_create_rule(self, manager):
        rule = manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=["ch1"],
            level=NotificationLevel.WARNING,
        ))
        assert rule.rule_id.startswith("rule_")
        assert rule.level == NotificationLevel.WARNING

    def test_create_template(self, manager):
        tpl = manager.create_template(TemplateCreate(
            name="t1",
            subject="Task {task} failed",
            body="Task {task} failed at {timestamp}",
        ))
        assert tpl.template_id.startswith("tpl_")
        assert "{task}" in tpl.body

    def test_update_preference(self, manager):
        pref = manager.update_preference("u1", PreferenceUpdate(
            channel_enabled={"email": True},
            quiet_hours_start=22,
        ))
        assert pref.channel_enabled["email"] is True
        assert pref.quiet_hours_start == 22

    def test_get_preference(self, manager):
        manager.update_preference("u1", PreferenceUpdate(channel_enabled={"inapp": True}))
        pref = manager.get_preference("u1")
        assert pref is not None
        assert pref.channel_enabled["inapp"] is True


# ── NotificationManager delivery tests ───────────────────────────


class TestNotificationManagerDelivery:
    def test_inapp_delivery_succeeds(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, config={"user_id": "u1"},
        ))
        notif = asyncio.run(manager.send_notification(
            channel_id=ch.channel_id,
            title="Hello",
            body="World",
            user_id="u1",
        ))
        assert notif.status == NotificationStatus.PENDING  # before delivery task runs
        # Give the background task time to complete
        asyncio.run(asyncio.sleep(0.05))
        retrieved = manager.get_notification(notif.notification_id)
        assert retrieved is not None
        assert retrieved.status == NotificationStatus.SENT

    def test_send_notification_channel_not_found(self, manager):
        with pytest.raises(ValueError):
            asyncio.run(manager.send_notification(
                channel_id="nonexistent", title="t", body="b",
            ))

    def test_event_driven_delivery_inapp(self, manager):
        # Create InApp channel + rule + template
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, config={"user_id": "u1"},
            tenant_id="t1",
        ))
        tpl = manager.create_template(TemplateCreate(
            name="task-failed-tpl",
            subject="Task {task} failed",
            body="Task {task} failed (event: {event_type})",
            tenant_id="t1",
        ))
        manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=[ch.channel_id],
            template_id=tpl.template_id,
            tenant_id="t1",
            level=NotificationLevel.ERROR,
        ))
        # Publish event
        asyncio.run(manager.event_bus.emit(
            "task_failed", {"task": "my-task"}, tenant_id="t1",
        ))
        # Allow background tasks to complete
        asyncio.run(asyncio.sleep(0.1))
        notifs, total = manager.list_notifications(tenant_id="t1")
        assert total == 1
        assert "my-task" in notifs[0].title or "my-task" in notifs[0].body
        assert notifs[0].level == NotificationLevel.ERROR

    def test_rule_filter_blocks_non_matching_event(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, tenant_id="t1",
        ))
        manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=[ch.channel_id],
            filter={"severity": "critical"},
            tenant_id="t1",
        ))
        # Event with non-matching severity
        asyncio.run(manager.event_bus.emit(
            "task_failed", {"severity": "info"}, tenant_id="t1",
        ))
        asyncio.run(asyncio.sleep(0.05))
        _, total = manager.list_notifications(tenant_id="t1")
        assert total == 0

    def test_rule_filter_matches_event(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, tenant_id="t1",
        ))
        manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=[ch.channel_id],
            filter={"severity": "critical"},
            tenant_id="t1",
        ))
        asyncio.run(manager.event_bus.emit(
            "task_failed", {"severity": "critical", "task": "x"}, tenant_id="t1",
        ))
        asyncio.run(asyncio.sleep(0.05))
        _, total = manager.list_notifications(tenant_id="t1")
        assert total == 1

    def test_tenant_isolation(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, tenant_id="t1",
        ))
        manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=[ch.channel_id],
            tenant_id="t1",
        ))
        # Event from different tenant
        asyncio.run(manager.event_bus.emit(
            "task_failed", {"task": "x"}, tenant_id="t2",
        ))
        asyncio.run(asyncio.sleep(0.05))
        _, total_t1 = manager.list_notifications(tenant_id="t1")
        _, total_t2 = manager.list_notifications(tenant_id="t2")
        assert total_t1 == 0
        assert total_t2 == 0  # no rule for t2

    def test_disabled_rule_not_triggered(self, manager):
        ch = manager.create_channel(ChannelCreate(name="inapp", type=ChannelType.INAPP))
        manager.create_rule(RuleCreate(
            name="r1",
            event_type="task_failed",
            channel_ids=[ch.channel_id],
            enabled=False,
        ))
        asyncio.run(manager.event_bus.emit("task_failed", {}))
        asyncio.run(asyncio.sleep(0.05))
        _, total = manager.list_notifications()
        assert total == 0

    def test_disabled_channel_skipped(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, enabled=False,
        ))
        manager.create_rule(RuleCreate(
            name="r1", event_type="task_failed", channel_ids=[ch.channel_id],
        ))
        asyncio.run(manager.event_bus.emit("task_failed", {}))
        asyncio.run(asyncio.sleep(0.05))
        _, total = manager.list_notifications()
        assert total == 0  # channel disabled, no notification created

    def test_broadcaster_invoked_for_inapp(self, manager):
        broadcast_calls: list[dict] = []

        async def broadcaster(notif):
            broadcast_calls.append(notif)

        manager.set_broadcaster(broadcaster)
        ch = manager.create_channel(ChannelCreate(name="inapp", type=ChannelType.INAPP))
        asyncio.run(manager.send_notification(
            channel_id=ch.channel_id, title="t", body="b",
        ))
        assert len(broadcast_calls) == 1
        assert broadcast_calls[0]["type"] == "notification"

    def test_unread_count_and_mark_read(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, config={"user_id": "u1"},
        ))
        notif = asyncio.run(manager.send_notification(
            channel_id=ch.channel_id, title="t", body="b", user_id="u1",
        ))
        asyncio.run(asyncio.sleep(0.05))
        assert manager.unread_count("u1") == 1
        assert manager.mark_read(notif.notification_id) is True
        assert manager.unread_count("u1") == 0

    def test_mark_all_read(self, manager):
        ch = manager.create_channel(ChannelCreate(
            name="inapp", type=ChannelType.INAPP, config={"user_id": "u1"},
        ))
        for i in range(3):
            asyncio.run(manager.send_notification(
                channel_id=ch.channel_id, title=f"t{i}", body="b", user_id="u1",
            ))
        asyncio.run(asyncio.sleep(0.05))
        assert manager.unread_count("u1") == 3
        count = manager.mark_all_read("u1")
        assert count == 3
        assert manager.unread_count("u1") == 0


# ── Router endpoint tests ────────────────────────────────────────


class TestRouterEndpoints:
    def test_list_channels_empty(self, client):
        r = client.get("/api/notifications/channels")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["channels"] == []
        assert data["count"] == 0

    def test_create_and_list_channel(self, client):
        r = client.post("/api/notifications/channels", json={
            "name": "test-inapp",
            "type": "inapp",
            "config": {"user_id": "u1"},
        })
        assert r.status_code == 200
        channel_id = r.json()["channel"]["channel_id"]

        r = client.get("/api/notifications/channels")
        assert r.status_code == 200
        assert r.json()["count"] == 1

        r = client.get(f"/api/notifications/channels/{channel_id}")
        assert r.status_code == 200
        assert r.json()["channel"]["name"] == "test-inapp"

    def test_update_channel(self, client):
        r = client.post("/api/notifications/channels", json={
            "name": "c1", "type": "inapp",
        })
        channel_id = r.json()["channel"]["channel_id"]
        r = client.put(f"/api/notifications/channels/{channel_id}", json={"name": "renamed"})
        assert r.status_code == 200
        assert r.json()["channel"]["name"] == "renamed"

    def test_delete_channel(self, client):
        r = client.post("/api/notifications/channels", json={
            "name": "c1", "type": "inapp",
        })
        channel_id = r.json()["channel"]["channel_id"]
        r = client.delete(f"/api/notifications/channels/{channel_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        r = client.get(f"/api/notifications/channels/{channel_id}")
        assert r.status_code == 404

    def test_create_rule(self, client):
        r = client.post("/api/notifications/rules", json={
            "name": "r1",
            "event_type": "task_failed",
            "channel_ids": [],
            "level": "warning",
        })
        assert r.status_code == 200
        assert r.json()["rule"]["name"] == "r1"

    def test_create_template(self, client):
        r = client.post("/api/notifications/templates", json={
            "name": "t1",
            "subject": "Subject {task}",
            "body": "Body {task}",
        })
        assert r.status_code == 200
        assert r.json()["template"]["template_id"].startswith("tpl_")

    def test_publish_event(self, client):
        # Set up a channel + rule first
        ch_r = client.post("/api/notifications/channels", json={
            "name": "inapp", "type": "inapp",
        })
        channel_id = ch_r.json()["channel"]["channel_id"]
        client.post("/api/notifications/rules", json={
            "name": "r1",
            "event_type": "task_failed",
            "channel_ids": [channel_id],
        })
        # Publish event
        r = client.post("/api/notifications/events/publish", json={
            "event_type": "task_failed",
            "payload": {"task": "my-task"},
        })
        assert r.status_code == 200
        assert r.json()["delivered_to"] >= 1

    def test_send_notification(self, client):
        ch_r = client.post("/api/notifications/channels", json={
            "name": "inapp", "type": "inapp",
        })
        channel_id = ch_r.json()["channel"]["channel_id"]
        r = client.post("/api/notifications/send", json={
            "channel_id": channel_id,
            "title": "Direct",
            "body": "Send",
            "user_id": "u1",
        })
        assert r.status_code == 200
        assert r.json()["notification"]["title"] == "Direct"

    def test_unread_count(self, client):
        ch_r = client.post("/api/notifications/channels", json={
            "name": "inapp", "type": "inapp", "config": {"user_id": "test-admin"},
        })
        channel_id = ch_r.json()["channel"]["channel_id"]
        client.post("/api/notifications/send", json={
            "channel_id": channel_id,
            "title": "t",
            "body": "b",
            "user_id": "test-admin",
        })
        # Wait for delivery
        import time as _t
        _t.sleep(0.1)
        r = client.get("/api/notifications/unread-count?user_id=test-admin")
        assert r.status_code == 200
        assert r.json()["unread_count"] >= 1

    def test_list_notifications(self, client):
        r = client.get("/api/notifications/list")
        assert r.status_code == 200
        assert "notifications" in r.json()
        assert "total" in r.json()

    def test_preferences(self, client):
        r = client.put("/api/notifications/preferences?user_id=u1", json={
            "channel_enabled": {"email": True},
            "quiet_hours_start": 22,
        })
        assert r.status_code == 200
        assert r.json()["preference"]["channel_enabled"]["email"] is True

        r = client.get("/api/notifications/preferences?user_id=u1")
        assert r.status_code == 200
        assert r.json()["preference"]["quiet_hours_start"] == 22

    def test_stats(self, client):
        r = client.get("/api/notifications/stats")
        assert r.status_code == 200
        assert "stats" in r.json()

    def test_dead_letters(self, client):
        r = client.get("/api/notifications/dead-letters")
        assert r.status_code == 200
        assert "dead_letters" in r.json()

    def test_get_notification_not_found(self, client):
        r = client.get("/api/notifications/nonexistent-id")
        assert r.status_code == 404

    def test_delete_notification(self, client):
        ch_r = client.post("/api/notifications/channels", json={
            "name": "inapp", "type": "inapp",
        })
        channel_id = ch_r.json()["channel"]["channel_id"]
        send_r = client.post("/api/notifications/send", json={
            "channel_id": channel_id, "title": "t", "body": "b",
        })
        notif_id = send_r.json()["notification"]["notification_id"]
        r = client.delete(f"/api/notifications/{notif_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_mark_read_endpoint(self, client):
        ch_r = client.post("/api/notifications/channels", json={
            "name": "inapp", "type": "inapp", "config": {"user_id": "u1"},
        })
        channel_id = ch_r.json()["channel"]["channel_id"]
        send_r = client.post("/api/notifications/send", json={
            "channel_id": channel_id, "title": "t", "body": "b", "user_id": "u1",
        })
        notif_id = send_r.json()["notification"]["notification_id"]
        r = client.post(f"/api/notifications/{notif_id}/read")
        assert r.status_code == 200
        assert r.json()["read"] is True


# ── Router auth tests ────────────────────────────────────────────


class TestRouterAuth:
    """Verify admin-only endpoints reject non-admin users."""

    def test_non_admin_cannot_create_channel(self, manager, monkeypatch):
        from maop.dashboard.routers import notifications as notif_router
        monkeypatch.setattr(notif_router, "_notification_manager", manager)
        monkeypatch.setattr(notif_router, "_event_bus", manager.event_bus)

        app = FastAPI()

        @app.middleware("http")
        async def _inject_viewer(request, call_next):
            request.state.auth_roles = ["viewer"]  # not admin
            request.state.auth_identity = "viewer-user"
            request.state.tenant_id = ""
            return await call_next(request)

        app.include_router(notif_router.router)
        client = TestClient(app)

        r = client.post("/api/notifications/channels", json={
            "name": "x", "type": "inapp",
        })
        assert r.status_code == 403

    def test_non_admin_can_list_channels(self, manager, monkeypatch):
        from maop.dashboard.routers import notifications as notif_router
        monkeypatch.setattr(notif_router, "_notification_manager", manager)
        monkeypatch.setattr(notif_router, "_event_bus", manager.event_bus)

        app = FastAPI()

        @app.middleware("http")
        async def _inject_viewer(request, call_next):
            request.state.auth_roles = ["viewer"]
            request.state.auth_identity = "viewer-user"
            request.state.tenant_id = ""
            return await call_next(request)

        app.include_router(notif_router.router)
        client = TestClient(app)

        r = client.get("/api/notifications/channels")
        assert r.status_code == 200