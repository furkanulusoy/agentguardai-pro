import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from infrastructure.config import settings
from infrastructure.database.models import Role, Tenant, User
from infrastructure.database.models.oauth_transaction import OAuthTransaction


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def begin_oauth(session, response, state, user):
    nonce = secrets.token_urlsafe(32)
    session.add(
        OAuthTransaction(
            state_hash=digest(state),
            nonce_hash=digest(nonce),
            user_id=user.id,
            tenant_id=user.tenant_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    await session.commit()
    response.set_cookie(
        "agentguard_oauth_nonce",
        nonce,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/connectors",
        max_age=600,
    )


async def consume_oauth(session, request, state, claims):
    tx = (
        await session.execute(
            select(OAuthTransaction)
            .where(OAuthTransaction.state_hash == digest(state))
            .with_for_update()
        )
    ).scalar_one_or_none()
    nonce = request.cookies.get("agentguard_oauth_nonce", "")
    if (
        tx is None
        or tx.used_at
        or tx.expires_at <= datetime.now(timezone.utc)
        or not secrets.compare_digest(tx.nonce_hash, digest(nonce))
        or tx.user_id != claims.user_id
        or tx.tenant_id != claims.tenant_id
    ):
        raise HTTPException(400, "Invalid OAuth transaction")
    user = (
        await session.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(User.id == tx.user_id)
        )
    ).scalar_one_or_none()
    tenant = await session.get(Tenant, tx.tenant_id)
    if (
        user is None
        or not user.is_active
        or user.tenant_id != tx.tenant_id
        or tenant is None
        or not tenant.is_active
        or "connector.write" not in {p.code for r in user.roles for p in r.permissions}
    ):
        raise HTTPException(403, "Connector linking is no longer authorized")
    tx.used_at = datetime.now(timezone.utc)
    await session.commit()
