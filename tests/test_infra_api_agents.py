"""
HTTP-level integration tests for Agent identity --
GET/POST /tenant/agents[/{id}/revoke] and the agent-scoped policy
endpoints (apps/api/routers/agents.py), plus what actually changes at
POST /connectors/{id}/execute (apps/api/routers/connectors/general.py)
when an Agent's own API key authenticates the call instead of a human's
JWT (apps/api/dependencies.py's Actor/get_current_actor). See
docs/PRODUCTIZATION_ROADMAP.md, Phase B.

Same real-Postgres-or-skip discipline, rate-limit-disabled reasoning,
and fake-GitHub-credential approach (a throwaway credential can't
complete a real api.github.com call, so tests that would need that
assert the real, honest 502 instead -- see
test_infra_api_connectors_approvals.py's own module docstring and
test_infra_api_policies.py's, which this file follows) as the rest of
this session's platform test suite.
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
class TestAgentIdentity(unittest.IsolatedAsyncioTestCase):
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
            "tenant_name": "Agent Test Co",
            "tenant_slug": f"agent-{uuid.uuid4().hex[:10]}",
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

    async def _make_admin(self, tenant_id: uuid.UUID) -> tuple[str, str]:
        """Returns (user_id, access_token) for a fresh ADMIN (not OWNER)
        member -- tenant.admin-holding but deliberately not the OWNER,
        for testing the OWNER-exclusion check on agent creation."""
        email = f"admin+{uuid.uuid4().hex[:10]}@example.com"
        password = "yet another correct horse"
        async with SessionLocal() as session:
            admin_role = (
                await session.execute(select(Role).where(Role.name == "ADMIN"))
            ).scalar_one()
            admin = User(tenant_id=tenant_id, email=email, hashed_password=hash_password(password))
            admin.roles.append(admin_role)
            session.add(admin)
            await session.commit()
            await session.refresh(admin)
            admin_id = str(admin.id)
        login = await self.client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return admin_id, login.json()["access_token"]

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

    async def _create_agent(self, access: str, name: str = "Test Agent", **overrides):
        body = {"name": name, **overrides}
        return await self.client.post("/tenant/agents", json=body, headers=self._auth(access))

    async def _grant(self, access: str, agent_id, credential_id) -> None:
        """AgentCredentialGrant is deny-by-default (see
        infrastructure/database/models/agent_credential_grant.py) -- any
        test that expects an agent key to actually reach policy/execution
        logic (not just fail at this earlier gate) needs this first."""
        resp = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        assert resp.status_code == 200, resp.text

    # --- POST /tenant/agents ---

    async def test_owner_can_create_agent_and_gets_a_raw_key_once(self):
        access, _tenant_id = await self._register()
        resp = await self._create_agent(access)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["api_key"].startswith("agk_"))

        listed = await self.client.get("/tenant/agents", headers=self._auth(access))
        self.assertIsNone(listed.json()[0]["api_key"])

    async def test_agent_defaults_to_creator_as_owner(self):
        access, _tenant_id = await self._register()
        me = await self.client.get("/auth/me", headers=self._auth(access))
        owner_email = me.json()["email"]

        resp = await self._create_agent(access)
        self.assertEqual(resp.json()["owner_email"], owner_email)
        self.assertEqual(resp.json()["created_by_email"], owner_email)

    async def test_admin_cannot_create_an_agent_owned_by_the_owner(self):
        # An Agent's effective permissions are its owner's, in full
        # (infrastructure/database/models/agent.py) -- a mere ADMIN
        # minting an agent "owned by" the OWNER would let it act (and
        # be attributed in every ApprovalRequest/AuditEvent) as the
        # OWNER. Same privilege-escalation reasoning as INVITABLE_ROLES
        # excluding OWNER and reset_member_password's target_is_owner
        # check (apps/api/routers/tenant.py).
        access, tenant_id = await self._register()
        me = await self.client.get("/auth/me", headers=self._auth(access))
        owner_id = me.json()["id"]
        _admin_id, admin_access = await self._make_admin(tenant_id)

        resp = await self._create_agent(admin_access, owner_user_id=owner_id)
        self.assertEqual(resp.status_code, 403)

    async def test_owner_can_create_an_agent_owned_by_another_admin(self):
        # The exclusion is specifically about minting *as the OWNER*,
        # not about cross-user agent creation in general -- an OWNER
        # assigning ownership to a mere ADMIN is a real, unrestricted
        # use case (a team lead setting up an agent for someone else).
        access, tenant_id = await self._register()
        admin_id, _admin_access = await self._make_admin(tenant_id)

        resp = await self._create_agent(access, owner_user_id=admin_id)
        self.assertEqual(resp.status_code, 201)

    async def test_creator_is_visible_when_different_from_owner(self):
        access, tenant_id = await self._register()
        me = await self.client.get("/auth/me", headers=self._auth(access))
        owner_email = me.json()["email"]
        admin_id, admin_access = await self._make_admin(tenant_id)
        admin_me = await self.client.get("/auth/me", headers=self._auth(admin_access))
        admin_email = admin_me.json()["email"]

        # OWNER creates an agent owned by the ADMIN -- creator and owner
        # genuinely differ here, and that must be visible on the record,
        # not just enforced silently at creation time.
        resp = await self._create_agent(access, owner_user_id=admin_id)
        self.assertEqual(resp.json()["owner_email"], admin_email)
        self.assertEqual(resp.json()["created_by_email"], owner_email)

    async def test_cannot_assign_an_owner_from_another_tenant(self):
        access_a, _tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        me_b = await self.client.get("/auth/me", headers=self._auth(access_b))
        outsider_id = me_b.json()["id"]

        resp = await self._create_agent(access_a, owner_user_id=outsider_id)
        self.assertEqual(resp.status_code, 400)

    async def test_viewer_cannot_create_agent(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        resp = await self._create_agent(viewer_access)
        self.assertEqual(resp.status_code, 403)

    async def test_agents_are_tenant_scoped(self):
        access_a, _tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        await self._create_agent(access_a)

        listed_a = await self.client.get("/tenant/agents", headers=self._auth(access_a))
        listed_b = await self.client.get("/tenant/agents", headers=self._auth(access_b))
        self.assertEqual(len(listed_a.json()), 1)
        self.assertEqual(len(listed_b.json()), 0)

    # --- POST /tenant/agents/{id}/revoke ---

    async def test_revoke_marks_agent_revoked(self):
        access, _tenant_id = await self._register()
        agent_id = (await self._create_agent(access)).json()["id"]

        resp = await self.client.post(
            f"/tenant/agents/{agent_id}/revoke", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 204)

        listed = await self.client.get("/tenant/agents", headers=self._auth(access))
        self.assertTrue(listed.json()[0]["is_revoked"])

    # --- agent key authenticates POST /connectors/{id}/execute ---

    async def test_agent_key_executes_as_its_owner(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        raw_key = created.json()["api_key"]
        await self._grant(access, created.json()["id"], credential_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending_approval")

        approval_id = resp.json()["approval_id"]
        approval = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access))
        self.assertEqual(approval.json()["agent_name"], "Test Agent")

    async def test_agent_sees_only_its_explicitly_granted_connectors(self):
        access, tenant_id = await self._register()
        granted_credential = await self._make_credential(tenant_id)
        ungranted_credential = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        await self._grant(access, created.json()["id"], granted_credential)

        response = await self.client.get(
            "/connectors", headers=self._auth(created.json()["api_key"])
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["id"] for row in response.json()}, {str(granted_credential)})
        self.assertNotIn(str(ungranted_credential), {row["id"] for row in response.json()})

    async def test_agent_connector_discovery_excludes_revoked_grant(self):
        access, tenant_id = await self._register()
        revoked_credential = await self._make_credential(tenant_id)
        active_credential = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        agent_id = created.json()["id"]
        await self._grant(access, agent_id, revoked_credential)
        await self._grant(access, agent_id, active_credential)

        revoked = await self.client.post(
            f"/connectors/{revoked_credential}/revoke", headers=self._auth(access)
        )
        self.assertEqual(revoked.status_code, 204)

        response = await self.client.get(
            "/connectors", headers=self._auth(created.json()["api_key"])
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["id"] for row in response.json()], [str(active_credential)])

    async def test_agent_cannot_read_another_agents_approval(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        first = await self._create_agent(access, name="First Agent")
        second = await self._create_agent(access, name="Second Agent")
        await self._grant(access, first.json()["id"], credential_id)
        await self._grant(access, second.json()["id"], credential_id)

        first_pending = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(first.json()["api_key"]), "Idempotency-Key": str(uuid.uuid4())},
        )
        second_pending = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(second.json()["api_key"]), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(first_pending.status_code, 200, first_pending.text)
        self.assertEqual(second_pending.status_code, 200, second_pending.text)

        own_list = await self.client.get("/approvals", headers=self._auth(first.json()["api_key"]))
        self.assertEqual(own_list.status_code, 200, own_list.text)
        self.assertEqual(
            [row["id"] for row in own_list.json()],
            [first_pending.json()["approval_id"]],
        )

        other_detail = await self.client.get(
            f"/approvals/{second_pending.json()['approval_id']}",
            headers=self._auth(first.json()["api_key"]),
        )
        self.assertEqual(other_detail.status_code, 404)

    async def test_revoked_agent_key_is_401(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        raw_key = created.json()["api_key"]
        await self.client.post(
            f"/tenant/agents/{created.json()['id']}/revoke", headers=self._auth(access)
        )

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 401)

    async def test_garbage_agent_key_is_401(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth("agk_not-a-real-key"), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 401)

    async def test_human_execute_still_has_no_agent_attribution(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        approval_id = resp.json()["approval_id"]
        approval = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access))
        self.assertIsNone(approval.json()["agent_name"])

    # --- GET /tenant/agents/{id}/policies, PUT/DELETE .../policies/{ct}/{action} ---

    async def test_agent_policy_list_starts_at_tenant_and_system_defaults(self):
        access, _tenant_id = await self._register()
        agent_id = (await self._create_agent(access)).json()["id"]

        resp = await self.client.get(
            f"/tenant/agents/{agent_id}/policies", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.json() if r["action"] == "close_issue")
        self.assertEqual(row["system_default"], "REQUIRE_APPROVAL")
        self.assertIsNone(row["tenant_override"])
        self.assertIsNone(row["agent_override"])
        self.assertEqual(row["effective"], "REQUIRE_APPROVAL")

    async def test_agent_override_cannot_override_tenant_deny(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id = (await self._create_agent(access)).json()["id"]
        other_agent = await self._create_agent(access, name="Second Agent")
        other_agent_key = other_agent.json()["api_key"]
        await self._grant(access, agent_id, credential_id)
        await self._grant(access, other_agent.json()["id"], credential_id)

        # Tenant-wide: DENY. Agent-specific override for `agent_id`: ALLOW.
        await self.client.put(
            "/tenant/policies/github/close_issue",
            json={"decision": "DENY"},
            headers=self._auth(access),
        )
        await self.client.put(
            f"/tenant/agents/{agent_id}/policies/github/close_issue",
            json={"decision": "ALLOW"},
            headers=self._auth(access),
        )

        listed = await self.client.get(
            f"/tenant/agents/{agent_id}/policies", headers=self._auth(access)
        )
        row = next(r for r in listed.json() if r["action"] == "close_issue")
        self.assertEqual(row["tenant_override"], "DENY")
        self.assertEqual(row["agent_override"], "ALLOW")
        self.assertEqual(row["effective"], "DENY")

        # A *different* agent (no agent-specific rule of its own) still
        # gets the tenant-wide DENY -- proves the override didn't leak.
        other_resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(other_agent_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(other_resp.status_code, 403)

    async def test_agent_scoped_allow_actually_bypasses_approval_for_that_agent(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        agent_id, raw_key = created.json()["id"], created.json()["api_key"]
        await self._grant(access, agent_id, credential_id)

        await self.client.put(
            f"/tenant/agents/{agent_id}/policies/github/close_issue",
            json={"decision": "ALLOW"},
            headers=self._auth(access),
        )

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        # Never pending_approval; a throwaway fake credential can't
        # complete a real GitHub call, so the honest outcome is the real
        # connector layer's own failure -- see this file's module
        # docstring and test_infra_api_policies.py's identical reasoning.
        self.assertEqual(resp.status_code, 200)

    async def test_delete_agent_policy_reverts_to_tenant_default(self):
        access, _tenant_id = await self._register()
        agent_id = (await self._create_agent(access)).json()["id"]
        await self.client.put(
            f"/tenant/agents/{agent_id}/policies/github/close_issue",
            json={"decision": "ALLOW"},
            headers=self._auth(access),
        )

        resp = await self.client.delete(
            f"/tenant/agents/{agent_id}/policies/github/close_issue", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 204)

        listed = await self.client.get(
            f"/tenant/agents/{agent_id}/policies", headers=self._auth(access)
        )
        row = next(r for r in listed.json() if r["action"] == "close_issue")
        self.assertIsNone(row["agent_override"])
        self.assertEqual(row["effective"], "REQUIRE_APPROVAL")

    async def test_viewer_cannot_set_agent_policy(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        agent_id = (await self._create_agent(access)).json()["id"]

        resp = await self.client.put(
            f"/tenant/agents/{agent_id}/policies/github/close_issue",
            json={"decision": "ALLOW"},
            headers=self._auth(viewer_access),
        )
        self.assertEqual(resp.status_code, 403)

    # --- AgentCredentialGrant: GET/PUT/DELETE .../connectors/{credential_id} ---

    async def test_new_agent_starts_with_no_connectors_granted(self):
        access, tenant_id = await self._register()
        await self._make_credential(tenant_id)
        agent_id = (await self._create_agent(access)).json()["id"]

        resp = await self.client.get(
            f"/tenant/agents/{agent_id}/connectors", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)
        self.assertFalse(resp.json()[0]["is_granted"])

    async def test_execute_without_a_grant_is_403(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        raw_key = created.json()["api_key"]

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_grant_then_revoke_round_trips(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self._create_agent(access)
        agent_id, raw_key = created.json()["id"], created.json()["api_key"]

        grant_resp = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        self.assertEqual(grant_resp.status_code, 200)
        self.assertTrue(grant_resp.json()["is_granted"])

        listed = await self.client.get(
            f"/tenant/agents/{agent_id}/connectors", headers=self._auth(access)
        )
        self.assertTrue(listed.json()[0]["is_granted"])

        # Granted now: list_repos (auto-allow default) actually reaches
        # the real connector layer -- honest 502 for the same reason
        # every other real-execution assertion in this file is 502, not
        # 200 (see the module docstring).
        exec_resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_repos", "params": {}},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(exec_resp.status_code, 200)

        revoke_resp = await self.client.delete(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}", headers=self._auth(access)
        )
        self.assertEqual(revoke_resp.status_code, 204)

        exec_after_revoke = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_repos", "params": {}},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(exec_after_revoke.status_code, 403)

    async def test_granting_twice_is_idempotent(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id = (await self._create_agent(access)).json()["id"]

        first = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        second = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

    async def test_grant_for_a_credential_from_another_tenant_is_404(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        agent_b_id = (await self._create_agent(access_b)).json()["id"]

        resp = await self.client.put(
            f"/tenant/agents/{agent_b_id}/connectors/{credential_a}",
            headers=self._auth(access_b),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        self.assertEqual(resp.status_code, 404)

    async def test_viewer_cannot_grant_agent_connector(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        credential_id = await self._make_credential(tenant_id)
        agent_id = (await self._create_agent(access)).json()["id"]

        resp = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(viewer_access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        self.assertEqual(resp.status_code, 403)

    async def test_not_granted_denial_is_audited_with_correct_source(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        raw_key = (await self._create_agent(access)).json()["api_key"]

        await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(raw_key), "Idempotency-Key": str(uuid.uuid4())},
        )

        audit = await self.client.get("/tenant/audit", headers=self._auth(access))
        row = audit.json()[0]
        self.assertEqual(row["decision"], "DENY")
        self.assertEqual(row["decision_source"], "AGENT_NOT_GRANTED")


if __name__ == "__main__":
    unittest.main()
