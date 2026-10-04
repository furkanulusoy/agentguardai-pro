"""Tests for JsonlAuditLog and its wiring into AgentGuard."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentguard import AgentGuard, JsonlAuditLog, PermissionDenied, Policy
from mock_services.inbox import MockInbox


class TestJsonlAuditLog(unittest.TestCase):
    def test_record_and_read_all(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audit.jsonl"
            log = JsonlAuditLog(path)
            from agentguard.guardrail import GuardEvent

            log.record("inbox", GuardEvent("allowed", "list_messages"))
            log.record("inbox", GuardEvent("blocked_scope", "delete_all", "out of scope"))
            log.close()

            entries = JsonlAuditLog.read_all(path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["kind"], "allowed")
        self.assertEqual(entries[1]["detail"], "out of scope")

    def test_read_all_on_missing_file_returns_empty_list(self):
        self.assertEqual(JsonlAuditLog.read_all("/tmp/does-not-exist-agentguard.jsonl"), [])

    def test_wired_into_agentguard_records_real_events(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audit.jsonl"
            inbox = MockInbox()
            policy = Policy(connector_name="inbox", task_scope={"list_messages"})
            log = JsonlAuditLog(path)
            guard = AgentGuard(connector=inbox, policy=policy, audit_log=log)

            guard.call("list_messages")
            with self.assertRaises(PermissionDenied):
                guard.call("delete_all")
            log.close()

            entries = JsonlAuditLog.read_all(path)
            kinds = [e["kind"] for e in entries]
            self.assertIn("allowed", kinds)
            self.assertIn("blocked_scope", kinds)
            # each line must be independently parseable JSON (not e.g. a trailing
            # comma or concatenated objects) -- read_all already proves this by
            # not raising, but assert the file is genuinely line-delimited too
            raw = path.read_text(encoding="utf-8").strip().splitlines()
            for raw_line in raw:
                json.loads(raw_line)  # raises if any single line isn't valid JSON


if __name__ == "__main__":
    unittest.main()
