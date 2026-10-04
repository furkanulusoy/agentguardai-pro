"""
Slack OAuth linking -- mirrors connectors/github.py's shape exactly
(GET /authorize protected by our own auth, GET /callback not, `state`
is the callback's only proof of identity). No PKCE here either (see
connectors/slack/oauth.py's module docstring for why).
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
from connectors.slack.oauth import (
    SlackOAuthNotConfigured,
    build_authorization_url,
    exchange_code_for_credential,
)
from infrastructure.auth.jwt import create_oauth_state_token, decode_oauth_state_token
from infrastructure.database.models import Credential, User
from infrastructure.secrets import secret_store

router = APIRouter(prefix="/connectors/slack", tags=["connectors"])


class AuthorizeResponse(BaseModel):
    authorization_url: str


@router.get("/authorize", response_model=AuthorizeResponse)
async def authorize(
    current_user: Annotated[User, Depends(require_permission("connector.write"))],
    session: DbSession,
    response: Response,
) -> AuthorizeResponse:
    state = create_oauth_state_token(
        user_id=current_user.id, tenant_id=current_user.tenant_id, connector_type="slack"
    )
    try:
        url = build_authorization_url(state)
    except SlackOAuthNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    await begin_oauth(session, response, state, current_user)
    return AuthorizeResponse(authorization_url=url)


@router.get("/callback")
async def callback(
    session: DbSession, request: Request, code: str = Query(...), state: str = Query(...)
) -> RedirectResponse:
    claims = decode_oauth_state_token(state)
    if claims is None or claims.connector_type != "slack":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired state")

    await consume_oauth(session, request, state, claims)

    try:
        credential_dict = await run_in_threadpool(exchange_code_for_credential, code)
    except SlackOAuthNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Slack token exchange failed") from exc

    encrypted = secret_store.encrypt(json.dumps(credential_dict))
    credential = Credential(
        tenant_id=claims.tenant_id,
        user_id=claims.user_id,
        connector_type="slack",
        label=credential_dict.get("team_name") or "Slack",
        encrypted_secret=encrypted,
    )
    session.add(credential)
    await session.commit()

    return RedirectResponse("/integrations?connected=slack", status_code=303)
