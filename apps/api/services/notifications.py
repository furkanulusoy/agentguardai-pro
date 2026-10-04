"""
Best-effort GitHub Issue notifications for pending approvals -- closes
a real UX gap: before this, a sensitive action landing in Approvals had
no signal at all beyond someone happening to have the dashboard open.

Deliberately NOT routed through agentguard.guardrail.AgentGuard.call():
opening a notification issue isn't a scoped "agent action" subject to
task_scope/approval-gate logic -- gating a notification ABOUT a pending
approval behind another approval would be circular. This talks to
PyGithub directly, the same way connectors/github/connector.py does,
using the tenant's own already-connected GitHub credential -- no
separate notification-specific OAuth grant.

Both notify_pending_approval and close_notification_issue swallow every
exception on purpose: a GitHub outage, a revoked token, or a deleted
repo must never block the real thing (creating or resolving an
ApprovalRequest). Callers commit their own session; these functions
only set attributes on the ORM objects passed in.
"""

from __future__ import annotations

import json
import logging

from github import Auth, Github
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models import Credential
from infrastructure.database.models.approval import ApprovalRequest
from infrastructure.database.models.tenant import Tenant
from infrastructure.secrets import secret_store

logger = logging.getLogger(__name__)


async def _active_github_credential(session: AsyncSession, tenant_id) -> Credential | None:
    result = await session.execute(
        select(Credential).where(
            Credential.tenant_id == tenant_id,
            Credential.connector_type == "github",
            Credential.revoked_at.is_(None),
        )
    )
    return result.scalars().first()


def _github_client(credential: Credential) -> Github:
    secret = json.loads(secret_store.decrypt(credential.encrypted_secret))
    return Github(auth=Auth.Token(secret["access_token"]))


async def notify_pending_approval(session: AsyncSession, approval: ApprovalRequest) -> None:
    """No-op, silently, unless the tenant has configured a
    notification_repo AND has an active GitHub credential -- both
    conditions are the normal case (most tenants), not an error."""
    tenant = await session.get(Tenant, approval.tenant_id)
    if tenant is None or not tenant.notification_repo:
        return

    credential = await _active_github_credential(session, approval.tenant_id)
    if credential is None:
        return

    action_label = f"{approval.credential.connector_type}.{approval.action}"
    try:
        client = _github_client(credential)
        repo = client.get_repo(tenant.notification_repo)
        issue = repo.create_issue(
            title=f"AgentGuard: onay bekliyor -- {action_label}",
            body=(
                f"**Aksiyon:** `{action_label}`\n"
                f"**Risk seviyesi:** {approval.risk_level}\n"
                f"**İsteyen:** {approval.requested_by.email}\n\n"
                "Bu, AgentGuard dashboard'undaki Approvals sayfasından "
                "onaylanmayı veya reddedilmeyi bekliyor."
            ),
        )
    except Exception:
        logger.warning(
            "Failed to open GitHub notification issue for approval %s", approval.id, exc_info=True
        )
        return

    approval.notification_repo = tenant.notification_repo
    approval.notification_issue_number = issue.number


async def close_notification_issue(session: AsyncSession, approval: ApprovalRequest) -> None:
    """No-op if notify_pending_approval never ran (or failed) for this approval."""
    if not approval.notification_repo or approval.notification_issue_number is None:
        return

    credential = await _active_github_credential(session, approval.tenant_id)
    if credential is None:
        return

    outcome = "onaylandı ✅" if approval.status == "APPROVED" else "reddedildi ❌"
    resolver = approval.resolved_by.email if approval.resolved_by else "bilinmiyor"

    try:
        client = _github_client(credential)
        repo = client.get_repo(approval.notification_repo)
        issue = repo.get_issue(approval.notification_issue_number)
        issue.create_comment(f"{resolver} tarafından **{outcome}**.")
        issue.edit(state="closed")
    except Exception:
        logger.warning(
            "Failed to close GitHub notification issue for approval %s", approval.id, exc_info=True
        )
