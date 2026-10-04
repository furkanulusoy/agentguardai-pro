"""
Persistent connector quarantine state.

AgentGuard.quarantined is, by default, in-memory only: a process restart
silently un-quarantines a connector that was just caught doing something
undeclared (see docs/ROADMAP_TO_PRODUCTION.md -- "if AgentGuard is down
or wedged... fail-closed by accident, not by verified design"). That's a
real gap: a compromised/malicious connector should stay quarantined
across a restart, deploy, or crash, until a human explicitly clears it.

SqliteQuarantineStore closes that gap. It's deliberately stdlib-only
(sqlite3), matching the rest of agentguard/ -- no new dependency for a
feature this small. A connector, once quarantined, stays quarantined
until `clear()` is called explicitly (e.g. via `agentguard quarantine
clear <connector>`), never by a restart resetting in-memory state.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class SqliteQuarantineStore:
    """A small SQLite-backed table of currently-quarantined connector
    names, safe to share across threads (guarded by a lock; sqlite3's own
    per-connection thread-safety is disabled via check_same_thread=False
    since Flask's dev server serves requests on multiple threads).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS quarantine ("
                "connector_name TEXT PRIMARY KEY, "
                "quarantined_at REAL NOT NULL, "
                "reason TEXT NOT NULL"
                ")"
            )
            self._conn.commit()

    def is_quarantined(self, connector_name: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM quarantine WHERE connector_name = ?", (connector_name,)
            ).fetchone()
        return row is not None

    def quarantine(self, connector_name: str, reason: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO quarantine (connector_name, quarantined_at, reason) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(connector_name) DO UPDATE SET "
                "quarantined_at=excluded.quarantined_at, reason=excluded.reason",
                (connector_name, time.time(), reason),
            )
            self._conn.commit()

    def clear(self, connector_name: str) -> bool:
        """Returns True if a quarantine record existed and was removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM quarantine WHERE connector_name = ?", (connector_name,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def list_quarantined(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT connector_name, quarantined_at, reason FROM quarantine "
                "ORDER BY quarantined_at DESC"
            ).fetchall()
        return [{"connector_name": r[0], "quarantined_at": r[1], "reason": r[2]} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteQuarantineStore:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
