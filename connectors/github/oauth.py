"""
Authorization-code OAuth flow for GitHub OAuth Apps (not GitHub Apps --
simpler, user-consent based, matches the shape of connectors/gmail's
flow). No PKCE: GitHub OAuth Apps are a confidential client
(client_secret exchanged server-side only, never in the browser), so
RFC 7636 doesn't apply the way it does to Google's flow -- see
infrastructure/auth/jwt.py's create_oauth_state_token docstring.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from connectors.github.connector import SCOPES
from infrastructure.config import settings

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"


class GitHubOAuthNotConfigured(RuntimeError):
    pass


def _require_config() -> tuple[str, str, str]:
    if not (
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.github_oauth_redirect_uri
    ):
        raise GitHubOAuthNotConfigured(
            "GitHub OAuth is not configured -- set GITHUB_OAUTH_CLIENT_ID/"
            "GITHUB_OAUTH_CLIENT_SECRET/GITHUB_OAUTH_REDIRECT_URI in .env"
        )
    return (
        settings.github_oauth_client_id,
        settings.github_oauth_client_secret,
        settings.github_oauth_redirect_uri,
    )


def build_authorization_url(state: str) -> str:
    client_id, _, redirect_uri = _require_config()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_credential(code: str) -> dict[str, Any]:
    """Real network call to GitHub's token endpoint. Returns the dict
    shape connectors.github.connector.GitHubConnector.authenticate()
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
        headers={"Accept": "application/json"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        reason = data.get("error_description", data["error"])
        raise RuntimeError(f"GitHub token exchange failed: {reason}")
    return {
        "access_token": data["access_token"],
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "bearer"),
    }
