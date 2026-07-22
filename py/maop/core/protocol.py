"""MAOP Protocol System — Dynamic agent communication protocol registry.

Provides:
  - ProtocolRegistry: register/discover agent communication protocols
  - MessageSchema: define and validate message formats between agents
  - Protocol versioning with backward compatibility checks
  - Runtime protocol write: add new protocols without code changes

Usage::

    from maop.core.protocol import ProtocolRegistry

    reg = ProtocolRegistry(root_dir="/path/to/MAOP")
    reg.register("code-review", version="1.0",
        schema={"type": "object", "properties": {"file": {"type": "string"}, "feedback": {"type": "string"}}},
        participants=["reviewer", "coder"])
    msg = reg.validate("code-review", {"file": "main.py", "feedback": "LGTM"})
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maop.core.db_utils import get_db_path, sqlite_connect

logger = logging.getLogger(__name__)


class ProtocolDef(BaseModel):
    name: str
    version: str = "1.0"
    schema_def: dict[str, Any] = Field(default_factory=dict)
    participants: list[str] = Field(default_factory=list)
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


class ProtocolMessage(BaseModel):
    id: str = ""
    protocol: str
    version: str = "1.0"
    sender: str
    recipient: str
    payload: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True
    created_at: str = ""


class ProtocolRegistry:
    """Dynamic agent communication protocol registry with schema validation."""

    def __init__(self, root_dir: str | Path = "data") -> None:
        self._root = Path(root_dir)
        self._db_path = get_db_path("protocol")
        self._cache: dict[str, ProtocolDef] = {}
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite_connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS protocols (
                    name TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '1.0',
                    schema_def TEXT DEFAULT '{}',
                    participants TEXT DEFAULT '[]',
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT DEFAULT '',
                    PRIMARY KEY (name, version)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS protocol_messages (
                    id TEXT PRIMARY KEY,
                    protocol TEXT NOT NULL,
                    version TEXT DEFAULT '1.0',
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    payload TEXT DEFAULT '{}',
                    valid INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_proto_name
                ON protocols(name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pmsg_protocol
                ON protocol_messages(protocol, created_at)
            """)

    def register(
        self,
        name: str,
        version: str = "1.0",
        schema_def: dict[str, Any] | None = None,
        participants: list[str] | None = None,
        description: str = "",
    ) -> ProtocolDef:
        """Register a new protocol or update an existing one."""
        now = datetime.now(timezone.utc).isoformat()
        proto = ProtocolDef(
            name=name, version=version,
            schema_def=schema_def or {},
            participants=participants or [],
            description=description,
            created_at=now, updated_at=now,
        )
        with sqlite_connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT created_at FROM protocols WHERE name=? AND version=?",
                (name, version),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE protocols SET schema_def=?, participants=?, description=?, updated_at=? WHERE name=? AND version=?",
                    (json.dumps(proto.schema_def), json.dumps(proto.participants), proto.description, now, name, version),
                )
                proto.created_at = existing["created_at"]
            else:
                conn.execute(
                    "INSERT INTO protocols (name, version, schema_def, participants, description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (name, version, json.dumps(proto.schema_def), json.dumps(proto.participants), proto.description, proto.created_at, proto.updated_at),
                )
        self._cache[f"{name}:{version}"] = proto
        logger.info("[protocol] Registered %s v%s", name, version)
        return proto

    def unregister(self, name: str, version: str = "1.0") -> bool:
        """Remove a protocol registration."""
        with sqlite_connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM protocols WHERE name=? AND version=?", (name, version))
        self._cache.pop(f"{name}:{version}", None)
        return cursor.rowcount > 0

    def get(self, name: str, version: str = "1.0") -> ProtocolDef | None:
        """Get a protocol definition."""
        cache_key = f"{name}:{version}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        with sqlite_connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM protocols WHERE name=? AND version=?", (name, version),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["schema_def"] = json.loads(d.get("schema_def", "{}"))
        d["participants"] = json.loads(d.get("participants", "[]"))
        proto = ProtocolDef(**d)
        self._cache[cache_key] = proto
        return proto

    def list_protocols(self) -> list[ProtocolDef]:
        """List all registered protocols."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM protocols ORDER BY name, version").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["schema_def"] = json.loads(d.get("schema_def", "{}"))
            d["participants"] = json.loads(d.get("participants", "[]"))
            result.append(ProtocolDef(**d))
        return result

    def list_versions(self, name: str) -> list[str]:
        """List all versions of a protocol."""
        with sqlite_connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT version FROM protocols WHERE name=? ORDER BY version", (name,),
            ).fetchall()
        return [r["version"] for r in rows]

    def validate(self, protocol_name: str, payload: dict[str, Any], version: str = "1.0") -> bool:
        """Validate a payload against a protocol's schema definition.

        Performs basic type checking against the schema_def properties.
        """
        proto = self.get(protocol_name, version)
        if proto is None:
            logger.warning("[protocol] Unknown protocol: %s v%s", protocol_name, version)
            return False
        if not proto.schema_def:
            return True
        props = proto.schema_def.get("properties", {})
        required = proto.schema_def.get("required", [])
        for field in required:
            if field not in payload:
                logger.warning("[protocol] Missing required field '%s' for %s v%s", field, protocol_name, version)
                return False
        for key, spec in props.items():
            if key in payload:
                expected_type = spec.get("type")
                if expected_type and not _check_type(payload[key], expected_type):
                    logger.warning("[protocol] Type mismatch for '%s' in %s v%s", key, protocol_name, version)
                    return False
        return True

    def send_message(
        self,
        protocol: str,
        sender: str,
        recipient: str,
        payload: dict[str, Any] | None = None,
        version: str = "1.0",
    ) -> ProtocolMessage:
        """Send a protocol-validated message between agents."""
        msg_id = f"pmsg-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        data = payload or {}
        valid = self.validate(protocol, data, version)
        msg = ProtocolMessage(
            id=msg_id, protocol=protocol, version=version,
            sender=sender, recipient=recipient,
            payload=data, valid=valid, created_at=now,
        )
        with sqlite_connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO protocol_messages (id, protocol, version, sender, recipient, payload, valid, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (msg.id, msg.protocol, msg.version, msg.sender, msg.recipient,
                 json.dumps(msg.payload), 1 if msg.valid else 0, msg.created_at),
            )
        return msg

    def get_messages(self, recipient: str, protocol: str | None = None, limit: int = 100) -> list[ProtocolMessage]:
        """Get messages for a recipient, optionally filtered by protocol."""
        with sqlite_connect(self._db_path) as conn:
            if protocol:
                rows = conn.execute(
                    "SELECT * FROM protocol_messages WHERE recipient=? AND protocol=? ORDER BY created_at LIMIT ?",
                    (recipient, protocol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM protocol_messages WHERE recipient=? ORDER BY created_at LIMIT ?",
                    (recipient, limit),
                ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.get("payload", "{}"))
            d["valid"] = bool(d.get("valid", 1))
            result.append(ProtocolMessage(**d))
        return result


def _check_type(value: Any, expected: str) -> bool:
    """Basic runtime type check against JSON schema type strings."""
    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "array": list, "object": dict,
    }
    py_type = type_map.get(expected)
    if py_type is None:
        return True
    return isinstance(value, py_type)  # type: ignore[arg-type]
