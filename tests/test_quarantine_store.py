"""Tests for SqliteQuarantineStore and its wiring into AgentGuard."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentguard import AgentGuard, ConnectorQuarantined, Policy, SqliteQuarantineStore
from agentguard.monitor import CallRecorder
from mock_services.inbox import MockInbox
from mock_services.malicious_connector import AttackerSink, MaliciousConnector


class TestSqliteQuarantineStore(unittest.TestCase):
    def test_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            self.assertFalse(store.is_quarantined("inbox"))
            self.assertEqual(store.list_quarantined(), [])
            store.close()

    def test_quarantine_then_is_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            store.quarantine("inbox", reason="test")
            self.assertTrue(store.is_quarantined("inbox"))
            self.assertFalse(store.is_quarantined("other-connector"))
            store.close()

    def test_clear_removes_quarantine(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            store.quarantine("inbox", reason="test")
            self.assertTrue(store.clear("inbox"))
            self.assertFalse(store.is_quarantined("inbox"))
            store.close()

    def test_clear_on_never_quarantined_connector_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            self.assertFalse(store.clear("never-quarantined"))
            store.close()

    def test_list_quarantined_reflects_reason(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            store.quarantine("inbox", reason="undeclared delete")
            entries = store.list_quarantined()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["connector_name"], "inbox")
            self.assertEqual(entries[0]["reason"], "undeclared delete")
            store.close()

    def test_survives_reopening_the_same_file(self):
        """The core promise: quarantine state outlives the process that set it."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "q.sqlite3"
            store1 = SqliteQuarantineStore(path)
            store1.quarantine("inbox", reason="undeclared delete")
            store1.close()

            # A brand new store instance, same file -- simulates a restart.
            store2 = SqliteQuarantineStore(path)
            self.assertTrue(store2.is_quarantined("inbox"))
            store2.close()


class TestAgentGuardQuarantinePersistence(unittest.TestCase):
    def test_new_guard_restores_prior_quarantine_from_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")
            store.quarantine("inbox", reason="previous run")

            inbox = MockInbox()
            policy = Policy(connector_name="inbox", task_scope={"list_messages"})
            guard = AgentGuard(connector=inbox, policy=policy, quarantine_store=store)

            # Restored as already-quarantined at construction time, before
            # any call() ever runs -- this is what makes it survive a restart.
            self.assertTrue(guard.quarantined)
            with self.assertRaises(ConnectorQuarantined):
                guard.call("list_messages")
            store.close()

    def test_no_store_means_no_restoration_unchanged_default_behavior(self):
        inbox = MockInbox()
        policy = Policy(connector_name="inbox", task_scope={"list_messages"})
        guard = AgentGuard(connector=inbox, policy=policy)  # no quarantine_store at all
        self.assertFalse(guard.quarantined)

    def test_real_quarantine_trigger_is_persisted_to_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteQuarantineStore(Path(d) / "q.sqlite3")

            real_inbox = MockInbox()
            monitored_inbox = CallRecorder(real_inbox, label="inbox")
            sink = AttackerSink()
            connector = MaliciousConnector(monitored_inbox, sink)
            policy = Policy(
                connector_name="inbox", task_scope={"list_messages", "read_message"}
            )
            guard = AgentGuard(
                connector=connector,
                policy=policy,
                monitored_resource=monitored_inbox,
                quarantine_store=store,
            )

            self.assertFalse(store.is_quarantined("inbox"))
            messages = guard.call("list_messages")
            with self.assertRaises(ConnectorQuarantined):
                guard.call("read_message", messages[-1]["id"])

            # The real point of this feature: it's not just in-memory anymore.
            self.assertTrue(store.is_quarantined("inbox"))
            store.close()


if __name__ == "__main__":
    unittest.main()
