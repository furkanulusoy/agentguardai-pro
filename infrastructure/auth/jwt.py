"""
Access tokens are real JWTs (HS256, signed with JWT_SECRET_KEY) --
stateless, verified without a database round trip. Refresh tokens are
NOT JWTs: they're opaque random strings, because they need to be
revocable/rotatable, which requires a database row to revoke. Putting
"is this refresh token still valid" logic in a signed-but-unrevokable
JWT would defeat the point of rotation.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from infrastructure.config import settings
from infrastructure.secrets import secret_store

_ALGORITHM = "HS256"


@dataclass
class AccessTokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    expires_at: datetime
    auth_version: int = 0


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID, auth_version: int = 0) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "exp": expires_at,
        "type": "access",
        "auth_version": auth_version,
        # RFC 7519's unique-per-token identifier. Without this, two
        # tokens issued for the same user within the same second (e.g.
        # login immediately followed by a refresh) are byte-for-byte
        # identical -- not a security issue on its own (still short-lived
        # and stateless), but jti gives every token a stable identity for
        # logging/tracing and is standard practice.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> AccessTokenClaims | None:
    """Returns None for any invalid/expired/wrong-type token -- callers
    should treat that as "not authenticated," never raise past this."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "tenant_id", "exp", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            auth_version=int(payload.get("auth_version", 0)),
        )
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None


def create_oauth_state_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    connector_type: str,
    code_verifier: str | None = None,
) -> str:
    """A short-lived, signed `state` value for an OAuth authorization-
    code flow (e.g. connectors/gmail's or connectors/github's
    authorize/callback pair). A browser navigating to the provider's
    consent screen and back can't carry an Authorization header, so
    `state` is how the callback learns which of our users/tenants
    initiated the request -- signed so it can't be forged into linking
    a credential to someone else's account.

    code_verifier is optional: Google's flow needs PKCE (a public-ish
    client, per Google's own recommendation) so /authorize and
    /callback -- two separate HTTP requests, each building its own Flow
    object -- need a way to share the verifier the first one generated,
    or the token exchange fails with "Missing code verifier" (found by
    actually running the flow, not by inspection). GitHub's OAuth Apps
    are a confidential client (client_secret only ever used server-side)
    so RFC 7636 PKCE doesn't apply there; connectors/github/oauth.py
    passes None.

    When present, the verifier is encrypted (infrastructure/secrets, the
    same Fernet store credentials are already encrypted with) before
    going into the payload, not left as a plain JWT claim -- a JWT's
    signature proves the payload wasn't tampered with, but doesn't hide
    it; this value round-trips through the browser (as part of `state`,
    alongside the provider's own `code` query parameter) on the way back
    from the consent screen, so anything that can observe that redirect
    (browser history, a proxy/CDN access log, a leaked Referer) could
    otherwise read the plaintext verifier -- defeating part of what PKCE
    is for. Found during an independent security review, not by this
    code failing a real OAuth flow.
    """
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "connector_type": connector_type,
        "code_verifier": secret_store.encrypt(code_verifier) if code_verifier else None,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        "type": "oauth_state",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


@dataclass
class OAuthStateClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    connector_type: str
    code_verifier: str | None


def decode_oauth_state_token(token: str) -> OAuthStateClaims | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "tenant_id", "exp", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "oauth_state":
        return None
    try:
        encrypted_verifier = payload.get("code_verifier")
        return OAuthStateClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            connector_type=payload["connector_type"],
            code_verifier=secret_store.decrypt(encrypted_verifier) if encrypted_verifier else None,
        )
    except (ValueError, TypeError, KeyError):
        return None


def generate_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, token_hash). The raw value is handed to the
    client once and never stored; only its hash is persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
