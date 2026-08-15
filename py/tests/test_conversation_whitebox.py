"""White-box regression tests for ConversationManager.

Covers bug2: conversation.py:286 — ``_estimate_tokens(None)`` crashed
with ``TypeError: object of type 'NoneType' has no len()``.  The fix
adds an early ``if not text: return 1`` guard so None / empty strings
return a token count of 1 instead of raising.

These tests also exercise add_message robustness and per-session
isolation of the SQLite-backed message store.
"""

from __future__ import annotations

import pytest

from maop.core.agent.llm_chat.conversation import ConversationManager

# ═══════════════════════════════════════════════════════════════════
# Bug 2: _estimate_tokens must handle None / empty
# ═══════════════════════════════════════════════════════════════════


class TestEstimateTokensRegression:
    """Regression: _estimate_tokens must handle None / empty without crash.

    Before the fix, ``len(text)`` was called unconditionally, so
    ``text=None`` raised ``TypeError``.  The guard ``if not text:
    return 1`` makes None and "" both return 1.
    """

    def test_estimate_tokens_none_returns_1(self):
        """Bug2: _estimate_tokens(None) must return 1, not raise TypeError."""
        assert ConversationManager._estimate_tokens(None) == 1

    def test_estimate_tokens_empty_string_returns_1(self):
        """_estimate_tokens('') must return 1 (falsy short-circuit)."""
        assert ConversationManager._estimate_tokens("") == 1

    def test_estimate_tokens_ascii(self):
        """ASCII text token estimate ≈ len/4 (4 chars per token)."""
        # "hello world" = 11 chars, all non-CJK → int(11/4) = 2
        result = ConversationManager._estimate_tokens("hello world")
        assert result == 2
        assert result >= 1

    def test_estimate_tokens_cjk(self):
        """CJK text token estimate ≈ len/1.5 (1.5 chars per token)."""
        # "你好世界" = 4 CJK chars → int(4/1.5) = int(2.67) = 2
        result = ConversationManager._estimate_tokens("你好世界")
        assert result == 2
        assert result >= 1

    def test_estimate_tokens_mixed(self):
        """Mixed ASCII + CJK uses weighted sum: cjk/1.5 + non_cjk/4."""
        # "Hello 你好" = 2 CJK + 6 non-CJK → int(2/1.5 + 6/4) = int(2.83) = 2
        result = ConversationManager._estimate_tokens("Hello 你好")
        assert result == 2
        assert result >= 1


# ═══════════════════════════════════════════════════════════════════
# add_message robustness with None / empty / normal content
# ═══════════════════════════════════════════════════════════════════


class TestAddMessageContentRegression:
    """Regression: add_message must not crash on None / empty content.

    Bug2 fixed _estimate_tokens(None), which is called inside
    add_message when token_count <= 0.  These tests verify the full
    add_message path no longer raises TypeError from _estimate_tokens.
    """

    def test_add_message_with_none_content(self, tmp_path):
        """add_message(content=None) must not raise TypeError from _estimate_tokens.

        Pydantic may still reject None for a ``str`` field, but that is
        a separate concern from the _estimate_tokens TypeError that
        bug2 fixed.  We only assert TypeError is not raised here.
        """
        mgr = ConversationManager(root_dir=str(tmp_path))
        try:
            mgr.add_message("s-none", role="user", content=None)  # type: ignore[arg-type]
        except TypeError:
            pytest.fail("add_message(content=None) raised TypeError from _estimate_tokens")
        except Exception:
            # Other errors (e.g. Pydantic ValidationError) are outside
            # bug2 scope — the _estimate_tokens guard is what we test.
            pass

    def test_add_message_with_empty_content(self, tmp_path):
        """add_message(content='') must store the message without error."""
        mgr = ConversationManager(root_dir=str(tmp_path))
        msg_id = mgr.add_message("s-empty", role="user", content="")

        assert msg_id.startswith("msg-")
        history = mgr.get_history("s-empty")
        assert len(history) == 1
        assert history[0].content == ""
        assert history[0].token_count == 1  # _estimate_tokens("") == 1

    def test_add_message_normal_content(self, tmp_path):
        """add_message(content='hello') stores content and role correctly."""
        mgr = ConversationManager(root_dir=str(tmp_path))
        msg_id = mgr.add_message("s-normal", role="user", content="hello")

        assert msg_id.startswith("msg-")
        history = mgr.get_history("s-normal")
        assert len(history) == 1
        assert history[0].content == "hello"
        assert history[0].role == "user"
        assert history[0].token_count >= 1


# ═══════════════════════════════════════════════════════════════════
# Session isolation
# ═══════════════════════════════════════════════════════════════════


class TestConversationSessionIsolation:
    """Regression: different sessions must have isolated message histories."""

    def test_conversation_session_isolation(self, tmp_path):
        """Messages in different sessions must not interfere with each other."""
        mgr = ConversationManager(root_dir=str(tmp_path))

        mgr.add_message("s-a", role="user", content="hello A")
        mgr.add_message("s-b", role="user", content="hello B")
        mgr.add_message("s-a", role="assistant", content="hi A")

        history_a = mgr.get_history("s-a")
        history_b = mgr.get_history("s-b")

        assert len(history_a) == 2
        assert len(history_b) == 1
        assert history_a[0].content == "hello A"
        assert history_a[0].role == "user"
        assert history_a[1].content == "hi A"
        assert history_a[1].role == "assistant"
        assert history_b[0].content == "hello B"

        # message_count / token_total should also be session-scoped.
        assert mgr.message_count("s-a") == 2
        assert mgr.message_count("s-b") == 1
        assert mgr.token_total("s-a") > 0
        assert mgr.token_total("s-b") > 0