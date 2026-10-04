"""
Default AgentGuard Policy for the GitHub connector
(connectors/github/connector.py). Not a per-tenant, user-editable
policy yet -- see connectors/gmail/policy.py's own note, same
limitation. list_repos is LOW risk and auto-approved; close_issue is
HIGH risk (a real, visible, moderately-consequential mutation on a
shared resource) and gated behind human approval.
"""
from __future__ import annotations

from agentguard.policy import Policy

RISK_LEVELS: dict[str, str] = {
    "list_repos": "LOW",
    "close_issue": "HIGH",
}


def default_github_policy() -> Policy:
    return Policy(
        connector_name="github",
        task_scope={"list_repos", "close_issue"},
        sensitive_actions={"close_issue"},
    )
