"""
Full HTTP-level integration tests for apps/api's connector and approval
routers (GET/POST /connectors/*, GET/POST /approvals/*) -- the two
routers that carry this project's actual point (scope enforcement, the
human-approval gate, tenant isolation, RBAC) but had zero coverage at
the HTTP layer before this file. agentguard/'s own unit tests
(tests/test_infra_connectors.py) already cover the guardrail engine
itself against a fake in-memory connector; test_infra_api.py covers
auth/tenant. This file is the missing piece: the real FastAPI routes,
through a real Postgres, the same way a real client actually calls
them.

Same real-Postgres-or-skip discipline as test_infra_api.py: skipped
(not failed) if the api extras aren't installed or Postgres isn't
reachable.

Scope, stated rather than silently narrowed: resolve_approval's
approved=True branch, which runs the real guarded connector call
(apps/api/services/execution.py), is NOT exercised here beyond the
request that CREATES a pending approval -- connectors/github's
list_repos/close_issue both make a real network call to api.github.com
even with a throwaway test credential, and this suite has no real,
disposable OAuth-linked GitHub account to call it with. Everything
that does not require that real upstream call -- policy-scope
enforcement, tenant isolation, RBAC on every route, the
pending-approval hand-off, denying (which never touches the
connector), and the atomic re-resolve race guard -- is covered here.
The approved=True path was verified manually against real data during
this project's UI work; see CHANGELOG.md's Phase 9.

Every test here registers a fresh tenant via /auth/register -- rate
limiting (apps/api/rate_limit.py) is disabled before apps.api.main is
imported, same reasoning as test_infra_api.py.
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
    # See test_infra_api.py's identical helper for why engine.dispose()
    # is required here: asyncpg connections can't cross event loops.
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()

_CLOSE_ISSUE_PARAMS = {"repo": "octocat/Hello-World", "issue_number": 1}


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestConnectorsAndApprovals(unittest.IsolatedAsyncioTestCase):
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

    # -- fixtures -----------------------------------------------------

    async def _register(self, **overrides) -> tuple[str, uuid.UUID]:
        """Registers a fresh tenant + OWNER user. Returns (access_token, tenant_id)."""
        body = {
            "tenant_name": "Test Co",
            "tenant_slug": f"test-{uuid.uuid4().hex[:10]}",
            "email": f"user+{uuid.uuid4().hex[:10]}@example.com",
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
        """Adds a VIEWER (read-only) user to tenant_id. Returns its access_token."""
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

    async def _make_credential(
        self, tenant_id: uuid.UUID, connector_type: str = "github"
    ) -> uuid.UUID:
        """Inserts a Credential row directly with a throwaway encrypted
        secret -- bypasses the real OAuth flow, which needs a browser
        and a real provider. Never valid enough to actually authenticate
        against the real service; see the module docstring's Scope note."""
        encrypted = secret_store.encrypt(json.dumps({"access_token": "fake-test-token"}))
        async with SessionLocal() as session:
            cred = Credential(
                tenant_id=tenant_id,
                connector_type=connector_type,
                label="Test GitHub",
                encrypted_secret=encrypted,
            )
            session.add(cred)
            await session.commit()
            await session.refresh(cred)
            return cred.id

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _create_pending_approval(self, access: str, credential_id: uuid.UUID) -> uuid.UUID:
        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_approval", resp.text
        return uuid.UUID(resp.json()["approval_id"])

    # --- GET /connectors ---

    async def test_list_connectors_is_tenant_scoped(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        await self._make_credential(tenant_a)

        resp_a = await self.client.get("/connectors", headers=self._auth(access_a))
        resp_b = await self.client.get("/connectors", headers=self._auth(access_b))
        self.assertEqual(len(resp_a.json()), 1)
        self.assertEqual(len(resp_b.json()), 0)

    async def test_list_connectors_requires_a_token(self):
        resp = await self.client.get("/connectors")
        self.assertEqual(resp.status_code, 401)

    # --- POST /connectors/{id}/revoke ---

    async def test_revoke_connector_from_another_tenant_is_404(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_id = await self._make_credential(tenant_a)

        resp = await self.client.post(
            f"/connectors/{credential_id}/revoke", headers=self._auth(access_b)
        )
        self.assertEqual(resp.status_code, 404)

    async def test_revoke_connector_marks_it_revoked(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/revoke", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 204)

        listed = await self.client.get("/connectors", headers=self._auth(access))
        self.assertTrue(listed.json()[0]["is_revoked"])

    async def test_viewer_cannot_revoke_connector(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/revoke", headers=self._auth(viewer_access)
        )
        self.assertEqual(resp.status_code, 403)

    # --- POST /connectors/{id}/execute ---

    async def test_execute_action_outside_policy_scope_is_403(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "delete_everything", "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_execute_sensitive_action_creates_pending_approval(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        approval_id = await self._create_pending_approval(access, credential_id)

        approval = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access))
        self.assertEqual(approval.status_code, 200)
        body = approval.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertEqual(body["risk_level"], "HIGH")
        self.assertEqual(
            body["call_context"]["kwargs"], {key: "[REDACTED]" for key in _CLOSE_ISSUE_PARAMS}
        )

    async def test_execute_on_revoked_connector_is_404(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self.client.post(f"/connectors/{credential_id}/revoke", headers=self._auth(access))

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 404)

    async def test_execute_requires_a_token(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        resp = await self.client.post(
            f"/connectors/{credential_id}/execute", json={"action": "close_issue", "params": {}}
        )
        self.assertEqual(resp.status_code, 401)

    async def test_viewer_cannot_execute(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(viewer_access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

    # --- GET /approvals, GET /approvals/{id} ---

    async def test_list_approvals_is_tenant_scoped(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        await self._create_pending_approval(access_a, credential_a)

        list_a = await self.client.get("/approvals", headers=self._auth(access_a))
        list_b = await self.client.get("/approvals", headers=self._auth(access_b))
        self.assertEqual(len(list_a.json()), 1)
        self.assertEqual(len(list_b.json()), 0)

    async def test_get_approval_from_another_tenant_is_404(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        approval_id = await self._create_pending_approval(access_a, credential_a)

        resp = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access_b))
        self.assertEqual(resp.status_code, 404)

    async def test_get_nonexistent_approval_is_404(self):
        access, _tenant_id = await self._register()
        resp = await self.client.get(f"/approvals/{uuid.uuid4()}", headers=self._auth(access))
        self.assertEqual(resp.status_code, 404)

    async def test_viewer_can_list_and_read_approvals(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        credential_id = await self._make_credential(tenant_id)
        approval_id = await self._create_pending_approval(access, credential_id)

        listed = await self.client.get("/approvals", headers=self._auth(viewer_access))
        self.assertEqual(listed.status_code, 200)
        got = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(viewer_access))
        self.assertEqual(got.status_code, 200)

    # --- POST /approvals/{id}/resolve ---

    async def test_deny_approval_marks_denied_without_executing(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        approval_id = await self._create_pending_approval(access, credential_id)

        resolved = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )
        self.assertEqual(resolved.status_code, 200)
        data = resolved.json()
        self.assertEqual(data["status"], "DENIED")
        self.assertIsNone(data["result"])
        self.assertIsNone(data["error"])
        self.assertIsNotNone(data["resolved_at"])

    async def test_resolving_twice_returns_409_the_second_time(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        approval_id = await self._create_pending_approval(access, credential_id)

        first = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )
        self.assertEqual(first.status_code, 200)

        second = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )
        self.assertEqual(second.status_code, 409)

    async def test_resolve_from_another_tenant_is_404(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        approval_id = await self._create_pending_approval(access_a, credential_a)

        resp = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access_b),
        )
        self.assertEqual(resp.status_code, 404)

    async def test_resolve_nonexistent_approval_is_404(self):
        access, _tenant_id = await self._register()
        resp = await self.client.post(
            f"/approvals/{uuid.uuid4()}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )
        self.assertEqual(resp.status_code, 404)

    async def test_viewer_cannot_resolve_approval(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        credential_id = await self._make_credential(tenant_id)
        approval_id = await self._create_pending_approval(access, credential_id)

        resp = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(viewer_access),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
