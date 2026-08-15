"""Tests for maop.core.safe_writer — atomic write utilities."""

from __future__ import annotations

import json
from pathlib import Path

from maop.core.reliability.safe_writer import safe_write_bytes, safe_write_json, safe_write_text


class TestSafeWriteBytes:
    def test_write_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        safe_write_bytes(target, b"hello world")
        assert target.read_bytes() == b"hello world"

    def test_overwrite_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        target.write_bytes(b"old content")
        safe_write_bytes(target, b"new content")
        assert target.read_bytes() == b"new content"

    def test_empty_content(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.bin"
        safe_write_bytes(target, b"")
        assert target.read_bytes() == b""

    def test_large_content(self, tmp_path: Path) -> None:
        target = tmp_path / "large.bin"
        data = bytes(range(256)) * 4096
        safe_write_bytes(target, data)
        assert target.read_bytes() == data

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "deep" / "out.bin"
        safe_write_bytes(target, b"nested")
        assert target.read_bytes() == b"nested"

    def test_no_temp_file_left(self, tmp_path: Path) -> None:
        target = tmp_path / "out.bin"
        safe_write_bytes(target, b"data")
        leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
        assert leftovers == []


class TestSafeWriteText:
    def test_write_utf8_text(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        safe_write_text(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_write_chinese_text(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        safe_write_text(target, "你好世界 — 自进化")
        assert target.read_text(encoding="utf-8") == "你好世界 — 自进化"

    def test_custom_encoding(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        safe_write_text(target, "hello", encoding="ascii")
        assert target.read_bytes() == b"hello"

    def test_overwrite(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        target.write_text("old", encoding="utf-8")
        safe_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"


class TestSafeWriteJson:
    def test_write_simple_dict(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        safe_write_json(target, {"key": "value"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"key": "value"}

    def test_write_nested_structure(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        data = {"agents": ["claude", "codex"], "config": {"timeout": 30}}
        safe_write_json(target, data)
        assert json.loads(target.read_text(encoding="utf-8")) == data

    def test_chinese_preserved(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        safe_write_json(target, {"name": "自进化模块", "desc": "错误修复"})
        text = target.read_text(encoding="utf-8")
        assert "自进化模块" in text
        assert "错误修复" in text

    def test_indent_param(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        safe_write_json(target, {"a": 1}, indent=4)
        text = target.read_text(encoding="utf-8")
        assert "    " in text

    def test_list_data(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        safe_write_json(target, [1, 2, 3])
        assert json.loads(target.read_text(encoding="utf-8")) == [1, 2, 3]

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        target.write_text('{"old": true}', encoding="utf-8")
        safe_write_json(target, {"new": True})
        assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}