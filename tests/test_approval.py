"""Tests for TerminalApprover, AutoDenyApprover, QueueApprover, and SlackNotifyApprover."""
from __future__ import annotations

import json
import threading
import time
import unittest

from agentguard.approval import (
    AutoDenyApprover,
    QueueApprover,
    SlackNotifyApprover,
    TerminalApprover,
)


class TestTerminalApprover(unittest.TestCase):
    def test_accepts_yes_variants(self):
        for word in ["e", "evet", "y", "yes", "E", "EVET"]:
            approver = TerminalApprover(input_fn=lambda p, w=word: w, print_fn=lambda s: None)
            self.assertTrue(approver("send_message", {"args": (), "kwargs": {}}))

    def test_accepts_no_variants(self):
        for word in ["h", "hayır", "hayir", "n", "no", "H"]:
            approver = TerminalApprover(input_fn=lambda p, w=word: w, print_fn=lambda s: None)
            self.assertFalse(approver("send_message", {"args": (), "kwargs": {}}))

    def test_reprompts_on_invalid_input(self):
        responses = iter(["maybe", "asdf", "evet"])
        approver = TerminalApprover(
            input_fn=lambda p: next(responses), print_fn=lambda s: None
        )
        self.assertTrue(approver("send_message", {"args": (), "kwargs": {}}))

    def test_does_not_print_argument_values(self):
        output = []
        approver = TerminalApprover(input_fn=lambda _: "h", print_fn=output.append)
        approver(
            "send_message",
            {"args": ("private@example.com",), "kwargs": {"token": "top-secret"}},
        )
        rendered = "\n".join(output)
        self.assertNotIn("private@example.com", rendered)
        self.assertNotIn("top-secret", rendered)
        self.assertIn("positional_argument_count=1", rendered)
        self.assertIn("keyword_argument_count=1", rendered)


class TestAutoDenyApprover(unittest.TestCase):
    def test_always_denies(self):
        approver = AutoDenyApprover()
        self.assertFalse(approver("anything", {"args": (), "kwargs": {}}))
        self.assertFalse(approver("delete_all", {"args": (), "kwargs": {}}))


class TestQueueApprover(unittest.TestCase):
    def test_approved_when_resolved_true(self):
        approver = QueueApprover(timeout_seconds=5)
        result = {}

        def call():
            result["approved"] = approver("send_message", {"args": (), "kwargs": {}})

        t = threading.Thread(target=call)
        t.start()
        time.sleep(0.2)
        pending = approver.pending()
        self.assertEqual(len(pending), 1)
        self.assertTrue(approver.resolve(pending[0]["id"], True))
        t.join(timeout=5)
        self.assertTrue(result["approved"])

    def test_denied_when_resolved_false(self):
        approver = QueueApprover(timeout_seconds=5)
        result = {}

        def call():
            result["approved"] = approver("delete_all", {"args": (), "kwargs": {}})

        t = threading.Thread(target=call)
        t.start()
        time.sleep(0.2)
        pending = approver.pending()
        approver.resolve(pending[0]["id"], False)
        t.join(timeout=5)
        self.assertFalse(result["approved"])

    def test_fails_closed_on_timeout(self):
        approver = QueueApprover(timeout_seconds=0.2)
        self.assertFalse(approver("delete_all", {"args": (), "kwargs": {}}))

    def test_resolve_unknown_id_returns_false(self):
        approver = QueueApprover(timeout_seconds=1)
        self.assertFalse(approver.resolve(999, True))

    def test_pending_reflects_action_and_context(self):
        approver = QueueApprover(timeout_seconds=2)
        t = threading.Thread(
            target=lambda: approver("send_message", {"args": ("x@y.com",), "kwargs": {}})
        )
        t.start()
        time.sleep(0.2)
        pending = approver.pending()
        self.assertEqual(pending[0]["action"], "send_message")
        self.assertEqual(pending[0]["ctx"]["args"], ("x@y.com",))
        approver.resolve(pending[0]["id"], False)
        t.join(timeout=5)


class TestSlackNotifyApprover(unittest.TestCase):
    def test_posts_notification_then_delegates_to_wrapped_approver(self):
        posted = []
        approver = SlackNotifyApprover(
            "https://hooks.slack.com/services/fake",
            wrapped=lambda action, ctx: True,
            post_fn=lambda url, body: posted.append((url, body)),
        )
        result = approver("send_message", {"args": ("x@y.com",), "kwargs": {}})

        self.assertTrue(result)
        self.assertEqual(len(posted), 1)
        url, body = posted[0]
        self.assertEqual(url, "https://hooks.slack.com/services/fake")
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("send_message", payload["text"])
        self.assertNotIn("x@y.com", payload["text"])
        self.assertIn("positional_argument_count=1", payload["text"])

    def test_delegates_the_wrapped_approvers_decision(self):
        approver_deny = SlackNotifyApprover(
            "https://hooks.slack.com/services/fake",
            wrapped=lambda action, ctx: False,
            post_fn=lambda url, body: None,
        )
        self.assertFalse(approver_deny("delete_all", {"args": (), "kwargs": {}}))

    def test_failed_slack_post_does_not_block_the_approval_decision(self):
        def _boom(url, body):
            raise ConnectionError("Slack unreachable")

        approver = SlackNotifyApprover(
            "https://hooks.slack.com/services/fake",
            wrapped=lambda action, ctx: True,
            post_fn=_boom,
        )
        # The notification failed, but the wrapped approver's decision must
        # still go through -- a missing Slack message is not a reason to
        # deny (or hang on) a legitimate approval.
        self.assertTrue(approver("send_message", {"args": (), "kwargs": {}}))

    def test_works_with_a_real_queue_approver(self):
        queue = QueueApprover(timeout_seconds=5)
        posted = []
        approver = SlackNotifyApprover(
            "https://hooks.slack.com/services/fake",
            wrapped=queue,
            post_fn=lambda url, body: posted.append(body),
        )
        result = {}

        def call():
            result["approved"] = approver("send_message", {"args": (), "kwargs": {}})

        t = threading.Thread(target=call)
        t.start()
        time.sleep(0.2)
        self.assertEqual(len(posted), 1)  # notification already sent before resolution
        pending = queue.pending()
        queue.resolve(pending[0]["id"], True)
        t.join(timeout=5)
        self.assertTrue(result["approved"])


if __name__ == "__main__":
    unittest.main()
