"""Cookie-only browser sessions. Refresh/reset tokens are consumed under row locks."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.dependencies import CurrentUser, DbSession
from apps.api.rate_limit import limiter
from infrastructure.auth.jwt import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_token_expiry,
)
from infrastructure.auth.passwords import hash_password, verify_password
from infrastructure.config import settings
from infrastructure.database.models import (
    Invitation,
    PasswordReset,
    RefreshToken,
    Role,
    Tenant,
    User,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Computed once at import time -- a fixed hash to verify against when no
# user exists, so a failed login always pays the same Argon2 cost (see
# login() below).
_DUMMY_HASH = hash_password(str(uuid.uuid4()))

COOKIE_NAME = "agentguard_refresh_token"
# Scoped to /auth on purpose -- the browser has no reason to send this
# cookie to /approvals, /connectors, etc., which only ever check the
# Authorization header. Smaller exposure than a site-wide cookie for no
# loss of function.
_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, raw_refresh: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        raw_refresh,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=_COOKIE_PATH)


class RegisterRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_slug: str = Field(min_length=1, max_length=63)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)

    @field_validator("tenant_slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                "tenant_slug must be lowercase alphanumeric with hyphens, e.g. 'acme-inc'"
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    # Optional: a browser client sends none of this, relying entirely on
    # COOKIE_NAME; apps/mcp_server/server.py (no cookie jar) still sends
    # the real value here. See the module docstring.
    refresh_token: str | None = None


class TokenPairResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    tenant_id: uuid.UUID
    roles: list[str]
    permissions: list[str]


async def _issue_token_pair(
    session: AsyncSession, user: User, response: Response
) -> TokenPairResponse:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(401, "Workspace inactive")
    response.headers["Cache-Control"] = "no-store"
    access_token = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, auth_version=user.auth_version
    )
    raw_refresh, refresh_hash = generate_refresh_token()
    session.add(
        RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=refresh_token_expiry())
    )
    await session.commit()
    _set_refresh_cookie(response, raw_refresh)
    return TokenPairResponse(access_token=access_token)


@router.post("/register", response_model=TokenPairResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def register(
    request: Request, body: RegisterRequest, session: DbSession, response: Response
) -> TokenPairResponse:
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    tenant = Tenant(name=body.tenant_name, slug=body.tenant_slug)
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Tenant slug already taken") from exc

    owner_role = (await session.execute(select(Role).where(Role.name == "OWNER"))).scalar_one()
    user = User(tenant_id=tenant.id, email=body.email, hashed_password=hash_password(body.password))
    user.roles.append(owner_role)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The earlier "does this email exist" check (line 91) is only
        # advisory -- two concurrent registrations for the same email can
        # both pass it before either commits. Without this, the loser of
        # that race gets an unhandled 500 instead of the same clean 409
        # the check above was meant to guarantee.
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc

    return await _issue_token_pair(session, user, response)


@router.post("/login", response_model=TokenPairResponse)
@limiter.limit("5/minute")
async def login(
    request: Request, body: LoginRequest, session: DbSession, response: Response
) -> TokenPairResponse:
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # verify_password is ALWAYS called, even when there's no such user --
    # `or`'s short-circuiting would otherwise skip it for a nonexistent
    # email, and Argon2 verification is slow enough (by design) that the
    # response-time difference is a real, measurable side channel: an
    # attacker could tell "no such account" apart from "wrong password"
    # just by timing, even though both return the identical error below.
    password_ok = verify_password(
        body.password, user.hashed_password if user is not None else _DUMMY_HASH
    )
    if user is None or not user.is_active or not password_ok:
        # Same error for "no such user" and "wrong password" -- an
        # attacker probing for valid emails must not be able to tell
        # the difference from the response (content or timing).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return await _issue_token_pair(session, user, response)


@router.post("/refresh", response_model=TokenPairResponse)
@limiter.limit("30/minute")
async def refresh_tokens(
    request: Request, body: RefreshRequest, session: DbSession, response: Response
) -> TokenPairResponse:
    # Body takes priority when explicitly provided (apps/mcp_server/server.py,
    # or a caller under test that wants a SPECIFIC token honored -- httpx's
    # test client shares one cookie jar across every call on the same
    # instance, so "cookie always wins" would silently substitute a
    # fresher cookie for a deliberately-replayed old token in tests).
    # A real browser sends no body at all, so this never matters there --
    # body.refresh_token is always None, and the cookie is used.
    raw_refresh = body.refresh_token or request.cookies.get(COOKIE_NAME)
    if raw_refresh is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    token_hash = hash_refresh_token(raw_refresh)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    stored = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if stored is None or stored.revoked_at is not None or stored.expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    stored.revoked_at = now  # rotation: this exact token can never be used again

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    return await _issue_token_pair(session, user, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshRequest, session: DbSession, request: Request, response: Response
) -> None:
    _clear_refresh_cookie(response)
    # Body takes priority when explicitly provided (apps/mcp_server/server.py,
    # or a caller under test that wants a SPECIFIC token honored -- httpx's
    # test client shares one cookie jar across every call on the same
    # instance, so "cookie always wins" would silently substitute a
    # fresher cookie for a deliberately-replayed old token in tests).
    # A real browser sends no body at all, so this never matters there --
    # body.refresh_token is always None, and the cookie is used.
    raw_refresh = body.refresh_token or request.cookies.get(COOKIE_NAME)
    if raw_refresh is not None:
        token_hash = hash_refresh_token(raw_refresh)
        result = await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        stored = result.scalar_one_or_none()
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = datetime.now(timezone.utc)
            await session.commit()
    # Always 204 whether or not the token existed/was already revoked --
    # an unauthenticated caller must not be able to distinguish those
    # cases via the response.


@router.get("/me", response_model=UserResponse)
async def me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        tenant_id=current_user.tenant_id,
        roles=[r.name for r in current_user.roles],
        permissions=sorted({p.code for r in current_user.roles for p in r.permissions}),
    )


class InvitePreviewResponse(BaseModel):
    email: str
    role: str
    tenant_name: str
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    invite_token: str
    password: str = Field(min_length=8, max_length=200)


def _invite_token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@router.get("/invites/{token}", response_model=InvitePreviewResponse)
@limiter.limit("20/minute")
async def preview_invite(request: Request, token: str, session: DbSession) -> InvitePreviewResponse:
    """No auth required -- the person looking at this doesn't have an
    account yet. Lets the frontend show "you're invited to join X as Y"
    before asking for a password, without spending the invite."""
    result = await session.execute(
        select(Invitation)
        .options(selectinload(Invitation.role), selectinload(Invitation.tenant))
        .where(Invitation.token_hash == _invite_token_hash(token))
    )
    invite = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if invite is None or invite.accepted_at is not None or invite.expires_at < now:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Geçersiz veya süresi dolmuş davet")

    return InvitePreviewResponse(
        email=invite.email,
        role=invite.role.name,
        tenant_name=invite.tenant.name,
        expires_at=invite.expires_at,
    )


@router.post(
    "/accept-invite", response_model=TokenPairResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("5/hour")
async def accept_invite(
    request: Request, body: AcceptInviteRequest, session: DbSession, response: Response
) -> TokenPairResponse:
    """Creates the invited person's User under the invite's tenant/role --
    never a new tenant. The email comes from the invite record, not the
    request body: accepting a token can only ever create the account it
    was issued for, not a different one someone typing into this form
    picks."""
    result = await session.execute(
        select(Invitation).where(Invitation.token_hash == _invite_token_hash(body.invite_token))
    )
    invite = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if invite is None or invite.accepted_at is not None or invite.expires_at < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz veya süresi dolmuş davet")

    existing = await session.execute(select(User.id).where(User.email == invite.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu e-posta zaten bir hesaba ait")

    role = await session.get(Role, invite.role_id)
    assert role is not None  # FK constraint guarantees this
    user = User(
        tenant_id=invite.tenant_id, email=invite.email, hashed_password=hash_password(body.password)
    )
    user.roles.append(role)
    session.add(user)
    invite.accepted_at = now
    try:
        await session.flush()
    except IntegrityError as exc:
        # Same race as register(): two accept-invite calls (or an
        # accept racing a real /register for the same email) can both
        # pass the check above before either commits.
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu e-posta zaten bir hesaba ait") from exc

    return await _issue_token_pair(session, user, response)


class PasswordResetPreviewResponse(BaseModel):
    email: str
    expires_at: datetime


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str = Field(min_length=8, max_length=200)


def _password_reset_token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@router.get("/password-reset/{token}", response_model=PasswordResetPreviewResponse)
@limiter.limit("20/minute")
async def preview_password_reset(
    request: Request, token: str, session: DbSession
) -> PasswordResetPreviewResponse:
    """No auth required -- same reasoning as preview_invite: the person
    looking at this hasn't proven who they are yet."""
    result = await session.execute(
        select(PasswordReset)
        .options(selectinload(PasswordReset.user))
        .where(PasswordReset.token_hash == _password_reset_token_hash(token))
    )
    reset = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if reset is None or reset.used_at is not None or reset.expires_at < now:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Geçersiz veya süresi dolmuş bağlantı")

    return PasswordResetPreviewResponse(email=reset.user.email, expires_at=reset.expires_at)


@router.post("/reset-password", response_model=TokenPairResponse)
@limiter.limit("5/hour")
async def reset_password(
    request: Request, body: ResetPasswordRequest, session: DbSession, response: Response
) -> TokenPairResponse:
    result = await session.execute(
        select(PasswordReset)
        .where(PasswordReset.token_hash == _password_reset_token_hash(body.reset_token))
        .with_for_update()
    )
    reset = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if reset is None or reset.used_at is not None or reset.expires_at < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz veya süresi dolmuş bağlantı")

    user = await session.get(User, reset.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Geçersiz veya süresi dolmuş bağlantı")

    user.hashed_password = hash_password(body.new_password)
    reset.used_at = now
    user.auth_version += 1
    await session.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    # A password reset invalidates every existing session -- whoever had
    # the old password (or a stolen refresh token/cookie) must not still
    # be able to act as this user afterward.
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()

    return await _issue_token_pair(session, user, response)
