"""
Real PostgreSQL security integration tests. Providers are replaced only at the outbound
boundary.
"""

import asyncio
import json
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select, update

from apps.api.main import app
from apps.worker.__main__ import process_one_ready
from infrastructure.database.models import (
    Agent,
    AuditEvent,
    Credential,
    Operation,
    Role,
    Tenant,
    User,
)
from infrastructure.database.session import SessionLocal, engine
from infrastructure.secrets import secret_store


class SecurityIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.tenants = []
        self.access, self.tenant = await self.register()
        self.credential = await self.credential_for(self.tenant)

    async def asyncTearDown(self):
        await self.client.aclose()
        async with SessionLocal() as session:
            for tid in self.tenants:
                row = await session.get(Tenant, tid)
                if row:
                    await session.delete(row)
            await session.commit()
        await engine.dispose()

    async def register(self):
        suffix = uuid.uuid4().hex
        response = await self.client.post(
            "/auth/register",
            json={
                "tenant_name": "Security Test",
                "tenant_slug": "test-" + suffix,
                "email": suffix + "@example.com",
                "password": "Synthetic-test-password-2026",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("refresh_token", response.json())
        token = response.json()["access_token"]
        me = (await self.client.get("/auth/me", headers=self.auth(token))).json()
        tid = uuid.UUID(me["tenant_id"])
        self.tenants.append(tid)
        return token, tid

    def auth(self, token):
        return {"Authorization": "Bearer " + token}

    async def credential_for(self, tid, kind="github"):
        async with SessionLocal() as s:
            c = Credential(
                tenant_id=tid,
                connector_type=kind,
                label="Synthetic fixture",
                encrypted_secret=secret_store.encrypt(
                    json.dumps({"access_token": "synthetic-not-valid"})
                ),
            )
            s.add(c)
            await s.commit()
            return c.id

    async def agent(self, token=None, credential=None):
        token = token or self.access
        data = (
            await self.client.post(
                "/tenant/agents", headers=self.auth(token), json={"name": "Test agent"}
            )
        ).json()
        result = await self.client.put(
            f"/tenant/agents/{data['id']}/connectors/{credential or self.credential}",
            headers=self.auth(token),
            json={"resources": ["acme/allowed"]},
        )
        self.assertEqual(result.status_code, 200)
        return data["api_key"], uuid.UUID(data["id"])

    async def execute(
        self, token=None, params=None, action="close_issue", key=None, credential=None
    ):
        return await self.client.post(
            f"/connectors/{credential or self.credential}/execute",
            headers={
                **self.auth(token or self.access),
                "Idempotency-Key": key or str(uuid.uuid4()),
            },
            json={
                "action": action,
                "params": params
                if params is not None
                else {"repo": "acme/allowed", "issue_number": 1},
            },
        )

    async def pending(self, token=None):
        response = await self.execute(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending_approval")
        return response.json()

    async def resolve(self, record, approved=True):
        reviewed = await self.client.get(
            f"/approvals/{record['approval_id']}/review", headers=self.auth(self.access)
        )
        self.assertEqual(reviewed.status_code, 200)
        return await self.client.post(
            f"/approvals/{record['approval_id']}/resolve",
            headers=self.auth(self.access),
            json={"approved": approved, "payload_hash": reviewed.json()["payload_hash"]},
        )

    async def test_agent_connector_and_approval_isolation(self):
        token_a, _ = await self.agent()
        token_b, _ = await self.agent()
        extra = await self.credential_for(self.tenant)
        own = (await self.client.get("/connectors", headers=self.auth(token_a))).json()
        self.assertEqual([r["id"] for r in own], [str(self.credential)])
        self.assertNotIn(str(extra), str(own))
        record = await self.pending(token_a)
        denied = await self.client.get(
            f"/approvals/{record['approval_id']}", headers=self.auth(token_b)
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            (await self.client.get("/approvals", headers=self.auth(token_b))).json(), []
        )

    async def test_cross_tenant_isolation(self):
        token, _ = await self.register()
        self.assertEqual((await self.execute(token)).status_code, 404)

    async def test_last_permission_grant_never_restores_permissions(self):
        token, aid = await self.agent()
        for code in ["connector.read", "approval.read", "agent.execute"]:
            result = await self.client.delete(
                f"/tenant/agents/{aid}/permissions/{code}", headers=self.auth(self.access)
            )
            self.assertEqual(result.status_code, 204)
        self.assertEqual((await self.execute(token)).status_code, 403)
        self.assertEqual(
            (await self.client.get("/connectors", headers=self.auth(token))).status_code, 403
        )

    async def test_resource_scope_cannot_be_widened_by_input(self):
        token, _ = await self.agent()
        self.assertEqual(
            (await self.execute(token, {"repo": "acme/forbidden", "issue_number": 1})).status_code,
            403,
        )

    async def test_tenant_deny_is_a_ceiling(self):
        token, aid = await self.agent()
        await self.client.put(
            "/tenant/policies/github/close_issue",
            headers=self.auth(self.access),
            json={"decision": "DENY"},
        )
        await self.client.put(
            f"/tenant/agents/{aid}/policies/github/close_issue",
            headers=self.auth(self.access),
            json={"decision": "ALLOW"},
        )
        self.assertEqual((await self.execute(token)).status_code, 403)

    async def test_strict_input_rejected_before_provider(self):
        for params in [
            {"repo": "acme/allowed", "issue_number": True},
            {"repo": "acme/allowed", "issue_number": -1},
            {"repo": "acme/allowed", "issue_number": 1, "secret": "synthetic-secret"},
            {"repo": "../bad", "issue_number": 1},
        ]:
            with patch("apps.api.services.operations.run_connector_action") as run:
                response = await self.execute(params=params)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("synthetic-secret", response.text)
                run.assert_not_called()

    async def test_parallel_resolve_executes_once(self):
        record = await self.pending()
        count = []

        def provider(**kwargs):
            count.append(1)
            time.sleep(0.2)
            return {"ok": True}

        with patch("apps.api.services.operations.run_connector_action", side_effect=provider):
            a, b = await asyncio.gather(self.resolve(record), self.resolve(record))
        self.assertEqual(sorted([a.status_code, b.status_code]), [200, 409])
        self.assertEqual(len(count), 1)
        op = (
            await self.client.get(
                f"/connectors/operations/{record['operation_id']}", headers=self.auth(self.access)
            )
        ).json()
        self.assertEqual(op["status"], "completed")
        self.assertEqual(op["result"], {"ok": True})

    async def test_revoked_agent_pending_request_never_executes(self):
        token, aid = await self.agent()
        record = await self.pending(token)
        await self.client.post(f"/tenant/agents/{aid}/revoke", headers=self.auth(self.access))
        with patch("apps.api.services.operations.run_connector_action") as run:
            response = await self.resolve(record)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["execution_status"], "DENIED")
            run.assert_not_called()

    async def test_revoked_credential_pending_request_never_executes(self):
        record = await self.pending()
        await self.client.post(
            f"/connectors/{self.credential}/revoke", headers=self.auth(self.access)
        )
        with patch("apps.api.services.operations.run_connector_action") as run:
            await self.resolve(record)
            run.assert_not_called()

    async def test_policy_changed_after_request_requires_resubmit(self):
        record = await self.pending()
        await self.client.put(
            "/tenant/policies/github/close_issue",
            headers=self.auth(self.access),
            json={"decision": "DENY"},
        )
        with patch("apps.api.services.operations.run_connector_action") as run:
            response = await self.resolve(record)
            self.assertEqual(response.json()["execution_status"], "DENIED")
            run.assert_not_called()

    async def test_idempotent_allow_runs_once(self):
        key = str(uuid.uuid4())
        with patch(
            "apps.api.services.operations.run_connector_action",
            return_value=[{"full_name": "acme/allowed"}],
        ) as run:
            a = await self.execute(action="list_repos", params={}, key=key)
            b = await self.execute(action="list_repos", params={}, key=key)
            self.assertEqual(a.status_code, 200)
            self.assertEqual(a.json(), b.json())
            self.assertEqual(run.call_count, 1)
        c = await self.execute(action="list_repos", params={"max_results": 5}, key=key)
        self.assertEqual(c.status_code, 409)

    async def test_parallel_idempotent_allow_runs_once(self):
        key = str(uuid.uuid4())

        def provider(**kwargs):
            time.sleep(0.15)
            return []

        with patch(
            "apps.api.services.operations.run_connector_action", side_effect=provider
        ) as run:
            a, b = await asyncio.gather(
                self.execute(action="list_repos", params={}, key=key),
                self.execute(action="list_repos", params={}, key=key),
            )
            self.assertEqual(a.status_code, 200)
            self.assertEqual(b.status_code, 200)
            self.assertEqual(a.json()["operation_id"], b.json()["operation_id"])
            self.assertEqual(run.call_count, 1)

    async def test_provider_error_is_unknown_and_not_retried(self):
        record = await self.pending()
        with patch(
            "apps.api.services.operations.run_connector_action",
            side_effect=RuntimeError("synthetic-sensitive-detail"),
        ) as run:
            response = await self.resolve(record)
            self.assertEqual(response.json()["execution_status"], "UNKNOWN")
            self.assertNotIn("synthetic-sensitive-detail", response.text)
            again = await self.resolve(record)
            self.assertEqual(again.status_code, 409)
            self.assertEqual(run.call_count, 1)

    async def test_audit_contains_no_payload_or_provider_content(self):
        cid = await self.credential_for(self.tenant, "slack")
        response = await self.execute(
            credential=cid,
            action="send_message",
            params={"channel": "CABCDEF", "text": "synthetic-private-message@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        audit = await self.client.get("/tenant/audit", headers=self.auth(self.access))
        self.assertNotIn("synthetic-private-message", audit.text)
        async with SessionLocal() as s:
            rows = (
                (await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == self.tenant)))
                .scalars()
                .all()
            )
            self.assertNotIn("synthetic-private-message", str([r.call_context for r in rows]))

    async def test_refresh_concurrently_consumes_once(self):
        cookie = self.client.cookies.get("agentguard_refresh_token")

        async def attempt():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                return await c.post(
                    "/auth/refresh", json={}, cookies={"agentguard_refresh_token": cookie}
                )

        a, b = await asyncio.gather(attempt(), attempt())
        self.assertEqual(sorted([a.status_code, b.status_code]), [200, 401])
        self.assertNotIn("refresh_token", a.json())
        self.assertNotIn("refresh_token", b.json())

    async def test_inactive_tenant_cannot_access(self):
        async with SessionLocal() as s:
            await s.execute(update(Tenant).where(Tenant.id == self.tenant).values(is_active=False))
            await s.commit()
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.auth(self.access))).status_code, 401
        )

    async def test_viewer_cannot_authorize_connector(self):
        async with SessionLocal() as s:
            user = (await s.execute(select(User).where(User.tenant_id == self.tenant))).scalar_one()
            role = (await s.execute(select(Role).where(Role.name == "VIEWER"))).scalar_one()
            await s.refresh(user, attribute_names=["roles"])
            user.roles = [role]
            await s.commit()
        for provider in ["gmail", "github", "slack"]:
            self.assertEqual(
                (
                    await self.client.get(
                        f"/connectors/{provider}/authorize", headers=self.auth(self.access)
                    )
                ).status_code,
                403,
            )

    async def test_unknown_action_denied(self):
        self.assertEqual(
            (await self.execute(action="delete_everything", params={})).status_code, 403
        )

    async def test_approval_requires_reviewed_payload_hash(self):
        record = await self.pending()
        response = await self.client.post(
            f"/approvals/{record['approval_id']}/resolve",
            headers=self.auth(self.access),
            json={"approved": True, "payload_hash": "wrong"},
        )
        self.assertEqual(response.status_code, 409)

    async def test_forbidden_origin(self):
        response = await self.client.post(
            "/auth/refresh", json={}, headers={"Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status_code, 403)

    async def test_operation_result_is_private_to_original_actor(self):
        token_a, _ = await self.agent()
        token_b, _ = await self.agent()
        record = await self.pending(token_a)
        for token in [token_b, self.access]:
            response = await self.client.get(
                f"/connectors/operations/{record['operation_id']}", headers=self.auth(token)
            )
            self.assertEqual(response.status_code, 404)
        self.assertEqual(
            (
                await self.client.get(
                    f"/approvals/{record['approval_id']}/review", headers=self.auth(token_a)
                )
            ).status_code,
            401,
        )

    async def test_revoked_grant_blocks_pending_execution(self):
        token, aid = await self.agent()
        record = await self.pending(token)
        response = await self.client.delete(
            f"/tenant/agents/{aid}/connectors/{self.credential}", headers=self.auth(self.access)
        )
        self.assertEqual(response.status_code, 204)
        with patch("apps.api.services.operations.run_connector_action") as run:
            response = await self.resolve(record)
            self.assertEqual(response.json()["execution_status"], "DENIED")
            run.assert_not_called()

    async def test_recovery_worker_claims_ready_operation_once(self):
        with patch("apps.api.routers.connectors.general.run_operation", new_callable=AsyncMock):
            response = await self.execute(action="list_repos", params={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        provider_calls = []

        def provider(**kwargs):
            provider_calls.append(True)
            time.sleep(0.1)
            return []

        with patch("apps.api.services.operations.run_connector_action", side_effect=provider):
            await asyncio.gather(process_one_ready(self.tenant), process_one_ready(self.tenant))
        self.assertEqual(len(provider_calls), 1)
        async with SessionLocal() as session:
            operation = await session.get(Operation, uuid.UUID(response.json()["operation_id"]))
            self.assertEqual(operation.status, "SUCCEEDED")

    async def test_recovery_worker_reauthorizes_before_provider(self):
        with patch("apps.api.routers.connectors.general.run_operation", new_callable=AsyncMock):
            response = await self.execute(action="list_repos", params={})
        await self.client.post(
            f"/connectors/{self.credential}/revoke", headers=self.auth(self.access)
        )
        with patch("apps.api.services.operations.run_connector_action") as provider:
            self.assertTrue(await process_one_ready(self.tenant))
            provider.assert_not_called()
        async with SessionLocal() as session:
            operation = await session.get(Operation, uuid.UUID(response.json()["operation_id"]))
            self.assertEqual(operation.status, "DENIED")

    async def test_worker_crash_after_claim_does_not_repeat_provider(self):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy.ext.asyncio import AsyncSession

        record = await self.pending()
        original = AsyncSession.commit
        provider_ran = []

        async def fail_after_provider(session):
            if provider_ran:
                raise RuntimeError("synthetic process failure after outbound I/O")
            await original(session)

        def provider(**kwargs):
            provider_ran.append(True)
            return {"ok": True}

        with patch("apps.api.services.operations.run_connector_action", side_effect=provider):
            with patch.object(AsyncSession, "commit", fail_after_provider):
                self.assertEqual((await self.resolve(record)).status_code, 500)
            async with SessionLocal() as session:
                op = await session.get(Operation, uuid.UUID(record["operation_id"]))
                self.assertEqual(op.status, "EXECUTING")
                op.started_at = datetime.now(timezone.utc) - timedelta(minutes=6)
                await session.commit()
            op = (
                await self.client.get(
                    f"/connectors/operations/{record['operation_id']}",
                    headers=self.auth(self.access),
                )
            ).json()
            self.assertEqual(op["status"], "unknown")
            self.assertEqual((await self.resolve(record)).status_code, 409)
            self.assertEqual(len(provider_ran), 1)

    async def test_password_version_revokes_existing_access(self):
        async with SessionLocal() as session:
            await session.execute(
                update(User).where(User.tenant_id == self.tenant).values(auth_version=1)
            )
            await session.commit()
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.auth(self.access))).status_code, 401
        )

    async def test_malformed_signed_claims_fail_closed(self):
        import jwt

        from infrastructure.config import settings

        payload = {
            "sub": "invalid-uuid",
            "tenant_id": str(self.tenant),
            "exp": int(time.time()) + 60,
            "type": "access",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.auth(token))).status_code, 401
        )

    async def test_oauth_state_browser_bound_and_single_use(self):
        from fastapi import HTTPException
        from starlette.requests import Request
        from starlette.responses import Response

        from apps.api.services.oauth import begin_oauth, consume_oauth
        from infrastructure.auth.jwt import create_oauth_state_token, decode_oauth_state_token

        async with SessionLocal() as session:
            user = (
                await session.execute(select(User).where(User.tenant_id == self.tenant))
            ).scalar_one()
            state = create_oauth_state_token(
                user_id=user.id, tenant_id=self.tenant, connector_type="github"
            )
            response = Response()
            await begin_oauth(session, response, state, user)
            cookie = response.headers["set-cookie"].split(";")[0]
        claims = decode_oauth_state_token(state)
        wrong = Request({"type": "http", "headers": []})
        request = Request({"type": "http", "headers": [(b"cookie", cookie.encode())]})
        async with SessionLocal() as session:
            with self.assertRaises(HTTPException):
                await consume_oauth(session, wrong, state, claims)
            await session.rollback()

        async def consume():
            async with SessionLocal() as session:
                try:
                    await consume_oauth(session, request, state, claims)
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        self.assertEqual(sorted(await asyncio.gather(consume(), consume())), [200, 400])

    async def test_python_sdk_uses_durable_operation_contract(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
        from agentguard_sdk import AgentGuardClient

        token, _ = await self.agent()
        async with AgentGuardClient(
            token, base_url="http://test", transport=httpx.ASGITransport(app=app)
        ) as sdk:
            with patch(
                "apps.api.services.operations.run_connector_action",
                return_value=[{"full_name": "acme/allowed"}, {"full_name": "acme/private"}],
            ) as run:
                key = str(uuid.uuid4())
                a = await sdk.run(str(self.credential), "list_repos", {}, idempotency_key=key)
                b = await sdk.run(str(self.credential), "list_repos", {}, idempotency_key=key)
                self.assertEqual(a, b)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(a["result"], [{"full_name": "acme/allowed"}])

    async def test_management_changes_are_transactionally_audited(self):
        token, aid = await self.agent()
        await self.client.post(f"/tenant/agents/{aid}/revoke", headers=self.auth(self.access))
        response = await self.client.get("/tenant/audit/management", headers=self.auth(self.access))
        self.assertEqual(response.status_code, 200)
        events = response.json()
        self.assertTrue(
            {"create_agent", "grant_agent_connector", "revoke_agent"}
            <= {row["action"] for row in events}
        )
        self.assertNotIn(token, response.text)
        other, _ = await self.register()
        self.assertEqual(
            (await self.client.get("/tenant/audit/management", headers=self.auth(other))).json(), []
        )

    async def test_arbitrary_field_names_and_historical_payload_redacted(self):
        response = await self.execute(
            action="invalid_action", params={"synthetic-private-key@example.com": "private"}
        )
        self.assertEqual(response.status_code, 403)
        async with SessionLocal() as session:
            await session.execute(
                update(AuditEvent)
                .where(AuditEvent.tenant_id == self.tenant)
                .values(
                    call_context=json.dumps({"kwargs": {"text": "synthetic-private-value"}}),
                    result_json=json.dumps({"secret": "synthetic-result"}),
                    error_message="synthetic-provider-secret",
                )
            )
            await session.commit()
        response = await self.client.get("/tenant/audit", headers=self.auth(self.access))
        self.assertNotIn("synthetic-", response.text)

    async def test_provider_clients_disable_automatic_mutation_retries(self):
        from connectors.github.connector import GitHubConnector
        from connectors.slack.connector import SlackConnector

        for cls, target in [
            (GitHubConnector, "connectors.github.connector.Github"),
            (SlackConnector, "connectors.slack.connector.WebClient"),
        ]:
            with patch(target) as client:
                connector = cls()
                connector.authenticate({"access_token": "synthetic"})
                connector.connect()
                options = client.call_args.kwargs
                self.assertEqual(options["timeout"], 20)
                self.assertEqual(
                    options.get("retry", options.get("retry_handlers")),
                    0 if cls == GitHubConnector else [],
                )

    async def test_request_logs_exclude_url_tokens_and_payload(self):
        with self.assertLogs("agentguard.http", level="INFO") as logs:
            response = await self.client.get(
                "/not-a-route?token=synthetic-private", headers=self.auth(self.access)
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("X-Request-ID", response.headers)
        self.assertNotIn("synthetic-private", str(logs.output))
        self.assertNotIn(self.access, str(logs.output))

    async def test_revocation_commits_before_execution_claim(self):
        from datetime import datetime, timezone

        from apps.api.services.governance_lock import lock_governance

        token, aid = await self.agent()
        record = await self.pending(token)
        async with SessionLocal() as admin:
            await lock_governance(admin, self.tenant)
            await admin.execute(
                update(Agent).where(Agent.id == aid).values(revoked_at=datetime.now(timezone.utc))
            )
            with patch("apps.api.services.operations.run_connector_action") as run:
                task = asyncio.create_task(self.resolve(record))
                await asyncio.sleep(0.1)
                self.assertFalse(task.done())
                run.assert_not_called()
                await admin.commit()
                response = await asyncio.wait_for(task, 5)
                self.assertEqual(response.json()["execution_status"], "DENIED")
                run.assert_not_called()

    async def test_revoked_grant_blocks_historical_result_access(self):
        token, aid = await self.agent()
        with patch("apps.api.services.operations.run_connector_action", return_value=[]):
            response = await self.execute(token, action="list_repos", params={})
        self.assertEqual(response.status_code, 200)
        await self.client.delete(
            f"/tenant/agents/{aid}/connectors/{self.credential}", headers=self.auth(self.access)
        )
        result = await self.client.get(
            f"/connectors/operations/{response.json()['operation_id']}", headers=self.auth(token)
        )
        self.assertEqual(result.status_code, 403)
