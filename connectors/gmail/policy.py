"""
Default AgentGuard Policy for the Gmail connector
(connectors/gmail/connector.py). Not a per-tenant, user-editable policy
yet -- that's real future work (see docs/ROADMAP_TO_PRODUCTION.md); this
is the one fixed policy every tenant's Gmail connector runs under
today. Deliberate choice, not an oversight: listing message metadata
(sender/subject headers only, via connector.list_messages) is low
risk; reading a message's actual content
(connector.read_message) is where sensitive content actually flows, so
that is the action gated behind human approval for this first pass.
"""
from __future__ import annotations

from agentguard.policy import Policy

RISK_LEVELS: dict[str, str] = {
    "list_messages": "LOW",
    "read_message": "MEDIUM",
}


def default_gmail_policy() -> Policy:
    return Policy(
        connector_name="gmail",
        task_scope={"list_messages", "read_message"},
        sensitive_actions={"read_message"},
    )
