"""Tests for the agentguard CLI (agentguard/cli.py). Skipped if click isn't installed."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from click.testing import CliRunner

    CLICK_AVAILABLE = True
except ImportError:
    CLICK_AVAILABLE = False


@unittest.skipUnless(CLICK_AVAILABLE, "click not installed (pip install agentguard[cli])")
class TestCli(unittest.TestCase):
    def setUp(self):
        from agentguard.cli import cli

        self.cli = cli
        self.runner = CliRunner()
        self.repo_root = Path(__file__).resolve().parent.parent

    def test_help(self):
        result = self.runner.invoke(self.cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("AgentGuard", result.output)

    def test_policy_check_valid_json(self):
        path = self.repo_root / "examples" / "policies" / "inbox.json"
        result = self.runner.invoke(self.cli, ["policy-check", str(path)])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("OK", result.output)
        self.assertIn("inbox", result.output)

    def test_policy_check_invalid_file(self):
        with self.runner.isolated_filesystem():
            Path("bad.json").write_text('{"task_scope": ["a"]}', encoding="utf-8")
            result = self.runner.invoke(self.cli, ["policy-check", "bad.json"])
        self.assertNotEqual(result.exit_code, 0)

    def test_demo_scenario_a_runs(self):
        result = self.runner.invoke(self.cli, ["demo", "--scenario", "a"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("SENARYO A", result.output)

    def test_audit_on_empty_log(self):
        with self.runner.isolated_filesystem():
            Path("empty.jsonl").write_text("", encoding="utf-8")
            result = self.runner.invoke(self.cli, ["audit", "empty.jsonl"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Kayıt yok", result.output)

    def test_audit_shows_recorded_events(self):
        with self.runner.isolated_filesystem():
            from agentguard.audit_log import JsonlAuditLog
            from agentguard.guardrail import GuardEvent

            log = JsonlAuditLog("audit.jsonl")
            log.record("inbox", GuardEvent("allowed", "list_messages"))
            log.close()
            result = self.runner.invoke(self.cli, ["audit", "audit.jsonl"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("allowed", result.output)
        self.assertIn("inbox.list_messages", result.output)

    def test_quarantine_list_on_fresh_db(self):
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(self.cli, ["quarantine", "list", "q.sqlite3"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Karantinada bağlayıcı yok", result.output)

    def test_quarantine_list_shows_quarantined_connector(self):
        with self.runner.isolated_filesystem():
            from agentguard.quarantine_store import SqliteQuarantineStore

            store = SqliteQuarantineStore("q.sqlite3")
            store.quarantine("inbox", reason="undeclared delete")
            store.close()
            result = self.runner.invoke(self.cli, ["quarantine", "list", "q.sqlite3"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("inbox", result.output)
        self.assertIn("undeclared delete", result.output)

    def test_quarantine_clear_lifts_quarantine(self):
        with self.runner.isolated_filesystem():
            from agentguard.quarantine_store import SqliteQuarantineStore

            store = SqliteQuarantineStore("q.sqlite3")
            store.quarantine("inbox", reason="undeclared delete")
            store.close()

            result = self.runner.invoke(self.cli, ["quarantine", "clear", "q.sqlite3", "inbox"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("karantinadan çıkarıldı", result.output)

            store2 = SqliteQuarantineStore("q.sqlite3")
            self.assertFalse(store2.is_quarantined("inbox"))
            store2.close()

    def test_quarantine_clear_on_unquarantined_connector(self):
        with self.runner.isolated_filesystem():
            result = self.runner.invoke(self.cli, ["quarantine", "clear", "q.sqlite3", "inbox"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("zaten karantinada değildi", result.output)


if __name__ == "__main__":
    unittest.main()
