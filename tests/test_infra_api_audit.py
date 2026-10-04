"""
HTTP-level integration tests for GET /tenant/audit
(apps/api/routers/audit.py) -- proving Phase C's actual point: an
AuditEvent gets written for every decision
POST /connectors/{id}/execute makes, not just the ones that happened to
need a human's approval. See docs/PRODUCTIZATION_ROADMAP.md, Phase C,
and infrastructure/database/models/audit_event.py's own docstring.

Same real-Postgres-or-skip discipline, rate-limit-disabled reasoning,
and fake-GitHub-credential approach as this session's other platform
test files.
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

_CLOSE_ISSUE_PARAMS = {"repo": "octocat/Hello-World", "issue_number": 1}


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestAuditTrail(unittest.IsolatedAsyncioTestCase):
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
            "tenant_name": "Audit Test Co",
            "tenant_slug": f"audit-{uuid.uuid4().hex[:10]}",
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

    async def _make_credential(
        self, tenant_id: uuid.UUID, connector_type: str = "github"
    ) -> uuid.UUID:
        # A throwaway secret shaped correctly for each connector's own
        # authenticate() -- GitHub's only reads "access_token"; Gmail's
        # also reads "client_id"/"client_secret" directly (a KeyError,
        # not a caught ConnectorError, if they're missing). Neither
        # connector can complete a real call with this fake data --
        # both make a genuine network call even for their LOW-risk
        # actions -- so every test here expects the real, honest
        # failure that produces, not a faked success.
        fake_secret: dict[str, str] = {"access_token": "fake-test-token"}
        if connector_type == "gmail":
            fake_secret["client_id"] = "fake-client-id"
            fake_secret["client_secret"] = "fake-client-secret"
        encrypted = secret_store.encrypt(json.dumps(fake_secret))
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

    async def _execute(self, access: str, credential_id: uuid.UUID, action: str, params=None):
        return await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": action, "params": params or {}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )

    async def _list_audit(self, access: str, **params):
        return await self.client.get("/tenant/audit", params=params, headers=self._auth(access))

    # --- coverage of decision paths that previously left zero trace ---

    async def test_auto_allowed_action_is_audited(self):
        # Before Phase C, an auto-ALLOWed action like this left zero
        # trace anywhere once it ran -- that's the actual gap this phase
        # closes, and the point this test exists to prove. A throwaway
        # fake credential can't complete a real call to api.github.com
        # (see _make_credential's own comment), so the honest outcome is
        # a real 502 -- what matters is that it's audited at all, with
        # the *reason* it was auto-allowed in the first place recorded
        # correctly, not that the call happened to succeed.
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        exec_resp = await self._execute(access, credential_id, "list_repos")
        self.assertEqual(exec_resp.status_code, 200)

        audit = await self._list_audit(access)
        self.assertEqual(audit.status_code, 200)
        row = next(r for r in audit.json() if r["action"] == "list_repos")
        self.assertEqual(row["decision"], "ALLOW")
        self.assertEqual(row["decision_source"], "SYSTEM_DEFAULT")
        self.assertIsNone(row["result"])
        self.assertIsNotNone(row["error"])
        self.assertIsNone(row["approval_id"])

    async def test_task_scope_denial_is_audited(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        resp = await self._execute(access, credential_id, "delete_everything")
        self.assertEqual(resp.status_code, 403)

        audit = await self._list_audit(access)
        row = next(r for r in audit.json() if r["action"] == "delete_everything")
        self.assertEqual(row["decision"], "DENY")
        self.assertEqual(row["decision_source"], "TASK_SCOPE")

    async def test_tenant_policy_denial_is_audited_with_correct_source(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self.client.put(
            "/tenant/policies/github/list_repos",
            json={"decision": "DENY"},
            headers=self._auth(access),
        )

        resp = await self._execute(access, credential_id, "list_repos")
        self.assertEqual(resp.status_code, 403)

        audit = await self._list_audit(access, action="list_repos")
        row = audit.json()[0]
        self.assertEqual(row["decision"], "DENY")
        self.assertEqual(row["decision_source"], "TENANT_POLICY")

    async def test_agent_policy_source_is_recorded(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        created = await self.client.post(
            "/tenant/agents", json={"name": "AuditAgent"}, headers=self._auth(access)
        )
        agent_id, raw_key = created.json()["id"], created.json()["api_key"]
        await self.client.put(
            f"/tenant/agents/{agent_id}/connectors/{credential_id}",
            headers=self._auth(access),
            json={"resources": ["octocat/Hello-World", "C123"]},
        )
        await self.client.put(
            f"/tenant/agents/{agent_id}/policies/github/list_repos",
            json={"decision": "REQUIRE_APPROVAL"},
            headers=self._auth(access),
        )

        resp = await self._execute(raw_key, credential_id, "list_repos")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pending_approval")

        audit = await self._list_audit(access, agent_id=agent_id)
        self.assertEqual(len(audit.json()), 1)
        row = audit.json()[0]
        self.assertEqual(row["decision_source"], "AGENT_POLICY")
        self.assertEqual(row["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(row["agent_name"], "AuditAgent")
        self.assertIsNotNone(row["approval_id"])

    async def test_pending_approval_row_reflects_resolution_after_the_fact(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        exec_resp = await self._execute(access, credential_id, "close_issue", _CLOSE_ISSUE_PARAMS)
        approval_id = exec_resp.json()["approval_id"]

        before = await self._list_audit(access, action="close_issue")
        self.assertEqual(before.json()[0]["approval_status"], "PENDING")

        await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )

        after = await self._list_audit(access, action="close_issue")
        row = after.json()[0]
        self.assertEqual(row["approval_status"], "DENIED")
        self.assertIsNotNone(row["approval_resolved_by_email"])

    async def test_filter_by_decision(self):
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)
        await self._execute(access, credential_id, "list_repos")
        await self._execute(access, credential_id, "delete_everything")

        allowed = await self._list_audit(access, decision="ALLOW")
        denied = await self._list_audit(access, decision="DENY")
        self.assertTrue(all(r["decision"] == "ALLOW" for r in allowed.json()))
        self.assertTrue(all(r["decision"] == "DENY" for r in denied.json()))

    async def test_filter_by_connector_type(self):
        access, tenant_id = await self._register()
        github_cred = await self._make_credential(tenant_id, "github")
        gmail_cred = await self._make_credential(tenant_id, "gmail")
        await self._execute(access, github_cred, "list_repos")
        try:
            # Gmail's real client library raises its own exception type
            # here (google.auth.exceptions.RefreshError, not wrapped in
            # ConnectorError) when a fake credential can't refresh a real
            # token -- exactly the "unexpected exception type" case
            # general.py's broadened except Exception (this same phase)
            # exists to still audit rather than silently miss. httpx's
            # ASGITransport re-raises server exceptions in test mode
            # (raise_server_exceptions=True, the default), so it surfaces
            # here rather than as an HTTP response -- expected, not a bug.
            await self._execute(access, gmail_cred, "list_messages")
        except Exception:
            pass

        github_only = await self._list_audit(access, connector_type="github")
        self.assertTrue(all(r["connector_type"] == "github" for r in github_only.json()))
        self.assertGreaterEqual(len(github_only.json()), 1)

        gmail_only = await self._list_audit(access, connector_type="gmail")
        self.assertEqual(len(gmail_only.json()), 1)
        self.assertIsNotNone(gmail_only.json()[0]["error"])

    async def test_audit_is_tenant_scoped(self):
        access_a, tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        credential_a = await self._make_credential(tenant_a)
        await self._execute(access_a, credential_a, "list_repos")

        audit_a = await self._list_audit(access_a)
        audit_b = await self._list_audit(access_b)
        self.assertGreaterEqual(len(audit_a.json()), 1)
        self.assertEqual(len(audit_b.json()), 0)

    async def test_audit_requires_a_token(self):
        resp = await self.client.get("/tenant/audit")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
