"""
HTTP-level integration tests for sdk/python/agentguard_sdk -- the
standalone SDK package Phase D (docs/PRODUCTIZATION_ROADMAP.md) added.
Run against the real platform app (apps.api.main:app) via
httpx.ASGITransport, the same pattern every other platform test file in
this session uses -- no real network, but a real Postgres and real
FastAPI request handling underneath, including this tenant's real
Agent auth (Phase B) and approval flow (Phase 7).

`pip install -e sdk/python` (done once per environment, same as
`pip install -e ".[api]"`) makes `agentguard_sdk` importable here --
this test file is the one place in the repo that's allowed to reach
into both the SDK package and the platform's own test fixtures, since
proving the SDK actually works against the real platform is the entire
point.
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
    from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardError

    from apps.api.main import app
    from connectors.base import ConnectorAuthenticationError
    from infrastructure.database.models import Credential, Tenant
    from infrastructure.database.session import SessionLocal, check_database_connection, engine
    from infrastructure.secrets import secret_store

    SDK_IMPORTS_AVAILABLE = True
except ImportError:
    SDK_IMPORTS_AVAILABLE = False


def _db_reachable() -> bool:
    if not SDK_IMPORTS_AVAILABLE:
        return False
    try:
        reachable = asyncio.run(check_database_connection())
    except Exception:
        return False
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()

_CLOSE_ISSUE_PARAMS = {"repo": "octocat/Hello-World", "issue_number": 1}


@unittest.skipUnless(
    SDK_IMPORTS_AVAILABLE,
    "api extras or agentguard-sdk not installed "
    "(pip install -e '.[api]' && pip install -e sdk/python)",
)
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestSdkClient(unittest.IsolatedAsyncioTestCase):
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

    async def _register(self) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "SDK Test Co",
            "tenant_slug": f"sdk-{uuid.uuid4().hex[:10]}",
            "email": f"owner+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
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

    async def _create_agent(self, access: str) -> tuple[str, str]:
        """Returns (agent_id, api_key)."""
        resp = await self.client.post(
            "/tenant/agents", json={"name": "SDK Test Agent"}, headers=self._auth(access)
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"], resp.json()["api_key"]

    async def _grant(self, access: str, agent_id: str, credential_id) -> None:
        """AgentCredentialGrant is deny-by-default (see
        infrastructure/database/models/agent_credential_grant.py) -- any
        test that expects an agent key to actually reach policy/execution
        logic, not just fail at this earlier gate, needs this first."""
        resp = await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        assert resp.status_code == 200, resp.text

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _sdk(self, agent_key: str) -> AgentGuardClient:
        # ASGITransport calls FastAPI directly, before nginx maps the public
        # /api prefix to the application's root routes.
        return AgentGuardClient(
            agent_key,
            base_url="http://test",
            transport=self.transport,
        )

    # --- construction ---

    async def test_rejects_a_key_without_the_agent_prefix(self):
        with self.assertRaises(ValueError):
            AgentGuardClient("not-an-agent-key")

    # --- list_connectors ---

    async def test_list_connectors_returns_real_data(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id, agent_key = await self._create_agent(access)
        await self._grant(access, agent_id, credential_id)

        async with self._sdk(agent_key) as sdk:
            connectors = await sdk.list_connectors()

        self.assertEqual(len(connectors), 1)
        self.assertEqual(connectors[0]["connector_type"], "github")

    # --- run() ---

    async def test_run_raises_when_a_policy_rule_denies_the_action(self):
        # A DENY (from a real policy rule) is a clean way to prove run()
        # surfaces the platform's real rejection as AgentGuardError,
        # without needing a real upstream network call to succeed --
        # same reasoning test_infra_api_policies.py's own module
        # docstring uses.
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id, agent_key = await self._create_agent(access)
        await self._grant(access, agent_id, credential_id)
        await self.client.put(
            "/tenant/policies/github/list_repos",
            json={"decision": "DENY"},
            headers=self._auth(access),
        )

        async with self._sdk(agent_key) as sdk:
            with self.assertRaises(AgentGuardError):
                await sdk.run(str(credential_id), "list_repos")

    async def test_run_raises_denied_when_a_human_denies_it(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id, agent_key = await self._create_agent(access)
        await self._grant(access, agent_id, credential_id)

        async def deny_it_shortly() -> None:
            await asyncio.sleep(0.3)
            listed = await self.client.get("/approvals", headers=self._auth(access))
            approval_id = listed.json()[0]["id"]
            resolved = await self.client.post(
                f"/approvals/{approval_id}/resolve",
                json={"approved": False},
                headers=self._auth(access),
            )
            assert resolved.status_code == 200, resolved.text

        async with self._sdk(agent_key) as sdk:
            denier = asyncio.create_task(deny_it_shortly())
            with self.assertRaises(AgentGuardDenied):
                await sdk.run(
                    str(credential_id),
                    "close_issue",
                    _CLOSE_ISSUE_PARAMS,
                    poll_interval=0.1,
                    max_wait=5,
                )
            await denier

    async def test_run_on_unknown_credential_is_an_agentguard_error(self):
        access, _tenant_id = await self._register()
        _agent_id, agent_key = await self._create_agent(access)

        async with self._sdk(agent_key) as sdk:
            with self.assertRaises(AgentGuardError):
                await sdk.run(str(uuid.uuid4()), "list_repos")

    async def test_run_exposes_safe_provider_authentication_error_code(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        agent_id, agent_key = await self._create_agent(access)
        await self._grant(access, agent_id, credential_id)

        with patch(
            "apps.api.services.operations.run_connector_action",
            side_effect=ConnectorAuthenticationError("sensitive provider detail"),
        ):
            async with self._sdk(agent_key) as sdk:
                with self.assertRaisesRegex(
                    AgentGuardError, "PROVIDER_AUTHENTICATION_FAILED"
                ) as raised:
                    await sdk.run(str(credential_id), "list_repos")

        self.assertNotIn("sensitive provider detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
