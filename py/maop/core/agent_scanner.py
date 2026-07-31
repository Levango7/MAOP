"""MAOP Agent Scanner — Auto-discover agent CLIs in the local environment.

Scans $PATH (and common install locations) for known agent CLI binaries,
reports their availability, version, and capabilities.

Known agents (extensible via KNOWN_AGENTS registry):
  - claude (Claude Code / Anthropic CLI)
  - codex (OpenAI Codex CLI)
  - gemini (Google Gemini CLI)
  - cursor (Cursor agent)
  - aider (Aider AI pair programming)
  - copilot (GitHub Copilot CLI)
  - goose (Block Goose CLI)
  - trae (Trae AI CLI)
  - cline (Cline VS Code extension CLI)
  - MAOP (MAOP itself — meta-agent)

Usage::

    from maop.core.agent_scanner import AgentScanner

    scanner = AgentScanner()
    found = scanner.scan()
    for agent in found:
        print(f"{agent.name}: {agent.cli_path} v{agent.version}")
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class AgentSource(str, Enum):
    SCANNED = "scanned"
    MANUAL = "manual"
    YAML = "yaml"


class AgentStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    UNKNOWN = "unknown"


class ScannedAgent(BaseModel):
    name: str
    cli_path: str = ""
    version: str = ""
    source: AgentSource = AgentSource.SCANNED
    status: AgentStatus = AgentStatus.UNKNOWN
    capabilities: list[str] = Field(default_factory=list)
    provider: str = ""
    description: str = ""
    model: str = ""
    timeout_s: int = 120
    driver: str = "cli"
    cli_args: str = ""
    last_checked: str = ""
    error: str = ""


class KnownAgentDef(BaseModel):
    name: str
    cli_names: list[str]
    provider: str = ""
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
    version_args: list[str] = Field(default_factory=lambda: ["--version"])
    model: str = ""
    driver: str = "cli"
    cli_args: str = ""
    timeout_s: int = 120


KNOWN_AGENTS: list[KnownAgentDef] = [
    KnownAgentDef(
        name="claude", cli_names=["claude"], provider="anthropic",
        capabilities=["chat", "code", "edit", "search", "vision", "react"],
        description="Claude Code — Anthropic's agentic coding CLI",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="codex", cli_names=["codex", "openai-codex"], provider="openai",
        capabilities=["chat", "code", "edit", "search"],
        description="OpenAI Codex CLI",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="gemini", cli_names=["gemini", "gcloud"], provider="google",
        capabilities=["chat", "code", "vision", "search"],
        description="Google Gemini CLI",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="aider", cli_names=["aider"], provider="open-source",
        capabilities=["chat", "code", "edit", "git"],
        description="Aider — AI pair programming in your terminal",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="goose", cli_names=["goose"], provider="block",
        capabilities=["chat", "code", "edit", "search", "react"],
        description="Block Goose — open source AI developer agent",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="trae", cli_names=["trae"], provider="bytedance",
        capabilities=["chat", "code", "edit"],
        description="Trae AI — ByteDance's AI coding assistant",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="cursor", cli_names=["cursor"], provider="cursor",
        capabilities=["chat", "code", "edit", "search"],
        description="Cursor — AI-first code editor CLI",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="copilot", cli_names=["github-copilot-cli", "copilot"], provider="github",
        capabilities=["chat", "code", "search"],
        description="GitHub Copilot CLI",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="cline", cli_names=["cline"], provider="open-source",
        capabilities=["chat", "code", "edit", "search", "react"],
        description="Cline — autonomous coding agent for VS Code",
        version_args=["--version"],
    ),
    KnownAgentDef(
        name="maop", cli_names=["maop"], provider="self",
        capabilities=["chat", "code", "plan", "verify", "evolve", "orchestrate", "react"],
        description="MAOP — self-referential multi-agent orchestration platform",
        version_args=["--version"],
        driver="wrapper",
    ),
]


class AgentScanner:
    """Scan local environment for agent CLIs.

    Features:
      - Scan $PATH for known agent binaries
      - Probe version via CLI execution
      - Custom search paths (Windows AppData, Homebrew, etc.)
      - SQLite persistence for scan results
      - Extensible KNOWN_AGENTS registry
    """

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("agent_scanner")
        self._extra_paths = self._detect_extra_paths()
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scanned_agents (
                    name TEXT PRIMARY KEY,
                    cli_path TEXT DEFAULT '',
                    version TEXT DEFAULT '',
                    source TEXT DEFAULT 'scanned',
                    status TEXT DEFAULT 'unknown',
                    capabilities TEXT DEFAULT '[]',
                    provider TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    timeout_s INTEGER DEFAULT 120,
                    driver TEXT DEFAULT 'cli',
                    cli_args TEXT DEFAULT '',
                    last_checked TEXT DEFAULT '',
                    error TEXT DEFAULT ''
                )
            """)

    @staticmethod
    def _detect_extra_paths() -> list[str]:
        paths = []
        home = Path.home()
        if os.name == "nt":
            appdata = os.environ.get("APPDATA", "")
            localappdata = os.environ.get("LOCALAPPDATA", "")
            if appdata:
                paths.append(str(Path(appdata) / "Programs"))
            if localappdata:
                paths.append(str(Path(localappdata) / "Programs"))
                paths.append(str(Path(localappdata) / "Programs" / "cursor"))
            paths.append(str(home / "AppData" / "Local" / "Programs"))
        else:
            paths.extend([
                "/usr/local/bin",
                "/opt/homebrew/bin",
                str(home / ".local" / "bin"),
                str(home / ".cursor" / "bin"),
            ])
        return paths

    def _search_paths(self) -> list[str]:
        env_path = os.environ.get("PATH", "").split(os.pathsep)
        return env_path + self._extra_paths

    def _find_cli(self, cli_name: str) -> str | None:
        result = shutil.which(cli_name)
        if result:
            return result
        for search_dir in self._extra_paths:
            candidate = Path(search_dir) / cli_name
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)
            if os.name == "nt":
                for ext in (".exe", ".cmd", ".bat"):
                    candidate_ext = Path(search_dir) / f"{cli_name}{ext}"
                    if candidate_ext.exists():
                        return str(candidate_ext)
        return None

    def _probe_version(self, cli_path: str, version_args: list[str]) -> str:
        try:
            result = subprocess.run(
                [cli_path] + version_args,
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=True,
            )
            output = (result.stdout or result.stderr or "").strip()
            first_line = output.split("\n")[0].strip() if output else ""
            return first_line[:80]
        except Exception:
            logger.debug("Silent exception in core/agent_scanner.py:248", exc_info=True)
            return ""

    def scan(self) -> list[ScannedAgent]:
        """Scan for all known agent CLIs.

        Two phases: locate every CLI (fast, no subprocess), then probe each
        installed CLI's version concurrently via a thread pool (subprocess is
        I/O bound, so threads give a large speed-up over the old serial loop).
        """
        found: list[ScannedAgent] = []
        now = datetime.now(timezone.utc).isoformat()
        known_names: set[str] = set()
        # (agent, version_args) for installed CLIs whose version we must probe
        pending: list[tuple[ScannedAgent, list[str]]] = []

        for known in KNOWN_AGENTS:
            cli_path: str | None = ""
            for cli_name in known.cli_names:
                cli_path = self._find_cli(cli_name) or ""
                if cli_path:
                    break

            if not cli_path:
                agent = ScannedAgent(
                    name=known.name, source=AgentSource.SCANNED,
                    status=AgentStatus.UNAVAILABLE, provider=known.provider,
                    description=known.description, capabilities=known.capabilities,
                    model=known.model, driver=known.driver, cli_args=known.cli_args,
                    timeout_s=known.timeout_s, last_checked=now,
                )
                # Regression fix: UNAVAILABLE agents must also be persisted —
                # list_agents() reads from the DB, and callers rely on seeing
                # known-but-not-installed agents (pre-refactor behaviour).
                self._upsert_db(agent)
                found.append(agent)
                known_names.add(known.name)
            else:
                agent = ScannedAgent(
                    name=known.name, cli_path=cli_path,
                    source=AgentSource.SCANNED, status=AgentStatus.AVAILABLE,
                    provider=known.provider, description=known.description,
                    capabilities=known.capabilities, model=known.model,
                    driver=known.driver, cli_args=known.cli_args,
                    timeout_s=known.timeout_s, last_checked=now,
                )
                pending.append((agent, known.version_args))

        # Config-driven discovery: surface any *enabled* CLI agent defined in
        # config/agents.yaml that is actually installed (present in PATH), so
        # the scan reflects every locally-installed agent CLI — not just the
        # hard-coded KNOWN_AGENTS whitelist. Agents that are not installed are
        # skipped (we don't add unavailable noise to the registry).
        for name, (cli_exec, caps, desc) in self._config_cli_agents().items():
            if name in known_names:
                continue
            cli_path = self._find_cli(cli_exec)
            if not cli_path:
                continue
            agent = ScannedAgent(
                name=name, cli_path=cli_path,
                source=AgentSource.YAML, status=AgentStatus.AVAILABLE,
                provider="", description=desc, capabilities=caps or [],
                last_checked=now,
            )
            pending.append((agent, ["--version"]))
            known_names.add(name)

        # Phase 2: probe versions concurrently (subprocess is I/O bound).
        if pending:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(pending), 12)
            ) as ex:
                futures = {
                    ex.submit(self._probe_version, ag.cli_path, args): ag
                    for ag, args in pending
                }
                for fut in concurrent.futures.as_completed(futures):
                    ag = futures[fut]
                    try:
                        ag.version = fut.result() or ""
                    except Exception:
                        ag.version = ""

        for agent, _ in pending:
            self._upsert_db(agent)
            found.append(agent)

        return found

    def _find_config(self) -> "Path | None":
        """Locate config/agents.yaml relative to the project root."""
        candidates = [
            self._root / "config" / "agents.yaml",
            Path(__file__).resolve().parents[3] / "config" / "agents.yaml",
            Path.cwd() / "config" / "agents.yaml",
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def _config_cli_agents(self) -> "dict[str, tuple[str, list[str], str]]":
        """Return {agent_name: (executable, capabilities, description)} for every
        enabled CLI agent declared in config/agents.yaml (skips disabled agents
        and MAOP-internal python wrappers like doc-pipeline / MAOP itself)."""
        result: dict[str, tuple[str, list[str], str]] = {}
        try:
            import yaml
        except Exception:
            return result
        cfg_path = self._find_config()
        if not cfg_path:
            return result
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            return result
        agents = data.get("agents") if isinstance(data, dict) else None
        if not isinstance(agents, dict):
            return result
        for name, spec in agents.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("enabled") is False:
                continue
            cli = (spec.get("cli") or "").strip()
            if not cli:
                continue
            exec_name = cli.split()[0]
            # Skip MAOP-internal python wrappers (e.g. doc-pipeline, MAOP).
            if exec_name.lower() in ("python", "python3", "python.exe"):
                continue
            if os.name == "nt":
                exec_name = exec_name[:-4] if exec_name.lower().endswith((".cmd", ".exe", ".bat")) else exec_name
            caps = spec.get("capabilities") or []
            desc = spec.get("description") or ""
            result[name] = (exec_name, list(caps), desc)
        return result

    def check_agent(self, name: str) -> ScannedAgent | None:
        """Re-check a single agent's availability."""
        known = next((k for k in KNOWN_AGENTS if k.name == name), None)
        if known is None:
            return None

        cli_path = ""
        for cli_name in known.cli_names:
            cli_path = self._find_cli(cli_name) or ""
            if cli_path:
                break

        now = datetime.now(timezone.utc).isoformat()
        if not cli_path:
            agent = ScannedAgent(
                name=known.name, source=AgentSource.SCANNED,
                status=AgentStatus.UNAVAILABLE, provider=known.provider,
                description=known.description, capabilities=known.capabilities,
                last_checked=now,
            )
        else:
            version = self._probe_version(cli_path, known.version_args)
            agent = ScannedAgent(
                name=known.name, cli_path=cli_path, version=version,
                source=AgentSource.SCANNED, status=AgentStatus.AVAILABLE,
                provider=known.provider, description=known.description,
                capabilities=known.capabilities, last_checked=now,
            )

        self._upsert_db(agent)
        return agent

    def register_manual(
        self,
        name: str,
        cli_path: str,
        *,
        provider: str = "",
        capabilities: list[str] | None = None,
        description: str = "",
        model: str = "",
        driver: str = "cli",
        cli_args: str = "",
        timeout_s: int = 120,
    ) -> ScannedAgent:
        """Manually register an agent CLI."""
        now = datetime.now(timezone.utc).isoformat()
        version = self._probe_version(cli_path, ["--version"]) if cli_path else ""
        agent = ScannedAgent(
            name=name, cli_path=cli_path, version=version,
            source=AgentSource.MANUAL, status=AgentStatus.AVAILABLE,
            provider=provider, description=description,
            capabilities=capabilities or [], model=model,
            driver=driver, cli_args=cli_args, timeout_s=timeout_s,
            last_checked=now,
        )
        self._upsert_db(agent)
        return agent

    def unregister(self, name: str) -> bool:
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM scanned_agents WHERE name=?", (name,))
        return cursor.rowcount > 0

    def list_agents(self, status: AgentStatus | None = None) -> list[ScannedAgent]:
        with sqlite_connect(self._db_path) as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM scanned_agents WHERE status=? ORDER BY name",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM scanned_agents ORDER BY name").fetchall()
        return [self._row_to_agent(r) for r in rows]

    def get_agent(self, name: str) -> ScannedAgent | None:
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM scanned_agents WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return self._row_to_agent(row)

    def _upsert_db(self, agent: ScannedAgent) -> None:
        import json
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scanned_agents
                   (name, cli_path, version, source, status, capabilities,
                    provider, description, model, timeout_s, driver, cli_args,
                    last_checked, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (agent.name, agent.cli_path, agent.version, agent.source.value,
                 agent.status.value, json.dumps(agent.capabilities),
                 agent.provider, agent.description, agent.model,
                 agent.timeout_s, agent.driver, agent.cli_args,
                 agent.last_checked, agent.error),
            )

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> ScannedAgent:
        import json
        capabilities: list[Any] = []
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            capabilities = json.loads(row["capabilities"]) if row["capabilities"] else []
        return ScannedAgent(
            name=row["name"], cli_path=row["cli_path"], version=row["version"],
            source=AgentSource(row["source"]), status=AgentStatus(row["status"]),
            capabilities=capabilities, provider=row["provider"],
            description=row["description"], model=row["model"],
            timeout_s=row["timeout_s"], driver=row["driver"],
            cli_args=row["cli_args"], last_checked=row["last_checked"],
            error=row["error"],
        )
