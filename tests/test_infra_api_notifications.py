"""
HTTP-level integration tests for GET/PATCH /tenant/settings
(apps/api/routers/tenant.py) and the notification hand-off it feeds --
apps/api/services/notifications.py's notify_pending_approval, wired
into POST /connectors/{id}/execute (apps/api/routers/connectors/general.py).

Same real-Postgres-or-skip discipline as test_infra_api.py.

Scope, stated rather than silently narrowed: notify_pending_approval
actually opening a real GitHub Issue is NOT exercised here -- same
reasoning as test_infra_api_connectors_approvals.py's approved=True
gap, this suite has no real, disposable OAuth-linked GitHub account to
call the real API with. What IS covered, fully network-free because
both return before ever calling GitHub: (1) no notification_repo
configured (the default -- most tenants), and (2) notification_repo
configured but the tenant has no active GitHub credential (e.g. only a
Gmail connector). Both are checked by reading the ApprovalRequest row
directly from the database, since notification_issue_number isn't
exposed on the HTTP response.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import patch

os.environ["RATE_LIMIT_ENABLED"] = "false"

try:
    import httpx
    from sqlalchemy import select

    from apps.api.main import app
    from infrastructure.auth.passwords import hash_password
    from infrastructure.database.models import Credential, Role, Tenant, User
    from infrastructure.database.models.approval import ApprovalRequest
    from infrastructure.database.session import SessionLocal, check_database_connection, engine
    from infrastructure.secrets import secret_store

    API_IMPORTS_AVAILABLE = True
except ImportError:
    API_IMPORTS_AVAILABLE = False


def _db_reachable() -> bool:
    if not API_IMPORTS_AVAILABLE:
        return False
    try:
        reachable = asyncio.run(check_database_connection())
    except Exception:
        return False
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestTenantNotificationSettings(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._outbound = patch(
            "apps.api.services.operations.run_connector_action",
            side_effect=RuntimeError("Synthetic provider outage"),
        )
        self._outbound.start()
        self.addCleanup(self._outbound.stop)
        self.transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        self._cleanup_tenant_ids: list[uuid.UUID] = []

    async def asyncTearDown(self):
        await self.client.aclose()
        if self._cleanup_tenant_ids:
            async with SessionLocal() as session:
                for tenant_id in self._cleanup_tenant_ids:
                    obj = await session.get(Tenant, tenant_id)
                    if obj is not None:
                        await session.delete(obj)
                await session.commit()
        await engine.dispose()

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _register(self, **overrides) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Test Co",
            "tenant_slug": f"test-{uuid.uuid4().hex[:10]}",
            "email": f"owner+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
        body.update(overrides)
        resp = await self.client.post("/auth/register", json=body)
        assert resp.status_code == 201, resp.text
        access = resp.json()["access_token"]
        me = await self.client.get("/auth/me", headers=self._auth(access))
        tenant_id = uuid.UUID(me.json()["tenant_id"])
        self._cleanup_tenant_ids.append(tenant_id)
        return access, tenant_id

    async def _make_viewer(self, tenant_id: uuid.UUID) -> str:
        email = f"viewer+{uuid.uuid4().hex[:10]}@example.com"
        password = "another correct horse battery"
        async with SessionLocal() as session:
            viewer_role = (
                await session.execute(select(Role).where(Role.name == "VIEWER"))
            ).scalar_one()
            viewer = User(tenant_id=tenant_id, email=email, hashed_password=hash_password(password))
            viewer.roles.append(viewer_role)
            session.add(viewer)
            await session.commit()
        login = await self.client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    async def _make_credential(self, tenant_id: uuid.UUID, connector_type: str) -> uuid.UUID:
        encrypted = secret_store.encrypt(json.dumps({"access_token": "fake-test-token"}))
        async with SessionLocal() as session:
            cred = Credential(
                tenant_id=tenant_id,
                connector_type=connector_type,
                label=f"Test {connector_type}",
                encrypted_secret=encrypted,
            )
            session.add(cred)
            await session.commit()
            await session.refresh(cred)
            return cred.id

    # --- GET/PATCH /tenant/settings ---

    async def test_default_notification_repo_is_null(self):
        access, _tenant_id = await self._register()
        resp = await self.client.get("/tenant/settings", headers=self._auth(access))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["notification_repo"])

    async def test_owner_can_set_and_get_notification_repo(self):
        access, _tenant_id = await self._register()
        patched = await self.client.patch(
            "/tenant/settings",
            json={"notification_repo": "acme/notifications"},
            headers=self._auth(access),
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["notification_repo"], "acme/notifications")

        got = await self.client.get("/tenant/settings", headers=self._auth(access))
        self.assertEqual(got.json()["notification_repo"], "acme/notifications")

    async def test_notification_repo_can_be_cleared(self):
        access, _tenant_id = await self._register()
        await self.client.patch(
            "/tenant/settings",
            json={"notification_repo": "acme/notifications"},
            headers=self._auth(access),
        )
        cleared = await self.client.patch(
            "/tenant/settings", json={"notification_repo": None}, headers=self._auth(access)
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["notification_repo"])

    async def test_invalid_repo_format_is_rejected(self):
        access, _tenant_id = await self._register()
        resp = await self.client.patch(
            "/tenant/settings",
            json={"notification_repo": "not-a-valid-format"},
            headers=self._auth(access),
        )
        self.assertEqual(resp.status_code, 422)

    async def test_viewer_cannot_update_settings(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        resp = await self.client.patch(
            "/tenant/settings",
            json={"notification_repo": "acme/notifications"},
            headers=self._auth(viewer_access),
        )
        self.assertEqual(resp.status_code, 403)

    # --- notify_pending_approval, network-free paths only (see module docstring) ---

    async def test_pending_approval_with_no_notification_repo_is_not_flagged_for_notification(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id, "gmail")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "read_message", "params": {"message_id": "abc123"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        approval_id = uuid.UUID(resp.json()["approval_id"])

        async with SessionLocal() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            self.assertIsNone(approval.notification_issue_number)
            self.assertIsNone(approval.notification_repo)

    async def test_pending_approval_with_repo_but_no_github_credential_is_not_flagged(self):
        access, tenant_id = await self._register()
        await self.client.patch(
            "/tenant/settings",
            json={"notification_repo": "acme/notifications"},
            headers=self._auth(access),
        )
        # Only a Gmail credential exists for this tenant -- no GitHub
        # credential to notify with, so notify_pending_approval must
        # skip before ever calling the real GitHub API.
        credential_id = await self._make_credential(tenant_id, "gmail")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "read_message", "params": {"message_id": "abc123"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        approval_id = uuid.UUID(resp.json()["approval_id"])

        async with SessionLocal() as session:
            approval = await session.get(ApprovalRequest, approval_id)
            self.assertIsNone(approval.notification_issue_number)


if __name__ == "__main__":
    unittest.main()
