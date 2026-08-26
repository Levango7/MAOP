"""P0 SQL 注入加固防御逻辑测试。

覆盖 Task 485 新增的两处防御校验（不改变合法输入的查询语义）：

1. ``TenantRLS.scoped_select`` — columns 列表白名单 / order_by 语法白名单 /
   limit 整数强转（py/maop/core/tenant/rls.py）。
2. ``MaopDatabase.fts_search`` — highlight_tag 标签名字符集白名单
   （py/maop/core/backends/data.py）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from maop.core.backends.data import MaopDatabase
from maop.core.backends.db_utils import sqlite_connect
from maop.core.tenant import TenantRLS


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return an isolated SQLite db path under tmp_path/data."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "maop.db"


# ── TenantRLS.scoped_select 加固 ──────────────────────────────────────


class TestScopedSelectHardening:
    def _make_items_table(self, db_path: Path) -> None:
        with sqlite_connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
            )

    # ── 合法输入回归：行为与加固前完全一致 ──

    def test_valid_columns_passthrough(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, params = rls.scoped_select("acme", "items", columns="id, name")
        assert "SELECT id, name FROM items" in sql.replace("\n", " ")
        assert params == ("acme",)

    def test_star_columns_allowed(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, _ = rls.scoped_select("acme", "items", columns="*")
        assert "SELECT * FROM items" in sql.replace("\n", " ")

    def test_valid_order_by_passthrough(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, _ = rls.scoped_select("acme", "items", order_by="name DESC, id asc")
        normalized = " ".join(sql.split())
        assert "ORDER BY name DESC, id asc" in normalized

    def test_limit_coercion_accepts_numeric_string(self, db_path: Path):
        """limit 为数字字符串时经 int() 强转后语义与 int 相同（防御非 int 类型）。"""
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql_int, _ = rls.scoped_select("acme", "items", limit=10)
        sql_str, _ = rls.scoped_select("acme", "items", limit="10")  # type: ignore[arg-type]
        assert "LIMIT 10" in sql_int
        assert "LIMIT 10" in sql_str

    def test_zero_limit_omitted(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        sql, _ = rls.scoped_select("acme", "items", limit=0)
        assert "LIMIT" not in sql

    # ── 恶意输入拒绝 ──

    @pytest.mark.parametrize(
        "malicious",
        [
            "* FROM sqlite_master--",
            "id; DROP TABLE items",
            "id WHERE 1=1",
            "(SELECT secret)",
            'id", (SELECT group_concat(sql) FROM sqlite_master)--',
        ],
    )
    def test_malicious_columns_rejected(self, db_path: Path, malicious: str):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        with pytest.raises(ValueError):
            rls.scoped_select("acme", "items", columns=malicious)

    @pytest.mark.parametrize(
        "malicious",
        [
            "name; DROP TABLE items",
            "name ASC, (SELECT 1)",
            "1=1 --",
            "name COLLATE evil",
            "ABS(name)",
        ],
    )
    def test_malicious_order_by_rejected(self, db_path: Path, malicious: str):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        with pytest.raises(ValueError):
            rls.scoped_select("acme", "items", order_by=malicious)

    def test_malicious_order_by_direction_rejected(self, db_path: Path):
        self._make_items_table(db_path)
        rls = TenantRLS(db_path, scoped_tables=["items"])
        with pytest.raises(ValueError):
            rls.scoped_select("acme", "items", order_by="name DESCENDING")


# ── MaopDatabase.fts_search highlight_tag 加固 ────────────────────────


class TestFtsHighlightTagHardening:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = MaopDatabase(db_path=Path(self.tmp) / "fts.db")
        self.db.init()
        self.db.fts_init("delegations", ["task", "stdout"])
        self.db.insert_delegation(agent="a", task="fix login timeout bug", stdout="auth")
        self.db.fts_rebuild("delegations")

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_tag_works(self):
        """默认 highlight_tag='mark' 行为不变（回归）。"""
        results = self.db.fts_search("delegations", "login", highlight=True)
        assert len(results) >= 1
        assert "<mark>" in results[0]["snippet"]

    @pytest.mark.parametrize("tag", ["em", "b", "hl-1", "Mark_2"])
    def test_safe_tags_work(self, tag: str):
        results = self.db.fts_search(
            "delegations", "login", highlight=True, highlight_tag=tag
        )
        assert len(results) >= 1

    @pytest.mark.parametrize(
        "evil",
        [
            "mark'</sql",
            "a'b",
            "'; DROP TABLE delegations_fts; --",
            "<script>",
        ],
    )
    def test_malicious_tags_rejected(self, evil: str):
        with pytest.raises(ValueError):
            self.db.fts_search(
                "delegations", "login", highlight=True, highlight_tag=evil
            )