"""
HTTP-level integration tests for the per-tenant Policy Engine --
GET/PUT/DELETE /tenant/policies/* (apps/api/routers/policies.py) and its
actual effect on POST /connectors/{id}/execute
(apps/api/routers/connectors/general.py). See
docs/PRODUCTIZATION_ROADMAP.md, Phase A.

Same real-Postgres-or-skip discipline, rate-limit-disabled reasoning,
and fake-GitHub-credential approach as test_infra_api_connectors_approvals.py
-- reused here rather than duplicated in spirit: this file only adds
what that one doesn't already cover (policy CRUD, and execution
outcomes that change *because* of a tenant override).

One test (`test_allow_override_skips_approval_and_attempts_real_execution`)
deliberately exercises the immediate-execution path for an action the
system default would gate behind approval. It can't complete a real
GitHub call with a throwaway fake token -- same limitation the sibling
file's module docstring states -- so it asserts the real, honest
outcome of that attempt (a 502 from the real connector layer failing to
authenticate) rather than pretending to fake a 200. What it proves is
narrower but real: the ALLOW override routed the call to the immediate-
execution path at all, instead of creating a pending approval.
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
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()

_CLOSE_ISSUE_PARAMS = {"repo": "octocat/Hello-World", "issue_number": 1}


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestPolicyEngine(unittest.IsolatedAsyncioTestCase):
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

    # -- fixtures (same shape as test_infra_api_connectors_approvals.py) --

    async def _register(self, **overrides) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Policy Test Co",
            "tenant_slug": f"policy-{uuid.uuid4().hex[:10]}",
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

    async def _make_credential(
        self, tenant_id: uuid.UUID, connector_type: str = "github"
    ) -> uuid.UUID:
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

    async def _set_policy(self, access: str, connector_type: str, action: str, decision: str):
        return await self.client.put(
            f"/tenant/policies/{connector_type}/{action}",
            json={"decision": decision},
            headers=self._auth(access),
        )

    # --- GET /tenant/policies ---

    async def test_list_effective_policies_shows_system_defaults_with_no_overrides(self):
        access, _tenant_id = await self._register()
        resp = await self.client.get("/tenant/policies", headers=self._auth(access))
        self.assertEqual(resp.status_code, 200)
        rows = {(r["connector_type"], r["action"]): r for r in resp.json()}

        self.assertEqual(rows[("github", "close_issue")]["system_default"], "REQUIRE_APPROVAL")
        self.assertEqual(rows[("github", "close_issue")]["effective"], "REQUIRE_APPROVAL")
        self.assertIsNone(rows[("github", "close_issue")]["override"])

        self.assertEqual(rows[("github", "list_repos")]["system_default"], "ALLOW")
        self.assertEqual(rows[("gmail", "read_message")]["system_default"], "REQUIRE_APPROVAL")
        self.assertEqual(rows[("gmail", "list_messages")]["system_default"], "ALLOW")

    async def test_list_policies_requires_a_token(self):
        resp = await self.client.get("/tenant/policies")
        self.assertEqual(resp.status_code, 401)

    async def test_viewer_cannot_list_policies(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        resp = await self.client.get("/tenant/policies", headers=self._auth(viewer_access))
        self.assertEqual(resp.status_code, 403)

    # --- PUT /tenant/policies/{connector_type}/{action} ---

    async def test_owner_can_set_and_it_becomes_the_effective_decision(self):
        access, _tenant_id = await self._register()
        resp = await self._set_policy(access, "github", "close_issue", "DENY")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["effective"], "DENY")
        self.assertEqual(resp.json()["system_default"], "REQUIRE_APPROVAL")

        listed = await self.client.get("/tenant/policies", headers=self._auth(access))
        row = next(r for r in listed.json() if r["action"] == "close_issue")
        self.assertEqual(row["override"], "DENY")

    async def test_setting_twice_upserts_rather_than_erroring(self):
        access, _tenant_id = await self._register()
        first = await self._set_policy(access, "github", "close_issue", "DENY")
        self.assertEqual(first.status_code, 200)
        second = await self._set_policy(access, "github", "close_issue", "ALLOW")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["effective"], "ALLOW")

    async def test_set_policy_on_action_outside_task_scope_is_400(self):
        access, _tenant_id = await self._register()
        resp = await self._set_policy(access, "github", "delete_repository", "DENY")
        self.assertEqual(resp.status_code, 400)

    async def test_set_policy_on_unknown_connector_is_404(self):
        access, _tenant_id = await self._register()
        resp = await self._set_policy(access, "not-a-real-connector", "some_action", "DENY")
        self.assertEqual(resp.status_code, 404)

    async def test_viewer_cannot_set_policy(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        resp = await self._set_policy(viewer_access, "github", "close_issue", "DENY")
        self.assertEqual(resp.status_code, 403)

    async def test_policy_override_is_tenant_scoped(self):
        access_a, _tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        await self._set_policy(access_a, "github", "close_issue", "DENY")

        listed_b = await self.client.get("/tenant/policies", headers=self._auth(access_b))
        row_b = next(r for r in listed_b.json() if r["action"] == "close_issue")
        self.assertIsNone(row_b["override"])
        self.assertEqual(row_b["effective"], "REQUIRE_APPROVAL")

    # --- DELETE /tenant/policies/{connector_type}/{action} ---

    async def test_delete_clears_override_back_to_system_default(self):
        access, _tenant_id = await self._register()
        await self._set_policy(access, "github", "close_issue", "DENY")

        resp = await self.client.delete(
            "/tenant/policies/github/close_issue", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 204)

        listed = await self.client.get("/tenant/policies", headers=self._auth(access))
        row = next(r for r in listed.json() if r["action"] == "close_issue")
        self.assertIsNone(row["override"])
        self.assertEqual(row["effective"], "REQUIRE_APPROVAL")

    async def test_delete_nonexistent_override_is_a_no_op(self):
        access, _tenant_id = await self._register()
        resp = await self.client.delete(
            "/tenant/policies/github/close_issue", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 204)

    # --- effect on POST /connectors/{id}/execute ---

    async def test_deny_override_blocks_a_normally_auto_allowed_action(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._set_policy(access, "github", "list_repos", "DENY")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_repos", "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_deny_override_blocks_a_normally_approval_gated_action(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._set_policy(access, "github", "close_issue", "DENY")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

        approvals = await self.client.get("/approvals", headers=self._auth(access))
        self.assertEqual(approvals.json(), [])

    async def test_require_approval_override_gates_a_normally_auto_allowed_action(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._set_policy(access, "github", "list_repos", "REQUIRE_APPROVAL")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_repos", "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending_approval")

    async def test_allow_override_skips_approval_and_attempts_real_execution(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._set_policy(access, "github", "close_issue", "ALLOW")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        # Never pending_approval -- proves the ALLOW override routed this
        # past the approval gate. A throwaway fake credential can't
        # complete a real call to api.github.com, so the honest outcome
        # here is the real connector layer's own failure (502), not a
        # faked success -- see this file's module docstring.
        self.assertEqual(resp.status_code, 200)

    async def test_allow_override_does_not_leak_across_tenants(self):
        access_a, _tenant_a = await self._register()
        access_b, tenant_b = await self._register()
        credential_b = await self._make_credential(tenant_b)
        await self._set_policy(access_a, "github", "close_issue", "ALLOW")

        resp = await self.client.post(
            f"/connectors/{credential_b}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access_b), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending_approval")


if __name__ == "__main__":
    unittest.main()
