"""
Default AgentGuard Policy for the Slack connector
(connectors/slack/connector.py). Not a per-tenant, user-editable policy
yet -- see connectors/gmail/policy.py's own note, same limitation.

Three risk tiers, not the two-tier LOW/HIGH split Gmail and GitHub each
used: list_channels is a safe read; send_message is a routine, largely
reversible write (a sent message can itself be deleted, so it's LOW
risk despite being a mutation); create_channel/delete_message/
invite_user each have a real, harder-to-undo consequence (persistent
workspace structure, permanent data loss, or a real person's channel
access) and are gated behind human approval.
"""

from __future__ import annotations

from agentguard.policy import Policy

RISK_LEVELS: dict[str, str] = {
    "list_channels": "LOW",
    "send_message": "LOW",
    "create_channel": "MEDIUM",
    "delete_message": "HIGH",
    "invite_user": "HIGH",
}


def default_slack_policy() -> Policy:
    return Policy(
        connector_name="slack",
        task_scope={
            "list_channels",
            "send_message",
            "create_channel",
            "delete_message",
            "invite_user",
        },
        sensitive_actions={"send_message", "create_channel", "delete_message", "invite_user"},
    )
