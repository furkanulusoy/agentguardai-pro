"""
Gmail OAuth linking.

GET /connectors/gmail/authorize is protected by our own auth (CurrentUser)
-- a real frontend fetches it via an authenticated AJAX call, then
navigates the browser to the returned authorization_url. The browser's
subsequent hop to Google (and back) can't carry our Authorization
header, which is exactly why `state` exists: it's how the callback
learns which of our users/tenants this belongs to
(infrastructure/auth/jwt.py:create_oauth_state_token/decode_oauth_state_token).

GET /connectors/gmail/callback is NOT protected by CurrentUser -- Google
calls it directly, with no way to attach our bearer token. `state` is
its only proof of identity, and it's signed, so it can't be forged into
linking a credential to someone else's account.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from apps.api.dependencies import DbSession, require_permission
from apps.api.services.oauth import begin_oauth, consume_oauth
from connectors.gmail.oauth import (
    GmailOAuthNotConfigured,
    build_authorization_url,
    exchange_code_for_credential,
    generate_code_verifier,
)
from infrastructure.auth.jwt import create_oauth_state_token, decode_oauth_state_token
from infrastructure.database.models import Credential, User
from infrastructure.secrets import secret_store

router = APIRouter(prefix="/connectors/gmail", tags=["connectors"])


class AuthorizeResponse(BaseModel):
    authorization_url: str


@router.get("/authorize", response_model=AuthorizeResponse)
async def authorize(
    current_user: Annotated[User, Depends(require_permission("connector.write"))],
    session: DbSession,
    response: Response,
) -> AuthorizeResponse:
    code_verifier = generate_code_verifier()
    state = create_oauth_state_token(
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        connector_type="gmail",
        code_verifier=code_verifier,
    )
    try:
        url = build_authorization_url(state, code_verifier)
    except GmailOAuthNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await begin_oauth(session, response, state, current_user)
    return AuthorizeResponse(authorization_url=url)


@router.get("/callback")
async def callback(
    session: DbSession, request: Request, code: str = Query(...), state: str = Query(...)
) -> RedirectResponse:
    claims = decode_oauth_state_token(state)
    if claims is None or claims.connector_type != "gmail" or claims.code_verifier is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired state")

    await consume_oauth(session, request, state, claims)

    try:
        credential_dict = await run_in_threadpool(
            exchange_code_for_credential, code, state, claims.code_verifier
        )
    except GmailOAuthNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google token exchange failed") from exc

    encrypted = secret_store.encrypt(json.dumps(credential_dict))
    credential = Credential(
        tenant_id=claims.tenant_id,
        user_id=claims.user_id,
        connector_type="gmail",
        label="Gmail",
        encrypted_secret=encrypted,
    )
    session.add(credential)
    await session.commit()

    return RedirectResponse("/integrations?connected=gmail", status_code=303)
