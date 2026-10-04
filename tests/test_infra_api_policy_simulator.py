"""
HTTP-level integration tests for the Policy Simulator --
GET /tenant/policies/{connector_type}/{action}/simulate and its
agent-scoped twin GET /tenant/agents/{id}/policies/{ct}/{action}/simulate
(apps/api/routers/policies.py's `_simulate`, shared by both). See
docs/PRODUCTIZATION_ROADMAP.md's Phase C sketch (the signature feature
it deliberately didn't build there) and CHANGELOG.md's own writeup for
what this actually computes: real AuditEvent history for one exact
(connector_type, action), not a full guardrail re-simulation.

Same real-Postgres-or-skip discipline as the rest of this session's
platform test suite.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["RATE_LIMIT_ENABLED"] = "false"

try:
    import httpx
    from sqlalchemy import select

    from apps.api.main import app
    from infrastructure.auth.passwords import hash_password
    from infrastructure.database.models import Credential, Role, Tenant, User
    from infrastructure.database.models.audit_event import AuditEvent
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
class TestPolicySimulator(unittest.IsolatedAsyncioTestCase):
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
            "tenant_name": "Simulator Test Co",
            "tenant_slug": f"sim-{uuid.uuid4().hex[:10]}",
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

    async def _execute(self, access: str, credential_id: uuid.UUID, action: str):
        return await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": action, "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )

    async def _simulate(self, access: str, connector_type: str, action: str, **params):
        return await self.client.get(
            f"/tenant/policies/{connector_type}/{action}/simulate",
            params=params,
            headers=self._auth(access),
        )

    # --- basic shape ---

    async def test_no_history_is_all_zeros(self):
        access, _tenant_id = await self._register()
        resp = await self._simulate(access, "github", "list_repos")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        zeros = {"ALLOW": 0, "DENY": 0, "REQUIRE_APPROVAL": 0}
        self.assertEqual(body["total_events"], 0)
        self.assertEqual(body["historical_breakdown"], zeros)
        self.assertEqual(body["would_change_if"], zeros)

    async def test_action_outside_task_scope_is_400(self):
        access, _tenant_id = await self._register()
        resp = await self._simulate(access, "github", "delete_everything")
        self.assertEqual(resp.status_code, 400)

    async def test_unknown_connector_is_404(self):
        access, _tenant_id = await self._register()
        resp = await self._simulate(access, "not-a-connector", "list_repos")
        self.assertEqual(resp.status_code, 404)

    async def test_requires_a_token(self):
        resp = await self.client.get("/tenant/policies/github/list_repos/simulate")
        self.assertEqual(resp.status_code, 401)

    async def test_viewer_cannot_simulate(self):
        access, tenant_id = await self._register()
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
        viewer_access = login.json()["access_token"]

        resp = await self._simulate(viewer_access, "github", "list_repos")
        self.assertEqual(resp.status_code, 403)

    # --- real history, real counts ---

    async def test_historical_breakdown_and_would_change_reflect_real_events(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        # list_repos is ALLOW by system default -- real network call
        # fails for real with this fake credential (see
        # test_infra_api_policies.py's own module docstring for why),
        # but the decision itself ("ALLOW") is recorded before that
        # real call ever happens, which is all this simulator reads.
        for _ in range(3):
            await self._execute(access, credential_id, "list_repos")

        # DENY it via a real tenant policy for the rest of this test.
        await self.client.put(
            "/tenant/policies/github/list_repos",
            json={"decision": "DENY"},
            headers=self._auth(access),
        )
        for _ in range(2):
            await self._execute(access, credential_id, "list_repos")
        await self.client.delete("/tenant/policies/github/list_repos", headers=self._auth(access))

        resp = await self._simulate(access, "github", "list_repos")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total_events"], 5)
        self.assertEqual(body["historical_breakdown"]["ALLOW"], 3)
        self.assertEqual(body["historical_breakdown"]["DENY"], 2)
        self.assertEqual(body["historical_breakdown"]["REQUIRE_APPROVAL"], 0)

        # If ALLOW had been the rule the whole time: only the 2 DENY
        # events would have landed differently.
        self.assertEqual(body["would_change_if"]["ALLOW"], 2)
        # If DENY had been the rule: only the 3 ALLOW events change.
        self.assertEqual(body["would_change_if"]["DENY"], 3)
        # REQUIRE_APPROVAL never actually happened here -- all 5 change.
        self.assertEqual(body["would_change_if"]["REQUIRE_APPROVAL"], 5)

    async def test_simulation_is_tenant_scoped(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        await self._execute(access_a, credential_a, "list_repos")

        sim_a = await self._simulate(access_a, "github", "list_repos")
        sim_b = await self._simulate(access_b, "github", "list_repos")
        self.assertEqual(sim_a.json()["total_events"], 1)
        self.assertEqual(sim_b.json()["total_events"], 0)

    async def test_different_action_does_not_bleed_into_the_count(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._execute(access, credential_id, "list_repos")
        await self._execute(access, credential_id, "close_issue")  # a different action

        resp = await self._simulate(access, "github", "list_repos")
        self.assertEqual(resp.json()["total_events"], 1)

    async def test_days_window_excludes_older_events(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._execute(access, credential_id, "list_repos")

        # Backdate the one real AuditEvent this created to 30 days ago,
        # directly -- the platform itself has no time-travel endpoint,
        # so this is the only way to prove the days= filter for real
        # rather than just trusting the SQL.
        async with SessionLocal() as session:
            event = (
                await session.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id))
            ).scalar_one()
            event.created_at = datetime.now(timezone.utc) - timedelta(days=30)
            session.add(event)
            await session.commit()

        within_default = await self._simulate(access, "github", "list_repos")
        self.assertEqual(within_default.json()["total_events"], 0)

        within_wide_window = await self._simulate(access, "github", "list_repos", days=60)
        self.assertEqual(within_wide_window.json()["total_events"], 1)

    # --- agent-scoped variant ---

    async def test_agent_scoped_simulation_only_counts_that_agent(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        created = await self.client.post(
            "/tenant/agents", json={"name": "SimAgent"}, headers=self._auth(access)
        )
        agent_id, raw_key = created.json()["id"], created.json()["api_key"]
        await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )

        # One human-triggered event, one agent-triggered event.
        await self._execute(access, credential_id, "list_repos")
        await self._execute(raw_key, credential_id, "list_repos")

        tenant_wide = await self._simulate(access, "github", "list_repos")
        self.assertEqual(tenant_wide.json()["total_events"], 2)

        agent_scoped = await self.client.get(
            f"/tenant/agents/{agent_id}/policies/github/list_repos/simulate",
            headers=self._auth(access),
        )
        self.assertEqual(agent_scoped.status_code, 200)
        self.assertEqual(agent_scoped.json()["total_events"], 1)

    async def test_agent_scoped_simulation_for_unknown_agent_is_404(self):
        access, _tenant_id = await self._register()
        resp = await self.client.get(
            f"/tenant/agents/{uuid.uuid4()}/policies/github/list_repos/simulate",
            headers=self._auth(access),
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
