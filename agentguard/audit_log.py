"""
Structured audit logging for AgentGuard.

Every GuardEvent AgentGuard produces (allowed / blocked_scope /
approval_requested / approval_denied / anomaly_detected / quarantined /
blocked_quarantine) can optionally be appended to a JSON Lines file --
one JSON object per line, so it's directly ingestible by a SIEM, `jq`,
or any log pipeline, and diffable/greppable as plain text in the
meantime.

This is stdlib-only on purpose (json + pathlib + time), matching the
rest of agentguard/. It is NOT a claim that file-based JSONL logging is
sufficient for a real production audit trail -- see
docs/ROADMAP_TO_PRODUCTION.md for what that actually requires (tamper
evidence, retention policy, shipping to a durable/central store, etc.).
This gives you a structured local record to build on.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TextIO

from agentguard.guardrail import GuardEvent


class JsonlAuditLog:
    """
    Append-only JSON Lines audit logger. Pass an instance as
    AgentGuard(..., audit_log=...) and every GuardEvent gets written as
    it happens (not batched -- each write is flushed immediately, so a
    crash right after a sensitive action doesn't lose the record of it).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh: TextIO = self.path.open("a", encoding="utf-8")

    def record(self, connector_name: str, event: GuardEvent) -> None:
        entry = {
            "timestamp": time.time(),
            "connector": connector_name,
            "kind": event.kind,
            "action": event.action,
            "detail": event.detail,
        }
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> JsonlAuditLog:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @staticmethod
    def read_all(path: str | Path) -> list[dict]:
        """Read every entry back out, e.g. for the CLI's `agentguard audit` command."""
        p = Path(path)
        if not p.exists():
            return []
        entries = []
        with p.open(encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if raw_line:
                    entries.append(json.loads(raw_line))
        return entries
