"""
Authorization-code OAuth flow for a Slack app (OAuth v2, bot-token
scopes only -- no user_scope requested). Same shape as
connectors/github/oauth.py: no PKCE, since a Slack app's client_secret
is exchanged server-side only, never in the browser (see
infrastructure/auth/jwt.py's create_oauth_state_token docstring for
why that matters).

Slack's own OAuth v2 response shape differs from GitHub/Google's flat
one -- it always returns HTTP 200, even on failure (`"ok": false` +
`"error"` instead of a non-2xx status), and nests the workspace under
`"team"` -- exchange_code_for_credential below normalizes both.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from connectors.slack.connector import SCOPES
from infrastructure.config import settings

_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_TOKEN_URL = "https://slack.com/api/oauth.v2.access"


class SlackOAuthNotConfigured(RuntimeError):
    pass


def _require_config() -> tuple[str, str, str]:
    if not (
        settings.slack_oauth_client_id
        and settings.slack_oauth_client_secret
        and settings.slack_oauth_redirect_uri
    ):
        raise SlackOAuthNotConfigured(
            "Slack OAuth is not configured -- set SLACK_OAUTH_CLIENT_ID/"
            "SLACK_OAUTH_CLIENT_SECRET/SLACK_OAUTH_REDIRECT_URI in .env"
        )
    return (
        settings.slack_oauth_client_id,
        settings.slack_oauth_client_secret,
        settings.slack_oauth_redirect_uri,
    )


def build_authorization_url(state: str) -> str:
    client_id, _, redirect_uri = _require_config()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(SCOPES),
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_credential(code: str) -> dict[str, Any]:
    """Real network call to Slack's token endpoint. Returns the dict
    shape connectors.slack.connector.SlackConnector.authenticate()
    expects -- callers encrypt this whole dict (infrastructure/secrets)
    before persisting it, never store it as-is."""
    client_id, client_secret, redirect_uri = _require_config()
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack token exchange failed: {data.get('error', 'unknown error')}")
    return {
        "access_token": data["access_token"],
        "team_id": data.get("team", {}).get("id", ""),
        "team_name": data.get("team", {}).get("name", ""),
        "scope": data.get("scope", ""),
    }
