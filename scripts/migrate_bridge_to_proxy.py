#!/usr/bin/env python3
"""Migrate agent_bridge_state table to agent_proxy_state.

Usage:
    python scripts/migrate_bridge_to_proxy.py [--db-path PATH]
"""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Check if old table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_bridge_state'"
    )
    if not cursor.fetchone():
        print(f"No agent_bridge_state table in {db_path}")
        conn.close()
        return

    # Check if new table already exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_proxy_state'"
    )
    if cursor.fetchone():
        print(f"agent_proxy_state already exists in {db_path}, skipping")
        conn.close()
        return

    # Rename table
    print(f"Migrating {db_path}: agent_bridge_state → agent_proxy_state")
    cursor.execute("ALTER TABLE agent_bridge_state RENAME TO agent_proxy_state")
    conn.commit()
    conn.close()
    print("Done")


if __name__ == "__main__":
    root = Path(__file__).parent.parent
    data_dir = root / "data"

    # Migrate all .db files
    for db_file in data_dir.glob("*.db"):
        migrate(db_file)

    # Also check agent_bridge.db if it exists
    bridge_db = data_dir / "agent_bridge.db"
    if bridge_db.exists():
        print(f"\nNote: {bridge_db} exists — consider renaming to agent_proxy.db")
