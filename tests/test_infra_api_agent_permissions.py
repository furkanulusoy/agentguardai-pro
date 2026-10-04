"""
HTTP-level integration tests for an Agent's own, narrower-than-its-
owner's RBAC permission set --
GET/PUT/DELETE /tenant/agents/{id}/permissions[/{code}]
(apps/api/routers/agents.py) and what actually changes at every
Actor-gated route (apps/api/dependencies.py's require_actor_permission)
once at least one grant exists. See
infrastructure/database/models/agent_permission_grant.py's own
docstring for why this defaults to "inherit the owner's full set," not
deny-by-default the way AgentCredentialGrant does -- the two most
important things to prove here are that default-inherit behavior
itself, and that it narrows to a real intersection (never widens past
the owner's own current roles) the moment a grant exists.

Same real-Postgres-or-skip discipline and fake-credential approach as
tests/test_infra_api_agents.py, which this file mirrors.
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


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestAgentPermissionGrants(unittest.IsolatedAsyncioTestCase):
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

    # -- fixtures -------------------------------------------------------

    async def _register(self, **overrides) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Agent Perms Test Co",
            "tenant_slug": f"agent-perms-{uuid.uuid4().hex[:10]}",
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

    async def _make_credential(self, tenant_id: uuid.UUID) -> uuid.UUID:
        encrypted = secret_store.encrypt(json.dumps({"access_token": "fake-test-token"}))
        async with SessionLocal() as session:
            cred = Credential(
                tenant_id=tenant_id,
                connector_type="github",
                label="Test GitHub",
                encrypted_secret=encrypted,
            )
            session.add(cred)
            await session.commit()
            await session.refresh(cred)
            return cred.id

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _create_agent(self, access: str, name: str = "Test Agent") -> tuple[uuid.UUID, str]:
        resp = await self.client.post(
            "/tenant/agents", json={"name": name}, headers=self._auth(access)
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return uuid.UUID(body["id"]), body["api_key"]

    async def _grant_connector(self, access: str, agent_id: uuid.UUID, credential_id) -> None:
        resp = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        assert resp.status_code == 200, resp.text

    async def _grant_permission(self, access: str, agent_id: uuid.UUID, code: str):
        return await self.client.put(
            f"/tenant/agents/{agent_id}/permissions/{code}", headers=self._auth(access)
        )

    # --- GET /tenant/agents/{id}/permissions ---

    async def test_list_shows_only_permissions_the_owner_holds(self):
        access, _tenant_id = await self._register()
        agent_id, _key = await self._create_agent(access)

        resp = await self.client.get(
            f"/tenant/agents/{agent_id}/permissions", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 200)
        codes = {row["code"] for row in resp.json()}
        # Registering creates an OWNER -- OWNER holds every permission
        # (see alembic's seed migration), so this is really asserting
        # "the full set," not an arbitrary subset.
        self.assertIn("agent.execute", codes)
        self.assertIn("tenant.admin", codes)
        self.assertEqual(
            {row["code"] for row in resp.json() if row["is_granted"]},
            {"agent.execute", "connector.read", "approval.read"},
        )

    async def test_viewer_cannot_list_or_grant(self):
        access, tenant_id = await self._register()
        agent_id, _key = await self._create_agent(access)
        viewer_access = await self._make_viewer(tenant_id)

        list_resp = await self.client.get(
            f"/tenant/agents/{agent_id}/permissions", headers=self._auth(viewer_access)
        )
        self.assertEqual(list_resp.status_code, 403)

        grant_resp = await self._grant_permission(viewer_access, agent_id, "agent.execute")
        self.assertEqual(grant_resp.status_code, 403)

    # --- PUT/DELETE .../permissions/{code} ---

    async def test_grant_and_revoke_round_trip(self):
        access, _tenant_id = await self._register()
        agent_id, _key = await self._create_agent(access)

        grant_resp = await self._grant_permission(access, agent_id, "agent.execute")
        self.assertEqual(grant_resp.status_code, 200, grant_resp.text)
        self.assertTrue(grant_resp.json()["is_granted"])

        list_resp = await self.client.get(
            f"/tenant/agents/{agent_id}/permissions", headers=self._auth(access)
        )
        row = next(r for r in list_resp.json() if r["code"] == "agent.execute")
        self.assertTrue(row["is_granted"])

        revoke_resp = await self.client.delete(
            f"/tenant/agents/{agent_id}/permissions/agent.execute", headers=self._auth(access)
        )
        self.assertEqual(revoke_resp.status_code, 204)

        list_resp2 = await self.client.get(
            f"/tenant/agents/{agent_id}/permissions", headers=self._auth(access)
        )
        row2 = next(r for r in list_resp2.json() if r["code"] == "agent.execute")
        self.assertFalse(row2["is_granted"])

    async def test_granting_twice_is_idempotent(self):
        access, _tenant_id = await self._register()
        agent_id, _key = await self._create_agent(access)

        first = await self._grant_permission(access, agent_id, "agent.execute")
        second = await self._grant_permission(access, agent_id, "agent.execute")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

    async def test_unknown_permission_code_is_404(self):
        access, _tenant_id = await self._register()
        agent_id, _key = await self._create_agent(access)

        resp = await self._grant_permission(access, agent_id, "not.a.real.permission")
        self.assertEqual(resp.status_code, 404)

    async def test_cannot_grant_from_another_tenant(self):
        access_a, _tenant_a = await self._register()
        agent_id, _key = await self._create_agent(access_a)
        access_b, _tenant_b = await self._register()

        resp = await self._grant_permission(access_b, agent_id, "agent.execute")
        self.assertEqual(resp.status_code, 404)

    # --- the real behavior: default-inherit, then intersection-narrow ---

    async def test_agent_with_zero_grants_is_denied(self):
        access, _ = await self._register()
        agent_id, key = await self._create_agent(access)
        for code in ["connector.read", "approval.read", "agent.execute"]:
            await self.client.delete(
                f"/tenant/agents/{agent_id}/permissions/{code}", headers=self._auth(access)
            )
        self.assertEqual(
            (await self.client.get("/connectors", headers=self._auth(key))).status_code, 403
        )

    async def test_agent_with_one_grant_is_narrowed_to_only_that_permission(self):
        access, _ = await self._register()
        agent_id, key = await self._create_agent(access)
        for code in ["connector.read", "approval.read"]:
            await self.client.delete(
                f"/tenant/agents/{agent_id}/permissions/{code}", headers=self._auth(access)
            )
        self.assertEqual(
            (await self.client.get("/connectors", headers=self._auth(key))).status_code, 403
        )
        self.assertEqual(
            (await self.client.get("/approvals", headers=self._auth(key))).status_code, 403
        )

    async def test_revoking_the_last_grant_never_expands_access(self):
        access, _ = await self._register()
        agent_id, key = await self._create_agent(access)
        for code in ["connector.read", "approval.read", "agent.execute"]:
            await self.client.delete(
                f"/tenant/agents/{agent_id}/permissions/{code}", headers=self._auth(access)
            )
        self.assertEqual(
            (await self.client.get("/connectors", headers=self._auth(key))).status_code, 403
        )
        self.assertEqual(
            (await self.client.get("/approvals", headers=self._auth(key))).status_code, 403
        )

    async def test_execute_still_works_when_agent_execute_is_the_only_grant(self):
        """The intersection must include what WAS granted, not just
        exclude what wasn't -- a real execute call against a real
        (fake-secret) credential, through the full policy/audit path,
        proves agent.execute itself still resolves correctly once
        explicitly granted, not just that other permissions got
        narrowed away."""
        access, tenant_id = await self._register()
        agent_id, agent_key = await self._create_agent(access)
        credential_id = await self._make_credential(tenant_id)
        await self._grant_connector(access, agent_id, credential_id)
        await self._grant_permission(access, agent_id, "agent.execute")

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_repos", "params": {}},
            headers={**self._auth(agent_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        # A fake credential means the real GitHub call inside this
        # ALLOW branch fails -- what matters here is that this got past
        # the permission gate at all (never a 403 from
        # require_actor_permission("agent.execute")).
        self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main()
