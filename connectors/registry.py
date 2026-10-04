"""
Maps Credential.connector_type (infrastructure/database/models/credential.py)
to a connector class and its default AgentGuard Policy. Every connector
the platform can actually execute an action against goes here -- three
entries today (Gmail, GitHub, Slack); Outlook/SMS each add one more
entry when they're built, not a new dispatch mechanism.
"""
from __future__ import annotations

from collections.abc import Callable

from agentguard.policy import Policy
from connectors.base import BaseConnector
from connectors.github.connector import GitHubConnector
from connectors.github.policy import RISK_LEVELS as _GITHUB_RISK_LEVELS
from connectors.github.policy import default_github_policy
from connectors.gmail.connector import GmailConnector
from connectors.gmail.policy import RISK_LEVELS as _GMAIL_RISK_LEVELS
from connectors.gmail.policy import default_gmail_policy
from connectors.slack.connector import SlackConnector
from connectors.slack.policy import RISK_LEVELS as _SLACK_RISK_LEVELS
from connectors.slack.policy import default_slack_policy

CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {
    "gmail": GmailConnector,
    "github": GitHubConnector,
    "slack": SlackConnector,
}

POLICY_FACTORIES: dict[str, Callable[[], Policy]] = {
    "gmail": default_gmail_policy,
    "github": default_github_policy,
    "slack": default_slack_policy,
}

RISK_LEVELS: dict[str, dict[str, str]] = {
    "gmail": _GMAIL_RISK_LEVELS,
    "github": _GITHUB_RISK_LEVELS,
    "slack": _SLACK_RISK_LEVELS,
}
