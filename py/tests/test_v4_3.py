"""Tests for v4.3: ImageStore, multimodal ChatEngine, self-referential SubagentManager."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from maop.core.chat_engine import ChatEngine, ChatMessage, ChatRequest, ContentPart
from maop.core.image_store import MAX_IMAGE_SIZE, ImageMeta, ImageStore
from maop.core.subagent import SubagentManager
from maop.delegate.models import AgentConfig

# ═══════════════════════════════════════════════════════════════════
# ImageStore Tests
# ═══════════════════════════════════════════════════════════════════

class TestImageMeta:
    def test_defaults(self):
        m = ImageMeta(id="test")
        assert m.session_id == ""
        assert m.size_bytes == 0
        assert m.content_type == ""


class TestImageStore:
    def test_init(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        assert store._upload_dir == tmp_path / "data" / "uploads"

    def test_save_and_get(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        img_id = store.save("s1", "test.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert img_id.startswith("img-")

        meta = store.get_meta(img_id)
        assert meta is not None
        assert meta.session_id == "s1"
        assert meta.filename == "test.png"

    def test_save_base64(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        b64 = base64.b64encode(b"fake image data").decode()
        img_id = store.save_base64("s1", "photo.jpg", b64)
        assert img_id.startswith("img-")

    def test_get_path(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        img_id = store.save("s1", "test.png", b"\x00" * 50)
        path = store.get_path(img_id)
        assert path is not None
        assert Path(path).exists()

    def test_get_data(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        data = b"hello image"
        img_id = store.save("s1", "test.png", data)
        retrieved = store.get_data(img_id)
        assert retrieved == data

    def test_get_base64(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        data = b"hello image"
        img_id = store.save("s1", "test.png", data)
        b64 = store.get_base64(img_id)
        assert b64 is not None
        assert base64.b64decode(b64) == data

    def test_list_session_images(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        store.save("s1", "a.png", b"\x00" * 10)
        store.save("s1", "b.png", b"\x00" * 10)
        store.save("s2", "c.png", b"\x00" * 10)
        images = store.list_session_images("s1")
        assert len(images) == 2

    def test_delete(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        img_id = store.save("s1", "test.png", b"\x00" * 10)
        assert store.delete(img_id) is True
        assert store.get_meta(img_id) is None
        assert store.delete("nonexistent") is False

    def test_cleanup_session(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        store.save("s1", "a.png", b"\x00" * 10)
        store.save("s1", "b.png", b"\x00" * 10)
        count = store.cleanup_session("s1")
        assert count == 2
        assert len(store.list_session_images("s1")) == 0

    def test_max_size_exceeded(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        with pytest.raises(ValueError, match="too large"):
            store.save("s1", "big.png", b"\x00" * (MAX_IMAGE_SIZE + 1))

    def test_ext_to_mime(self):
        assert ImageStore._ext_to_mime(".png") == "image/png"
        assert ImageStore._ext_to_mime(".jpg") == "image/jpeg"
        assert ImageStore._ext_to_mime(".svg") == "image/svg+xml"
        assert ImageStore._ext_to_mime(".xyz") == "application/octet-stream"

    def test_content_type_auto(self, tmp_path):
        store = ImageStore(root_dir=str(tmp_path))
        img_id = store.save("s1", "photo.jpg", b"\x00" * 10)
        meta = store.get_meta(img_id)
        assert meta.content_type == "image/jpeg"


# ═══════════════════════════════════════════════════════════════════
# ContentPart + ChatMessage Multimodal Tests
# ═══════════════════════════════════════════════════════════════════

class TestContentPart:
    def test_text_part(self):
        p = ContentPart(type="text", text="Hello")
        assert p.type == "text"
        assert p.text == "Hello"

    def test_image_url_part(self):
        p = ContentPart(type="image_url", image_url="https://example.com/img.png")
        assert p.type == "image_url"
        assert p.image_url != ""

    def test_image_id_part(self):
        p = ContentPart(type="image_url", image_id="img-abc123")
        assert p.image_id == "img-abc123"


class TestChatMessageMultimodal:
    def test_text_content(self):
        m = ChatMessage(role="user", content="Hello")
        assert isinstance(m.content, str)

    def test_multipart_content(self):
        parts = [
            ContentPart(type="text", text="What is this?"),
            ContentPart(type="image_url", image_url="https://example.com/img.png"),
        ]
        m = ChatMessage(role="user", content=parts)
        assert isinstance(m.content, list)
        assert len(m.content) == 2


class TestChatRequestMultimodal:
    def test_with_images(self):
        r = ChatRequest(message="Describe this", images=["img-abc", "img-def"])
        assert len(r.images) == 2

    def test_no_images(self):
        r = ChatRequest(message="Hello")
        assert r.images == []


# ═══════════════════════════════════════════════════════════════════
# ChatEngine Multimodal Tests
# ═══════════════════════════════════════════════════════════════════

class TestChatEngineMultimodal:
    def test_build_user_content_text_only(self, tmp_path):
        from maop.memory.manager import ConsolidationTrigger, MemoryManagerConfig
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(message="Hello", images=[])
        content = engine._build_user_content(request)
        assert isinstance(content, str)
        assert content == "Hello"

    def test_build_user_content_with_url(self, tmp_path):
        from maop.memory.manager import ConsolidationTrigger, MemoryManagerConfig
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(
            message="What is this?",
            images=["https://example.com/img.png"],
        )
        content = engine._build_user_content(request)
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"

    def test_build_user_content_with_data_uri(self, tmp_path):
        from maop.memory.manager import ConsolidationTrigger, MemoryManagerConfig
        engine = ChatEngine(root_dir=str(tmp_path), config=MemoryManagerConfig(
            consolidation=ConsolidationTrigger(auto_trigger=False),
        ))
        request = ChatRequest(
            message="Analyze",
            images=["data:image/png;base64,iVBORw0KGgo="],
        )
        content = engine._build_user_content(request)
        assert isinstance(content, list)
        assert content[1]["type"] == "image_url"


# ═══════════════════════════════════════════════════════════════════
# Self-referential SubagentManager Tests
# ═══════════════════════════════════════════════════════════════════

class TestSubagentSelfReference:
    def test_spawn_with_call_chain(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        info = mgr.spawn(
            parent="MAOP", agent="mavis", task="test",
            call_chain=["MAOP"],
        )
        assert info.depth == 1

    def test_spawn_deep_chain(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        info = mgr.spawn(
            parent="mavis", agent="claude", task="test",
            call_chain=["MAOP", "mavis"],
        )
        assert info.depth == 2

    def test_self_ref_limit(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError, match="Self-reference limit"):
            mgr.spawn(
                parent="MAOP", agent="MAOP", task="test",
                call_chain=["MAOP", "MAOP", "MAOP"],
                max_self_ref_depth=3,
            )

    def test_self_ref_allowed_within_limit(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        info = mgr.spawn(
            parent="MAOP", agent="MAOP", task="test",
            call_chain=["MAOP", "mavis"],
            max_self_ref_depth=3,
        )
        assert info.child_agent == "MAOP"

    def test_max_depth_with_call_chain(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        with pytest.raises(ValueError, match="Max subagent depth"):
            mgr.spawn(
                parent="a", agent="b", task="test",
                call_chain=["x", "y", "z", "w", "v"],
                max_depth=4,
            )

    def test_fallback_db_depth_without_chain(self, tmp_path):
        mgr = SubagentManager(root_dir=str(tmp_path))
        info = mgr.spawn(parent="root", agent="child", task="test")
        assert info.depth >= 1


# ═══════════════════════════════════════════════════════════════════
# AgentConfig Vision Extension Tests
# ═══════════════════════════════════════════════════════════════════

class TestAgentConfigVision:
    def test_defaults(self):
        cfg = AgentConfig(name="test")
        assert cfg.supports_vision is False
        assert cfg.image_arg_template == ""

    def test_vision_agent(self):
        cfg = AgentConfig(
            name="claude",
            cli="claude",
            supports_vision=True,
            image_arg_template="--image {image_path}",
        )
        assert cfg.supports_vision is True
        assert "{image_path}" in cfg.image_arg_template
