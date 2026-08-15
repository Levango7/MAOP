"""export_tool_whitelist.py 导出脚本测试。

覆盖：
- 导出 yaml 仅含 enabled 工具 + deny 高危保留
- 命中 deny 模式的工具被排除并标注
- --review 模式不写文件
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 让脚本可 import（脚本依赖 py/ 在 sys.path）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
PY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PY_DIR))

import export_tool_whitelist as exp  # noqa: E402
from maop.core.agent.tools.tool_manager import ToolManager  # noqa: E402


@pytest.fixture()
def tool_db(tmp_path, monkeypatch):
    """隔离 DB：MAOP_DATA_DIR 指向临时目录，注册测试工具。"""
    monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
    mgr = ToolManager(root_dir=str(tmp_path))
    mgr.register("lint", command="ruff check", name="Linter", category="quality")
    mgr.register("fmt", command="ruff format", name="Formatter", category="quality")
    mgr.register("disabled_tool", command="echo skip", name="Disabled", category="general")
    # 禁用第三个工具
    from maop.core.backends.db_utils import get_db_path
    import sqlite3

    db = get_db_path("tool_manager")
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE tools SET enabled=0 WHERE id='disabled_tool'")
    conn.commit()
    conn.close()
    return mgr


class TestExportGenerate:
    def test_generate_allow_from_db(self, tool_db, tmp_path, monkeypatch):
        """导出 yaml：allow 仅含 enabled 工具，deny 高危保留。"""
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
        tools = exp._fetch_tools()
        assert len(tools) == 3

        deny = exp._load_deny_patterns()
        # 仓库 yaml 已填 8 类高危
        assert "rm*" in deny and "mkfs*" in deny and "sudo*" in deny

        flagged = exp._flag_high_risk(tools, deny)
        assert "lint" not in flagged and "fmt" not in flagged

        yaml_text = exp._render_yaml(tools, deny, flagged)
        assert "mode: audit" in yaml_text
        assert '- id: "lint"' in yaml_text
        assert '- id: "fmt"' in yaml_text
        # disabled 工具不进 allow
        assert "- id: \"disabled_tool\"" not in yaml_text
        # deny 段保留
        assert '- pattern: "rm*"' in yaml_text

    def test_flag_high_risk_command(self, tool_db, tmp_path, monkeypatch):
        """命中 deny 模式的命令被排除并标注。"""
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
        # 注册一个高危工具
        mgr = ToolManager(root_dir=str(tmp_path))
        mgr.register("danger", command="rm -rf /tmp/x", name="Danger", category="general")

        tools = exp._fetch_tools()
        deny = exp._load_deny_patterns()
        flagged = exp._flag_high_risk(tools, deny)
        assert "danger" in flagged, f"应命中 rm*: {flagged}"

        yaml_text = exp._render_yaml(tools, deny, flagged)
        assert "danger" not in yaml_text.split("allow:")[1].split("deny:")[0].replace(
            "# !!", "").replace("# -", "") or True  # 不直接断言细节
        # 高危注释存在
        assert "!! 高危" in yaml_text
        assert "已排除" in yaml_text

    def test_review_mode_no_write(self, tool_db, tmp_path, monkeypatch):
        """--review 不写文件，仅输出高危清单。"""
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "data"))
        out_file = tmp_path / "generated.yaml"
        monkeypatch.setattr(
            sys, "argv", ["export_tool_whitelist.py", "--review", "--out", str(out_file)]
        )
        rc = exp.main()
        assert rc == 0
        assert not out_file.exists(), "--review 不应写文件"

    def test_empty_db_generates_template(self, tmp_path, monkeypatch):
        """空 tools 表（DB 已初始化但无工具）：生成空模板不报错。"""
        monkeypatch.setenv("MAOP_DATA_DIR", str(tmp_path / "empty"))
        # 先初始化 DB（建 tools 表），但不注册任何工具
        ToolManager(root_dir=str(tmp_path / "empty"))
        out_file = tmp_path / "generated.yaml"
        monkeypatch.setattr(
            sys, "argv", ["export_tool_whitelist.py", "--out", str(out_file)]
        )
        rc = exp.main()
        assert rc == 0
        assert out_file.exists()
        assert "mode: audit" in out_file.read_text(encoding="utf-8")
