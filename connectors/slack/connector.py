"""
Real Slack connector -- slack_sdk.WebClient wraps the actual Slack Web
API. Named in docs/PRODUCTIZATION_ROADMAP.md's Phase E sketch as the
third connector specifically because its risk shape is a genuine step
up from Gmail/GitHub: five actions across three tiers (a safe read, a
routine reversible write, and three actions with a real, harder-to-undo
consequence -- persistent channel creation, permanent message deletion,
and adding a real person to a channel) rather than the two-tier LOW/
HIGH split Gmail and GitHub's first passes each used. See
connectors/slack/policy.py for the tier assignment.
"""

from __future__ import annotations

from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from connectors.base import BaseConnector, ConnectorAuthenticationError, ConnectorError

#: Bot-token scopes only (no user_scope) -- see connectors/slack/oauth.py.
#: channels:manage covers both conversations.create and conversations.invite
#: for public channels; a private-channel equivalent (groups:write) isn't
#: requested here, matching Gmail's "narrowest scope for this first pass"
#: precedent (connectors/gmail/connector.py's own module docstring).
SCOPES = ["channels:read", "chat:write", "channels:manage"]


class SlackConnector(BaseConnector):
    connector_type = "slack"
    supported_actions = frozenset(
        {"list_channels", "send_message", "create_channel", "delete_message", "invite_user"}
    )

    def __init__(self) -> None:
        self._token: str | None = None
        self._client: WebClient | None = None

    def authenticate(self, credential: dict[str, Any]) -> None:
        """`credential` is whatever infrastructure/secrets decrypted --
        shape matches connectors/slack/oauth.py's exchange_code_for_credential()."""
        self._token = credential["access_token"]

    def connect(self) -> None:
        if self._token is None:
            raise RuntimeError("authenticate() must be called before connect()")
        try:
            self._client = WebClient(token=self._token, retry_handlers=[], timeout=20)
        except Exception as exc:
            raise ConnectorAuthenticationError(f"Could not build Slack client: {exc}") from exc

    def disconnect(self) -> None:
        self._client = None

    def validate(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.auth_test().get("ok"))
        except SlackApiError:
            return False

    def list_channels(self, max_results: int = 20) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            result = self._client.conversations_list(limit=max_results, types="public_channel")
            return [
                {"id": c["id"], "name": c["name"], "is_archived": c["is_archived"]}
                for c in result["channels"]
            ]
        except SlackApiError as exc:
            raise ConnectorError(f"Slack list_channels failed: {exc}") from exc

    def send_message(self, channel: str, text: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            result = self._client.chat_postMessage(channel=channel, text=text)
            return {"channel": result["channel"], "ts": result["ts"]}
        except SlackApiError as exc:
            raise ConnectorError(f"Slack send_message failed: {exc}") from exc

    def create_channel(self, name: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            result = self._client.conversations_create(name=name)
            channel = result["channel"]
            return {"id": channel["id"], "name": channel["name"]}
        except SlackApiError as exc:
            raise ConnectorError(f"Slack create_channel failed: {exc}") from exc

    def delete_message(self, channel: str, ts: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            self._client.chat_delete(channel=channel, ts=ts)
            return {"channel": channel, "ts": ts, "deleted": True}
        except SlackApiError as exc:
            raise ConnectorError(f"Slack delete_message failed: {exc}") from exc

    def invite_user(self, channel: str, user_id: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            result = self._client.conversations_invite(channel=channel, users=user_id)
            return {"channel": result["channel"]["id"], "invited": user_id}
        except SlackApiError as exc:
            raise ConnectorError(f"Slack invite_user failed: {exc}") from exc
