"""Tests for MAOP.core.protocol — Dynamic agent communication protocol registry."""


import pytest

from maop.core.agent.plugins_hooks.protocol import ProtocolRegistry


@pytest.fixture
def reg(tmp_path):
    return ProtocolRegistry(root_dir=str(tmp_path))


class TestProtocolRegister:
    def test_register_basic(self, reg):
        proto = reg.register(name="code-review", version="1.0",
                             schema_def={"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
                             participants=["reviewer", "coder"])
        assert proto.name == "code-review"
        assert proto.version == "1.0"
        assert "reviewer" in proto.participants

    def test_register_update(self, reg):
        reg.register(name="code-review", version="1.0", description="v1")
        updated = reg.register(name="code-review", version="1.0", description="v1-updated")
        assert updated.description == "v1-updated"

    def test_register_multiple_versions(self, reg):
        reg.register(name="code-review", version="1.0")
        reg.register(name="code-review", version="2.0")
        versions = reg.list_versions("code-review")
        assert "1.0" in versions
        assert "2.0" in versions


class TestProtocolUnregister:
    def test_unregister_existing(self, reg):
        reg.register(name="test-proto", version="1.0")
        result = reg.unregister("test-proto", "1.0")
        assert result is True
        assert reg.get("test-proto", "1.0") is None

    def test_unregister_not_found(self, reg):
        result = reg.unregister("nonexistent", "1.0")
        assert result is False


class TestProtocolGet:
    def test_get_existing(self, reg):
        reg.register(name="test-proto", version="1.0", description="test")
        proto = reg.get("test-proto", "1.0")
        assert proto is not None
        assert proto.description == "test"

    def test_get_not_found(self, reg):
        assert reg.get("nonexistent", "1.0") is None

    def test_get_cached(self, reg):
        reg.register(name="cached-proto", version="1.0")
        proto1 = reg.get("cached-proto", "1.0")
        proto2 = reg.get("cached-proto", "1.0")
        assert proto1 is proto2


class TestProtocolList:
    def test_list_protocols(self, reg):
        reg.register(name="proto-a", version="1.0")
        reg.register(name="proto-b", version="1.0")
        protos = reg.list_protocols()
        assert len(protos) >= 2

    def test_list_versions(self, reg):
        reg.register(name="multi-ver", version="1.0")
        reg.register(name="multi-ver", version="2.0")
        reg.register(name="multi-ver", version="3.0")
        versions = reg.list_versions("multi-ver")
        assert len(versions) == 3


class TestProtocolValidate:
    def test_validate_valid_payload(self, reg):
        reg.register(name="review", version="1.0",
                     schema_def={"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]})
        assert reg.validate("review", {"file": "main.py"}) is True

    def test_validate_missing_required(self, reg):
        reg.register(name="review", version="1.0",
                     schema_def={"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]})
        assert reg.validate("review", {}) is False

    def test_validate_type_mismatch(self, reg):
        reg.register(name="review", version="1.0",
                     schema_def={"type": "object", "properties": {"count": {"type": "integer"}}})
        assert reg.validate("review", {"count": "not-a-number"}) is False

    def test_validate_unknown_protocol(self, reg):
        assert reg.validate("nonexistent", {}) is False

    def test_validate_no_schema(self, reg):
        reg.register(name="empty-proto", version="1.0")
        assert reg.validate("empty-proto", {"any": "data"}) is True


class TestProtocolMessaging:
    def test_send_message(self, reg):
        reg.register(name="review", version="1.0")
        msg = reg.send_message(protocol="review", sender="coder", recipient="reviewer",
                               payload={"file": "main.py", "feedback": "LGTM"})
        assert msg.valid is True
        assert msg.protocol == "review"

    def test_send_invalid_message(self, reg):
        reg.register(name="review", version="1.0",
                     schema_def={"type": "object", "required": ["file"]})
        msg = reg.send_message(protocol="review", sender="coder", recipient="reviewer", payload={})
        assert msg.valid is False

    def test_get_messages(self, reg):
        reg.register(name="review", version="1.0")
        reg.send_message(protocol="review", sender="a", recipient="b", payload={"x": 1})
        reg.send_message(protocol="review", sender="a", recipient="b", payload={"x": 2})
        messages = reg.get_messages(recipient="b")
        assert len(messages) == 2

    def test_get_messages_filtered(self, reg):
        reg.register(name="proto-a", version="1.0")
        reg.register(name="proto-b", version="1.0")
        reg.send_message(protocol="proto-a", sender="a", recipient="b")
        reg.send_message(protocol="proto-b", sender="a", recipient="b")
        messages = reg.get_messages(recipient="b", protocol="proto-a")
        assert len(messages) == 1
