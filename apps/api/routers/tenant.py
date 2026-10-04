"""
Tenant membership. GET /members proves require_permission() actually
blocks, not just is useful on its own. The invites endpoints close a
real gap: before this, POST /auth/register always created a brand new
tenant + OWNER -- there was no way to add a SECOND person to an
existing one, so a workspace could never grow past its first user (see
CHANGELOG.md).

Invite flow, deliberately not sending real email yet (that's future
work -- a provider integration is a separate decision from "can a
tenant have more than one user at all"): POST /invites returns the raw
token once, for the admin to send however they like; the invited
person previews it via GET /auth/invites/{token} (apps/api/routers/auth.py,
no auth required -- they don't have an account yet) and accepts it via
POST /auth/accept-invite, which creates their User under the SAME
tenant with the role the admin picked, never a new tenant.

POST /members/{user_id}/reset-password is the same shape, for a
locked-out EXISTING teammate rather than a new one -- see
infrastructure/database/models/password_reset.py's docstring for why
this is admin-initiated, not (yet) self-service.

GET/PATCH /settings expose Tenant.notification_repo -- the "owner/repo"
apps/api/services/notifications.py opens a GitHub Issue in when a
sensitive action creates a PENDING ApprovalRequest, using the tenant's
own already-connected GitHub credential.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from apps.api.dependencies import DbSession, require_permission
from apps.api.services.security_events import record_security_event
from infrastructure.database.models import Invitation, PasswordReset, Role, Tenant, User

router = APIRouter(prefix="/tenant", tags=["tenant"])

RequireTenantAdmin = Annotated[User, Depends(require_permission("tenant.admin"))]

# OWNER excluded on purpose: tenant.admin (ADMIN or OWNER) can invite,
# but must not be able to mint a peer OWNER -- that would let a mere
# admin hand out billing.admin/tenant.admin itself, wider than what
# they were granted.
INVITABLE_ROLES = ("VIEWER", "OPERATOR", "APPROVER", "ADMIN")
INVITE_EXPIRY_DAYS = 7


class MemberResponse(BaseModel):
    id: uuid.UUID
    email: str
    roles: list[str]


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    current_user: RequireTenantAdmin, session: DbSession
) -> list[MemberResponse]:
    result = await session.execute(
        select(User)
        .options(selectinload(User.roles))
        .where(User.tenant_id == current_user.tenant_id)
    )
    members = result.scalars().all()
    return [
        MemberResponse(id=m.id, email=m.email, roles=[r.name for r in m.roles]) for m in members
    ]


PASSWORD_RESET_EXPIRY_DAYS = 1  # shorter than an invite -- this grants
# entry to an EXISTING account, not a brand new one


class PasswordResetResponse(BaseModel):
    reset_token: str
    expires_at: datetime


@router.post("/members/{user_id}/reset-password", response_model=PasswordResetResponse)
async def reset_member_password(
    user_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> PasswordResetResponse:
    result = await session.execute(
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == user_id, User.tenant_id == current_user.tenant_id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Üye bulunamadı")

    # Mirrors INVITABLE_ROLES' own reasoning below: a mere ADMIN forcing
    # a password reset on the OWNER would be a real privilege-escalation
    # path (reset it, log in as them), not just an inconvenience.
    target_is_owner = any(r.name == "OWNER" for r in target.roles)
    requester_is_owner = any(r.name == "OWNER" for r in current_user.roles)
    if target_is_owner and not requester_is_owner:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Bir OWNER'ın şifresini yalnızca başka bir OWNER sıfırlayabilir",
        )

    raw_token = secrets.token_urlsafe(32)
    reset = PasswordReset(
        user_id=target.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        requested_by_user_id=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=PASSWORD_RESET_EXPIRY_DAYS),
    )
    session.add(reset)
    record_security_event(session, current_user, "reset_member_password", user_id)
    await session.commit()

    return PasswordResetResponse(reset_token=raw_token, expires_at=reset.expires_at)


class CreateInviteRequest(BaseModel):
    email: EmailStr
    role: str


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    # Only ever populated on the response to POST /invites itself -- the
    # raw token is never stored, and never included when listing.
    invite_token: str | None = None


@router.post("/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: CreateInviteRequest, current_user: RequireTenantAdmin, session: DbSession
) -> InviteResponse:
    if body.role not in INVITABLE_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{body.role}' bir davet rolü değil, şunlardan biri olmalı: {list(INVITABLE_ROLES)}.",
        )

    existing_user = await session.execute(select(User.id).where(User.email == body.email))
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu e-posta zaten bir hesaba ait")

    now = datetime.now(timezone.utc)
    existing_invite = await session.execute(
        select(Invitation.id).where(
            Invitation.tenant_id == current_user.tenant_id,
            Invitation.email == body.email,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > now,
        )
    )
    if existing_invite.scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Bu e-posta için zaten bekleyen bir davet var"
        )

    role = (
        await session.execute(select(Role).where(Role.name == body.role, Role.tenant_id.is_(None)))
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bilinmeyen rol '{body.role}'")

    raw_token = secrets.token_urlsafe(32)
    invite = Invitation(
        tenant_id=current_user.tenant_id,
        email=body.email,
        role_id=role.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        invited_by_user_id=current_user.id,
        expires_at=now + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    session.add(invite)
    record_security_event(session, current_user, "create_invite", invite.id)
    await session.commit()

    return InviteResponse(
        id=invite.id,
        email=invite.email,
        role=role.name,
        expires_at=invite.expires_at,
        accepted_at=None,
        invite_token=raw_token,
    )


@router.get("/invites", response_model=list[InviteResponse])
async def list_invites(
    current_user: RequireTenantAdmin, session: DbSession
) -> list[InviteResponse]:
    result = await session.execute(
        select(Invitation)
        .options(selectinload(Invitation.role))
        .where(Invitation.tenant_id == current_user.tenant_id, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    return [
        InviteResponse(
            id=i.id, email=i.email, role=i.role.name, expires_at=i.expires_at, accepted_at=None
        )
        for i in result.scalars().all()
    ]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> None:
    invite = await session.get(Invitation, invite_id)
    if invite is None or invite.tenant_id != current_user.tenant_id:
        # Same 404 whether it doesn't exist or belongs to another tenant --
        # matches the tenant-isolation discipline everywhere else in this
        # project (see apps/api/routers/connectors/general.py).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Davet bulunamadı")
    await session.delete(invite)
    record_security_event(session, current_user, "revoke_invite", invite_id)
    await session.commit()


class TenantSettingsResponse(BaseModel):
    notification_repo: str | None


class UpdateTenantSettingsRequest(BaseModel):
    notification_repo: str | None = None

    @field_validator("notification_repo")
    @classmethod
    def _validate_repo(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if v.count("/") != 1 or v.startswith("/") or v.endswith("/"):
            raise ValueError(
                "notification_repo 'sahip/depo' formatında olmalı, örn. 'acme/notifications'"
            )
        return v


@router.get("/settings", response_model=TenantSettingsResponse)
async def get_settings(
    current_user: RequireTenantAdmin, session: DbSession
) -> TenantSettingsResponse:
    tenant = await session.get(Tenant, current_user.tenant_id)
    assert tenant is not None  # current_user.tenant_id is always a real tenant
    return TenantSettingsResponse(notification_repo=tenant.notification_repo)


@router.patch("/settings", response_model=TenantSettingsResponse)
async def update_settings(
    body: UpdateTenantSettingsRequest, current_user: RequireTenantAdmin, session: DbSession
) -> TenantSettingsResponse:
    tenant = await session.get(Tenant, current_user.tenant_id)
    assert tenant is not None
    tenant.notification_repo = body.notification_repo
    record_security_event(session, current_user, "update_settings", current_user.tenant_id)
    await session.commit()
    return TenantSettingsResponse(notification_repo=tenant.notification_repo)
