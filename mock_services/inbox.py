"""
Mock Inbox Service
-------------------
A tiny in-memory stand-in for a real email/inbox provider (Gmail, Outlook, ...).
It exists ONLY so the demos in this repo can show what an agent connected to
"an inbox" could do -- nothing here talks to a real mail server, and no real
account is ever touched.

Exposes a small tool surface, similar in spirit to what an MCP connector
would expose to an agent: list_messages, read_message, send_message,
delete_message.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass


@dataclass
class Message:
    id: int
    sender: str
    subject: str
    body: str
    deleted: bool = False


class MockInbox:
    """A deliberately simple, fully local mock of an email inbox."""

    def __init__(self, owner: str = "demo-user@example.com"):
        self.owner = owner
        # Per-instance counter -- each fresh inbox starts its own numbering
        # at 1. (This used to be a class attribute shared across every
        # MockInbox instance in the process, which meant IDs kept climbing
        # across unrelated inboxes/tests instead of resetting per instance.)
        self._id_counter = itertools.count(1)
        self.messages: dict[int, Message] = {}
        self.sent: list[Message] = []
        self._seed()

    def _seed(self):
        seed_data = [
            ("billing@saas-tool.example", "Your invoice is ready", "Invoice #1042 attached."),
            (
                "teammate@company.example",
                "Sprint planning notes",
                "Here's what we agreed on for the sprint.",
            ),
            ("newsletter@devweekly.example", "This week in dev", "Top 5 links for you this week."),
            (
                "client@bigcorp.example",
                "Contract renewal",
                "Please review the attached renewal terms.",
            ),
        ]
        for sender, subject, body in seed_data:
            mid = next(self._id_counter)
            self.messages[mid] = Message(mid, sender, subject, body)

    # --- tool surface exposed to an agent -------------------------------

    def list_messages(self) -> list[dict]:
        return [
            {"id": m.id, "sender": m.sender, "subject": m.subject}
            for m in self.messages.values()
            if not m.deleted
        ]

    def read_message(self, message_id: int) -> dict:
        m = self.messages[message_id]
        return {"id": m.id, "sender": m.sender, "subject": m.subject, "body": m.body}

    def send_message(self, to: str, subject: str, body: str) -> dict:
        mid = next(self._id_counter)
        msg = Message(mid, to, subject, body)
        self.sent.append(msg)
        return {"status": "sent", "id": mid, "to": to, "subject": subject}

    def receive_external_email(self, sender: str, subject: str, body: str) -> dict:
        """
        An email arriving FROM outside, same as any real inbox accepting mail
        from anyone. No scope is required for mail to arrive -- that's what
        makes inbox content an untrusted input channel for whatever reads it.
        """
        mid = next(self._id_counter)
        self.messages[mid] = Message(mid, sender, subject, body)
        return {"status": "received", "id": mid}

    def delete_message(self, message_id: int) -> dict:
        m = self.messages[message_id]
        m.deleted = True
        return {"status": "deleted", "id": message_id}

    def delete_all(self) -> dict:
        count = 0
        for m in self.messages.values():
            if not m.deleted:
                m.deleted = True
                count += 1
        return {"status": "deleted_all", "count": count}

    # --- helpers used only by the demo narration -------------------------

    def remaining_count(self) -> int:
        return sum(1 for m in self.messages.values() if not m.deleted)
