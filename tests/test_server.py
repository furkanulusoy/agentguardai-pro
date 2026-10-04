"""
End-to-end tests for the Flask reference server (server/app.py), including
the concurrent blocking-approval flow: one thread posts a sensitive tool
call and blocks, another thread resolves it via a separate request, same
as a real Slack-button webhook would.

Skipped automatically if Flask isn't installed (it's an optional extra:
pip install agentguard[server]).
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import flask  # noqa: F401

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "flask not installed (pip install agentguard[server])")
class TestReferenceServer(unittest.TestCase):
    def setUp(self):
        from server.app import create_app

        self.audit_path = Path(tempfile.gettempdir()) / "agentguard_test_server_audit.jsonl"
        self.quarantine_db_path = (
            Path(tempfile.gettempdir()) / "agentguard_test_server_quarantine.sqlite3"
        )
        if self.audit_path.exists():
            self.audit_path.unlink()
        if self.quarantine_db_path.exists():
            self.quarantine_db_path.unlink()
        self.app = create_app(
            audit_log_path=self.audit_path, quarantine_db_path=self.quarantine_db_path
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.config["AUDIT_LOG"].close()
        self.app.config["QUARANTINE_STORE"].close()
        if self.audit_path.exists():
            self.audit_path.unlink()
        if self.quarantine_db_path.exists():
            self.quarantine_db_path.unlink()

    def test_in_scope_read_succeeds(self):
        r = self.client.post("/tools/list_messages", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["result"]), 4)

    def test_out_of_scope_action_is_blocked(self):
        r = self.client.post("/tools/delete_all", json={})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "permission_denied")

    def test_sensitive_action_blocks_until_approved(self):
        result = {}

        def send_request():
            client = self.app.test_client()
            r = client.post(
                "/tools/send_message",
                json={"args": ["teammate@company.example", "hi", "hello"]},
            )
            result["status"] = r.status_code

        t = threading.Thread(target=send_request)
        t.start()
        time.sleep(0.3)  # let the request actually reach the blocking approval wait

        pending = self.client.get("/approvals/pending").get_json()
        self.assertEqual(len(pending), 1)

        resolve = self.client.post(f"/approvals/{pending[0]['id']}", json={"approved": True})
        self.assertEqual(resolve.status_code, 200)

        t.join(timeout=5)
        self.assertEqual(result.get("status"), 200)

    def test_sensitive_action_denied_returns_403(self):
        result = {}

        def send_request():
            client = self.app.test_client()
            r = client.post(
                "/tools/send_message",
                json={"args": ["teammate@company.example", "hi", "hello"]},
            )
            result["status"] = r.status_code

        t = threading.Thread(target=send_request)
        t.start()
        time.sleep(0.3)

        pending = self.client.get("/approvals/pending").get_json()
        self.client.post(f"/approvals/{pending[0]['id']}", json={"approved": False})
        t.join(timeout=5)

        self.assertEqual(result.get("status"), 403)

    def test_audit_log_records_events(self):
        self.client.post("/tools/list_messages", json={})
        self.client.post("/tools/delete_all", json={})
        audit = self.client.get("/audit").get_json()
        self.assertGreaterEqual(len(audit), 2)
        kinds = {entry["kind"] for entry in audit}
        self.assertIn("allowed", kinds)
        self.assertIn("blocked_scope", kinds)

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")


@unittest.skipUnless(FLASK_AVAILABLE, "flask not installed (pip install agentguard[server])")
class TestReferenceServerWithSlack(unittest.TestCase):
    """create_app(slack_webhook_url=...) end to end -- the real network call
    (agentguard.approval._post_to_slack) is patched out; everything else
    (Flask, AgentGuard, QueueApprover, the actual /tools and /approvals
    routes) is real."""

    def setUp(self):
        from server.app import create_app

        self.audit_path = Path(tempfile.gettempdir()) / "agentguard_test_server_slack_audit.jsonl"
        self.quarantine_db_path = (
            Path(tempfile.gettempdir()) / "agentguard_test_server_slack_quarantine.sqlite3"
        )
        if self.audit_path.exists():
            self.audit_path.unlink()
        if self.quarantine_db_path.exists():
            self.quarantine_db_path.unlink()
        self._patcher = patch("agentguard.approval._post_to_slack")
        self.mock_post = self._patcher.start()
        self.app = create_app(
            audit_log_path=self.audit_path,
            quarantine_db_path=self.quarantine_db_path,
            slack_webhook_url="https://hooks.slack.com/services/fake",
            # Short on purpose: if a future regression makes this block for
            # real, the test fails fast instead of hanging for the 120s
            # default (see the leaked-thread incident that motivated this).
            approval_timeout_seconds=2.0,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.config["AUDIT_LOG"].close()
        self.app.config["QUARANTINE_STORE"].close()
        if self.audit_path.exists():
            self.audit_path.unlink()
        if self.quarantine_db_path.exists():
            self.quarantine_db_path.unlink()
        self._patcher.stop()

    def test_sensitive_action_notifies_slack_and_still_resolves_via_api(self):
        result = {}

        def send_request():
            client = self.app.test_client()
            r = client.post(
                "/tools/send_message",
                json={"args": ["teammate@company.example", "hi", "hello"]},
            )
            result["status"] = r.status_code

        t = threading.Thread(target=send_request)
        t.start()
        try:
            time.sleep(0.3)

            # The Slack notification was attempted for this sensitive action.
            self.assertEqual(self.mock_post.call_count, 1)
            webhook_url = self.mock_post.call_args[0][0]
            self.assertEqual(webhook_url, "https://hooks.slack.com/services/fake")

            # Approval itself still happens through the existing API, not a Slack button.
            pending = self.client.get("/approvals/pending").get_json()
            self.assertEqual(len(pending), 1)
            resolve = self.client.post(f"/approvals/{pending[0]['id']}", json={"approved": True})
            self.assertEqual(resolve.status_code, 200)
        finally:
            # Always joined, even on assertion failure, so a broken test
            # can't leave a thread blocked on the approval wait behind it.
            t.join(timeout=5)
        self.assertEqual(result.get("status"), 200)

    def test_in_scope_non_sensitive_call_never_touches_slack(self):
        r = self.client.post("/tools/list_messages", json={})
        self.assertEqual(r.status_code, 200)
        self.mock_post.assert_not_called()


@unittest.skipUnless(FLASK_AVAILABLE, "flask not installed (pip install agentguard[server])")
class TestReferenceServerQuarantinePersistence(unittest.TestCase):
    """Proves quarantine state survives a restart of the reference server --
    two separate create_app() calls sharing one quarantine_db_path, which is
    exactly what two runs of `python3 -m server.app` (with a persistent
    quarantine_db_path) would look like."""

    def setUp(self):
        self.audit_path = Path(tempfile.gettempdir()) / "agentguard_test_qp_audit.jsonl"
        self.quarantine_db_path = (
            Path(tempfile.gettempdir()) / "agentguard_test_qp_quarantine.sqlite3"
        )
        for p in (self.audit_path, self.quarantine_db_path):
            if p.exists():
                p.unlink()

    def tearDown(self):
        for p in (self.audit_path, self.quarantine_db_path):
            if p.exists():
                p.unlink()

    def test_quarantine_set_in_one_app_instance_is_seen_by_a_fresh_one(self):
        from server.app import create_app

        app_kwargs = dict(
            audit_log_path=self.audit_path, quarantine_db_path=self.quarantine_db_path
        )
        app1 = create_app(**app_kwargs)
        try:
            self.assertFalse(app1.config["GUARD"].quarantined)
            self.assertEqual(app1.test_client().get("/healthz").get_json()["quarantined"], False)
            # Simulate the guard having quarantined the connector during app1's
            # lifetime (the real trigger path is exercised in test_quarantine_store.py).
            app1.config["QUARANTINE_STORE"].quarantine("inbox", reason="simulated for this test")
        finally:
            app1.config["AUDIT_LOG"].close()
            app1.config["QUARANTINE_STORE"].close()

        # A brand new app instance -- same quarantine_db_path, nothing else shared.
        app2 = create_app(**app_kwargs)
        try:
            self.assertTrue(app2.config["GUARD"].quarantined)
            r = app2.test_client().post("/tools/list_messages", json={})
            self.assertEqual(r.status_code, 423)
            self.assertEqual(r.get_json()["error"], "connector_quarantined")
        finally:
            app2.config["AUDIT_LOG"].close()
            app2.config["QUARANTINE_STORE"].close()


@unittest.skipUnless(FLASK_AVAILABLE, "flask not installed (pip install agentguard[server])")
class TestReferenceServerWithApiKey(unittest.TestCase):
    def setUp(self):
        from server.app import create_app

        self.audit_path = Path(tempfile.gettempdir()) / "agentguard_test_apikey_audit.jsonl"
        self.quarantine_db_path = (
            Path(tempfile.gettempdir()) / "agentguard_test_apikey_quarantine.sqlite3"
        )
        for p in (self.audit_path, self.quarantine_db_path):
            if p.exists():
                p.unlink()
        self.app = create_app(
            audit_log_path=self.audit_path,
            quarantine_db_path=self.quarantine_db_path,
            api_key="s3cr3t-test-key",
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.config["AUDIT_LOG"].close()
        self.app.config["QUARANTINE_STORE"].close()
        for p in (self.audit_path, self.quarantine_db_path):
            if p.exists():
                p.unlink()

    def test_tools_call_without_header_is_rejected(self):
        r = self.client.post("/tools/list_messages", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["error"], "unauthorized")

    def test_tools_call_with_wrong_key_is_rejected(self):
        r = self.client.post(
            "/tools/list_messages", json={}, headers={"Authorization": "Bearer wrong-key"}
        )
        self.assertEqual(r.status_code, 401)

    def test_tools_call_with_correct_key_succeeds(self):
        r = self.client.post(
            "/tools/list_messages",
            json={},
            headers={"Authorization": "Bearer s3cr3t-test-key"},
        )
        self.assertEqual(r.status_code, 200)

    def test_approvals_pending_requires_key(self):
        r = self.client.get("/approvals/pending")
        self.assertEqual(r.status_code, 401)
        r_ok = self.client.get(
            "/approvals/pending", headers={"Authorization": "Bearer s3cr3t-test-key"}
        )
        self.assertEqual(r_ok.status_code, 200)

    def test_audit_requires_key(self):
        r = self.client.get("/audit")
        self.assertEqual(r.status_code, 401)
        r_ok = self.client.get("/audit", headers={"Authorization": "Bearer s3cr3t-test-key"})
        self.assertEqual(r_ok.status_code, 200)

    def test_healthz_never_requires_a_key(self):
        # Monitoring/load-balancer probes must be able to reach this unauthenticated.
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)

    def test_malformed_authorization_header_is_rejected(self):
        r = self.client.post(
            "/tools/list_messages", json={}, headers={"Authorization": "s3cr3t-test-key"}
        )
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
