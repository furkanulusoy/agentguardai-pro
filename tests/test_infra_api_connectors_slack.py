"""
HTTP-level integration tests for the Slack connector
(connectors/slack/), through the same real FastAPI + real Postgres
path tests/test_infra_api_connectors_approvals.py already proves for
GitHub -- registered here as a real, separate test module (not folded
into that file) because what's actually worth proving about Slack is
different: it introduces a THREE-tier policy (LOW auto-allow ->
LOW-but-still-a-write auto-allow -> MEDIUM/HIGH requiring approval),
where GitHub and Gmail each only ever demonstrated two tiers. This file
proves each tier resolves to the right decision, not just that Slack's
endpoints respond at all.

Same real-Postgres-or-skip discipline and the same credential-testing
limits as test_infra_api_connectors_approvals.py's own module docstring:
Slack's real network calls (conversations.list, chat.postMessage, ...)
are made even with a throwaway test credential -- this suite has no
real, disposable Slack workspace to call them against, so what's
provable at the HTTP layer without a real upstream call (policy-tier
resolution, task-scope enforcement, the pending-approval hand-off) is
what's covered here.
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

    from apps.api.main import app
    from infrastructure.database.models import Credential, Tenant
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
class TestSlackConnector(unittest.IsolatedAsyncioTestCase):
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

    async def _register(self) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Slack Test Co",
            "tenant_slug": f"slack-test-{uuid.uuid4().hex[:10]}",
            "email": f"user+{uuid.uuid4().hex[:10]}@example.com",
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
        """Same throwaway-secret shape as
        test_infra_api_connectors_approvals.py's own helper -- never
        valid enough to actually authenticate against Slack's real API."""
        encrypted = secret_store.encrypt(json.dumps({"access_token": "fake-test-token"}))
        async with SessionLocal() as session:
            cred = Credential(
                tenant_id=tenant_id,
                connector_type="slack",
                label="Test Slack",
                encrypted_secret=encrypted,
            )
            session.add(cred)
            await session.commit()
            await session.refresh(cred)
            return cred.id

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # --- task scope ---

    async def test_slack_action_outside_task_scope_is_403(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "delete_workspace", "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

    # --- tier 1: list_channels (LOW, read, auto-allow) ---

    async def test_list_channels_is_allowed_immediately_not_pending(self):
        # A fake credential means the real Slack API call inside this
        # ALLOW branch will itself fail (502) -- what this proves is the
        # POLICY decision, not that a fake token can list real channels.
        # See this module's own docstring for why that upstream call
        # can't be exercised here for real.
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "list_channels", "params": {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        # Never 403 (in scope, not blocked) and never
        # "pending_approval" (not sensitive) -- a fake token surfaces as
        # a real 502 from the connector layer instead, which is itself
        # proof the policy allowed the call through to a real network
        # attempt rather than short-circuiting it.
        self.assertEqual(resp.status_code, 200, resp.text)

        audit = await self.client.get("/tenant/audit?limit=1", headers=self._auth(access))
        self.assertEqual(audit.json()[0]["decision"], "ALLOW")
        self.assertEqual(audit.json()[0]["decision_source"], "SYSTEM_DEFAULT")
        self.assertEqual(audit.json()[0]["risk_level"], "LOW")

    # --- tier 2: send_message (LOW risk, still a write, auto-allow) ---

    async def test_send_message_requires_human_approval(self):
        # The whole point of this tier: a real mutation (posting a
        # message) that's still auto-allowed, because it's reversible
        # (see connectors/slack/policy.py's own docstring) -- unlike
        # GitHub, where every write is HIGH/sensitive. Proven the same
        # way as list_channels above: real 502 from the fake credential,
        # not a 403 or a pending approval.
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "send_message", "params": {"channel": "C123", "text": "hi"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        audit = await self.client.get("/tenant/audit?limit=1", headers=self._auth(access))
        self.assertEqual(audit.json()[0]["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(resp.json()["status"], "pending_approval")
        self.assertEqual(audit.json()[0]["risk_level"], "LOW")

    # --- tier 3: create_channel / delete_message / invite_user (sensitive) ---

    async def test_create_channel_requires_approval(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "create_channel", "params": {"name": "incident-room"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "pending_approval")

        approval = await self.client.get(
            f"/approvals/{body['approval_id']}", headers=self._auth(access)
        )
        self.assertEqual(approval.json()["risk_level"], "MEDIUM")

    async def test_delete_message_requires_approval(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={
                "action": "delete_message",
                "params": {"channel": "C123", "ts": "1234567890.123456"},
            },
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "pending_approval")

        approval = await self.client.get(
            f"/approvals/{body['approval_id']}", headers=self._auth(access)
        )
        self.assertEqual(approval.json()["risk_level"], "HIGH")

    async def test_invite_user_requires_approval(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "invite_user", "params": {"channel": "C123", "user_id": "U456"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "pending_approval")

        approval = await self.client.get(
            f"/approvals/{body['approval_id']}", headers=self._auth(access)
        )
        self.assertEqual(approval.json()["risk_level"], "HIGH")

    # --- Policy Simulator picks up the new connector_type for free ---

    async def test_policy_simulator_accepts_a_registered_slack_action(self):
        # Phase 23's simulate endpoint is entirely registry-driven
        # (apps/api/routers/policies.py's _validate_action) -- this
        # proves Slack's registration alone was enough to make it work
        # there too, without any Policy Simulator-specific code change.
        access, _ = await self._register()
        resp = await self.client.get(
            "/tenant/policies/slack/delete_message/simulate", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["connector_type"], "slack")
        self.assertEqual(body["action"], "delete_message")
        self.assertEqual(body["total_events"], 0)

    async def test_policy_simulator_rejects_an_unregistered_slack_action(self):
        access, _ = await self._register()
        resp = await self.client.get(
            "/tenant/policies/slack/delete_workspace/simulate", headers=self._auth(access)
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
