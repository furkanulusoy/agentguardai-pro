"""
Authorization-code OAuth flow for Gmail, via google-auth-oauthlib's
`Flow` (Google's own recommended way to do this from a web app -- not
hand-rolled). Two halves: build_authorization_url() (the "connect
Gmail" link) and exchange_code_for_credential() (what the callback
route calls once Google redirects back with a `code`).
"""
from __future__ import annotations

import secrets
from typing import Any

from google_auth_oauthlib.flow import Flow

from connectors.gmail.connector import SCOPES
from infrastructure.config import settings


class GmailOAuthNotConfigured(RuntimeError):
    pass


def _build_flow(state: str, code_verifier: str) -> Flow:
    if not (
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_redirect_uri
    ):
        raise GmailOAuthNotConfigured(
            "Gmail OAuth is not configured -- set GOOGLE_OAUTH_CLIENT_ID/"
            "GOOGLE_OAUTH_CLIENT_SECRET/GOOGLE_OAUTH_REDIRECT_URI in .env"
        )
    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }
    # code_verifier passed explicitly (not auto-generated) -- /authorize
    # and /callback are two separate requests, each building its own Flow;
    # without a shared, caller-supplied verifier the second Flow has none,
    # and Google's token endpoint rejects the exchange with "Missing code
    # verifier" (this exact failure was hit and is why this isn't
    # left to autogenerate_code_verifier's default).
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        redirect_uri=settings.google_oauth_redirect_uri,
        code_verifier=code_verifier,
    )


def generate_code_verifier() -> str:
    """RFC 7636 PKCE code_verifier: 43-128 chars from the URL-safe
    unreserved set. token_urlsafe(64) -> 86 chars from [A-Za-z0-9_-],
    a subset of the allowed charset, well within the length bounds."""
    return secrets.token_urlsafe(64)


def build_authorization_url(state: str, code_verifier: str) -> str:
    flow = _build_flow(state, code_verifier)
    url, _ = flow.authorization_url(
        access_type="offline",  # request a refresh_token, not just a short-lived access token
        include_granted_scopes="true",
        prompt="consent",  # force a refresh_token even if this user consented before
        state=state,
    )
    return url


def exchange_code_for_credential(code: str, state: str, code_verifier: str) -> dict[str, Any]:
    """Real network call to Google's token endpoint. Returns the dict
    shape connectors.gmail.connector.GmailConnector.authenticate() expects
    -- callers encrypt this whole dict (infrastructure/secrets) before
    persisting it, never store it as-is."""
    flow = _build_flow(state, code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
