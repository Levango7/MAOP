"""Structured Context Compressor — nine-section context compaction.

Inspired by Claude Code's structured context compression. When a conversation
or task context grows too large, instead of naive truncation (which loses
critical information), we produce a structured nine-section summary that
preserves the most important information in each category.

The nine sections:
  1. Primary Request     — What the user originally asked for
  2. Working Assumptions — Assumptions made that haven't been confirmed
  3. Files Modified      — All files touched, with brief change descriptions
  4. Key Decisions       — Architecture/tech choices and their rationale
  5. Current State       — What's done, what's in progress, what's blocked
  6. Error History       — Errors encountered and how they were resolved
  7. User Corrections    — Times the user corrected us (preserved verbatim)
  8. Pending Actions     — What still needs to be done
  9. Environment Notes   — Platform, versions, paths, and other env specifics

The compressor takes a list of context messages (or a text blob) and produces
a compact structured summary. The User Corrections section is always preserved
verbatim — this is the "guardrail" mechanism that prevents repeating mistakes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, cast

from pydantic import BaseModel, Field

# ── Models ────────────────────────────────────────────────────

class ContextSection(BaseModel):
    """A single section of the compressed context."""
    title: str
    content: str
    priority: int = 0  # Higher = more important
    preserved_verbatim: bool = False


class CompressionResult(BaseModel):
    """Result of context compression."""
    sections: list[ContextSection] = Field(default_factory=list)
    original_tokens: int = 0  # Estimated token count of original
    compressed_tokens: int = 0  # Estimated token count of compressed
    reduction_pct: float = 0.0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Context Compressor ────────────────────────────────────────

class ContextCompressor:
    """Compress a conversation/task context into a nine-section summary.

    Usage::

        compressor = ContextCompressor()
        result = compressor.compress(
            messages=conversation_history,
            max_tokens=4000,
        )
        # result.sections contains the 9 sections
        # User Corrections section is preserved verbatim

    Or from a text blob::

        result = compressor.compress_text(
            text=long_text,
            max_tokens=4000,
        )
    """

    # Section titles (in priority order)
    SECTION_TITLES = [
        "Primary Request",
        "Working Assumptions",
        "Files Modified",
        "Key Decisions",
        "Current State",
        "Error History",
        "User Corrections",
        "Pending Actions",
        "Environment Notes",
    ]

    # Patterns for extracting user corrections
    _CORRECTION_PATTERNS = [
        r"(?:no|stop|don't|wrong|incorrect|actually|wait)[,!]\s.*",
        r"(?:that's not|this is wrong|you messed up|fix this)[.!].*",
        r"(?:I meant|I wanted|not what I asked).*",
        r"(?:revert|undo|rollback).*",
    ]

    # Patterns for extracting file modifications
    _FILE_PATTERNS = [
        r"(?:modified|edited|created|updated|deleted|wrote)\s+[:`]?([^\s:`]+)[:`]?",
        r"([^\s]+\.(?:py|js|ts|json|yaml|yml|md|txt|html|css|sql))",
    ]

    # Patterns for extracting errors
    _ERROR_PATTERNS = [
        r"(?:error|exception|failed|traceback|syntax\s*error)[:\s].*",
    ]

    def __init__(self, max_section_tokens: int = 500) -> None:
        self._max_section_tokens = max_section_tokens

    def compress(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 4000,
    ) -> CompressionResult:
        """Compress a list of conversation messages.

        Parameters
        ----------
        messages : list[dict]
            Conversation messages with 'role' and 'content' keys.
        max_tokens : int
            Target maximum token count for the compressed output.

        Returns
        -------
        CompressionResult
        """
        original_text = "\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')}"
            for m in messages
        )
        original_tokens = self._estimate_tokens(original_text)

        # Extract each section from the messages
        sections: list[ContextSection] = []

        # 1. Primary Request — first user message
        primary = self._extract_primary_request(messages)
        sections.append(ContextSection(
            title="Primary Request", content=primary, priority=9,
        ))

        # 2. Working Assumptions
        assumptions = self._extract_assumptions(messages)
        sections.append(ContextSection(
            title="Working Assumptions", content=assumptions, priority=7,
        ))

        # 3. Files Modified
        files = self._extract_files_modified(messages)
        sections.append(ContextSection(
            title="Files Modified", content=files, priority=8,
        ))

        # 4. Key Decisions
        decisions = self._extract_key_decisions(messages)
        sections.append(ContextSection(
            title="Key Decisions", content=decisions, priority=7,
        ))

        # 5. Current State
        current_state = self._extract_current_state(messages)
        sections.append(ContextSection(
            title="Current State", content=current_state, priority=8,
        ))

        # 6. Error History
        errors = self._extract_error_history(messages)
        sections.append(ContextSection(
            title="Error History", content=errors, priority=5,
        ))

        # 7. User Corrections — PRESERVED VERBATIM
        corrections = self._extract_user_corrections(messages)
        sections.append(ContextSection(
            title="User Corrections",
            content=corrections,
            priority=10,  # Highest priority
            preserved_verbatim=True,
        ))

        # 8. Pending Actions
        pending = self._extract_pending_actions(messages)
        sections.append(ContextSection(
            title="Pending Actions", content=pending, priority=6,
        ))

        # 9. Environment Notes
        env_notes = self._extract_env_notes(messages)
        sections.append(ContextSection(
            title="Environment Notes", content=env_notes, priority=4,
        ))

        # Trim sections to fit max_tokens
        sections = self._trim_to_budget(sections, max_tokens)

        compressed_text = "\n\n".join(
            f"## {s.title}\n{s.content}" for s in sections
        )
        compressed_tokens = self._estimate_tokens(compressed_text)
        reduction = 0.0
        if original_tokens > 0:
            reduction = (1 - compressed_tokens / original_tokens) * 100

        return CompressionResult(
            sections=sections,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction_pct=reduction,
        )

    def compress_text(
        self,
        text: str,
        max_tokens: int = 4000,
    ) -> CompressionResult:
        """Compress a raw text blob into nine sections."""
        messages = [{"role": "mixed", "content": text}]
        return self.compress(messages, max_tokens)

    def to_prompt(self, result: CompressionResult) -> str:
        """Render a CompressionResult as a prompt-ready string."""
        lines = ["[Compressed Context Summary]"]
        for section in result.sections:
            marker = " (verbatim)" if section.preserved_verbatim else ""
            lines.append(f"\n## {section.title}{marker}")
            lines.append(section.content)
        lines.append("\n---")
        lines.append(f"Original: ~{result.original_tokens} tokens -> "
                     f"Compressed: ~{result.compressed_tokens} tokens "
                     f"({result.reduction_pct:.1f}% reduction)")
        return "\n".join(lines)

    # ── Section extractors ────────────────────────────────────

    def _extract_primary_request(self, messages: list[dict]) -> str:
        """First user message — the original request."""
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                return cast(str, content[:500])
        return "No primary request found"

    def _extract_assumptions(self, messages: list[dict]) -> str:
        """Extract assumptions (lines starting with 'Assumption:' or similar)."""
        assumptions: list[str] = []
        pattern = re.compile(
            r"(?:assumption|假设|前提)[:\s]+(.+?)(?:\n|$)",
            re.IGNORECASE,
        )
        for m in messages:
            for match in pattern.finditer(m.get("content", "")):
                assumptions.append(match.group(1).strip()[:200])
        if not assumptions:
            return "None explicitly stated"
        return "\n".join(f"- {a}" for a in assumptions[:10])

    def _extract_files_modified(self, messages: list[dict]) -> str:
        """Extract file paths mentioned in the conversation."""
        files: dict[str, str] = {}
        for m in messages:
            content = m.get("content", "")
            for pattern in self._FILE_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    path = match.group(1) if match.groups() else match.group(0)
                    path = path.strip()
                    if path and len(path) < 200:
                        files.setdefault(path, "")

        if not files:
            return "No files modified"
        return "\n".join(f"- {f}" for f in sorted(files.keys())[:30])

    def _extract_key_decisions(self, messages: list[dict]) -> str:
        """Extract decision-like statements."""
        decisions: list[str] = []
        pattern = re.compile(
            r"(?:decision|决定|chose|selected|use|using|adopted)[:\s]+(.+?)(?:\n|$)",
            re.IGNORECASE,
        )
        for m in messages:
            for match in pattern.finditer(m.get("content", "")):
                decisions.append(match.group(1).strip()[:200])
        if not decisions:
            return "No explicit decisions recorded"
        return "\n".join(f"- {d}" for d in decisions[:10])

    def _extract_current_state(self, messages: list[dict]) -> str:
        """Extract current state from the last few messages."""
        recent = messages[-3:] if len(messages) >= 3 else messages
        contents = [m.get("content", "")[:300] for m in recent]
        return "\n---\n".join(contents) if contents else "Unknown"

    def _extract_error_history(self, messages: list[dict]) -> str:
        """Extract error messages and their resolutions."""
        errors: list[str] = []
        for m in messages:
            content = m.get("content", "")
            for pattern in self._ERROR_PATTERNS:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    errors.append(match.group(0).strip()[:200])
        if not errors:
            return "No errors encountered"
        return "\n".join(f"- {e}" for e in errors[:10])

    def _extract_user_corrections(self, messages: list[dict]) -> str:
        """Extract user corrections — PRESERVED VERBATIM.

        This is the most critical section. When the user corrects us,
        we must never lose that correction, even under aggressive compression.
        """
        corrections: list[str] = []
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content", "").strip()
            if not content:
                continue
            # Check if this message looks like a correction
            for pattern in self._CORRECTION_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    corrections.append(content[:500])
                    break
            # Also catch short negative responses
            if content.lower().strip() in ("no", "wrong", "incorrect", "不对", "错", "不是"):
                corrections.append(content[:500])

        if not corrections:
            return "No user corrections"
        return "\n---\n".join(corrections)

    def _extract_pending_actions(self, messages: list[dict]) -> str:
        """Extract pending/todo items."""
        pending: list[str] = []
        patterns = [
            r"(?:todo|pending|待办|needs? to|still need|must|should)[:\s]+(.+?)(?:\n|$)",
            r"(?:下一步|next step)[:\s]+(.+?)(?:\n|$)",
        ]
        for m in messages:
            for pattern in patterns:
                for match in re.finditer(pattern, m.get("content", ""), re.IGNORECASE):
                    pending.append(match.group(1).strip()[:200])
        if not pending:
            return "No pending actions"
        return "\n".join(f"- {p}" for p in pending[:10])

    def _extract_env_notes(self, messages: list[dict]) -> str:
        """Extract environment-related information."""
        notes: list[str] = []
        patterns = [
            r"(?:platform|os|python|node|version|path|directory)[:\s]+(.+?)(?:\n|$)",
        ]
        for m in messages:
            for pattern in patterns:
                for match in re.finditer(pattern, m.get("content", ""), re.IGNORECASE):
                    notes.append(match.group(0).strip()[:200])
        if not notes:
            return "Not specified"
        return "\n".join(f"- {n}" for n in notes[:10])

    # ── Utilities ─────────────────────────────────────────────

    def _trim_to_budget(
        self,
        sections: list[ContextSection],
        max_tokens: int,
    ) -> list[ContextSection]:
        """Trim sections to fit within max_tokens budget.

        Verbatim sections are never trimmed. Non-verbatim sections are
        trimmed from lowest priority first.
        """
        # Sort by priority (highest first) for trimming order
        sorted_sections = sorted(sections, key=lambda s: s.priority, reverse=True)

        total = sum(self._estimate_tokens(s.content) for s in sorted_sections)
        if total <= max_tokens:
            return sections  # Everything fits

        # Trim from lowest priority (end of sorted list)
        for i in range(len(sorted_sections) - 1, -1, -1):
            if total <= max_tokens:
                break
            s = sorted_sections[i]
            if s.preserved_verbatim:
                continue  # Never trim verbatim sections
            section_tokens = self._estimate_tokens(s.content)
            # Trim this section to half its size
            half = len(s.content) // 2
            s.content = s.content[:half] + "\n[... trimmed]"
            new_tokens = self._estimate_tokens(s.content)
            total = total - section_tokens + new_tokens

        # Restore original order
        title_order = {t: i for i, t in enumerate(self.SECTION_TITLES)}
        sorted_sections.sort(key=lambda s: title_order.get(s.title, 99))
        return sorted_sections

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Token estimate: ~1 token per 4 chars for ASCII, ~1 token per 1.5 chars for CJK."""
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
        non_cjk = len(text) - cjk
        return int(cjk / 1.5 + non_cjk / 4)
