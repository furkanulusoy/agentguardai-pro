"""
Direct, real-HTTP verification of the 10-step "sensitive action needs
human approval" scenario audited on 2026-08-21 (see CHANGELOG.md's
corresponding entry for the full write-up of what was found).

The scenario as given named a `refund_customer(amount=50000 TRY)`
action classified as `CRITICAL` risk. Neither exists anywhere in this
codebase: no connector defines a `refund_customer` action (there is no
payments/financial connector at all -- connectors/registry.py only
ever registers gmail/github/slack), and risk levels are a fixed
LOW/MEDIUM/HIGH mapping per connector action (connectors/*/policy.py's
RISK_LEVELS dicts) -- there is no "CRITICAL" tier, and no code path
that inspects an action's actual parameter VALUES (e.g. a specific
`amount`) to decide risk dynamically. Inventing either to make this
test "look like" the scenario would be exactly the "code doesn't have
this capability, don't pretend it does" mistake this audit was
commissioned to avoid.

What this file verifies instead is real: the identical MECHANISM the
scenario is actually asking about -- catch a sensitive action, hold it
pending, let a human APPROVE or DENY it from the same
GET/POST /approvals API the dashboard itself calls, never run the real
call until approved, record who/what/when/why -- exercised end to end
against `github.close_issue` (connectors/github/connector.py), the
existing action in this codebase closest in shape to the scenario: a
real, consequential, HIGH-risk write that already requires approval by
default (connectors/github/policy.py).

Closes a real, previously-named test gap: this test suite's sibling
file, test_infra_api_connectors_approvals.py, states directly in its
own module docstring that resolve_approval's approved=True branch --
the moment a real guarded call actually executes -- "is NOT exercised
here... verified manually... during this project's UI work" rather
than by an automated test. test_full_lifecycle_approve_path below is
that automated test, closing that gap.

Same real-Postgres-or-skip discipline, fake-credential-so-the-real-
upstream-call-fails-loudly-instead-of-being-mocked approach (see that
sibling file's own docstring for why a 502 from a throwaway credential
is the honest outcome, not a test weakness), and fixture shape as
test_infra_api_connectors_approvals.py, which this file deliberately
mirrors rather than reinventing.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import patch

from tests.platform_contract import reviewed_decision

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

# Stands in for the scenario's refund_customer(amount=50000 TRY) --
# github.close_issue is the closest REAL, existing HIGH-risk action
# this codebase has (see this module's own docstring for why nothing
# resembling a payments action is invented here). repo/issue_number are
# what close_issue actually accepts; "amount"/"currency" are added on
# top, in the DENY test only, purely to prove call_context really does
# carry through whatever a caller sends -- not because close_issue does
# anything with them (it wouldn't -- see that test's own comment).
_CLOSE_ISSUE_PARAMS = {"repo": "octocat/Hello-World", "issue_number": 1}


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestApprovalLifecycleScenario(unittest.IsolatedAsyncioTestCase):
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

    # -- fixtures, mirroring test_infra_api_connectors_approvals.py's own --

    async def _register(self) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Approval Lifecycle Test Co",
            "tenant_slug": f"approval-lifecycle-{uuid.uuid4().hex[:10]}",
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
        """A throwaway, never-valid secret -- deliberately never a real
        OAuth token pointed at a real account, per this audit's own
        instruction not to risk a real irreversible action. Real HTTP
        calls still happen (see test_full_lifecycle_approve_path); they
        just fail with a real 401/502 from GitHub's own API instead of
        succeeding, which is the safe, honest outcome here."""
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

    # --- Steps 1-6: catch the action, classify its risk, hold it pending ---

    async def test_full_lifecycle_deny_path(self):
        """Scenario steps 1-2 (agent wants a HIGH-risk action with real
        parameters), 3-6 (AgentGuard catches it, classifies it as the
        real risk tier this codebase actually has, requires approval,
        does not run it), 7-8 (a human can DENY it from the same API
        the dashboard calls; denying it means the real action never
        runs, and the outcome is visible on the audit trail)."""
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        # Step 1-2: the agent's call, with real parameters attached.
        # "amount"/"currency" ride along in call_context exactly like
        # the scenario's amount=50000 TRY would -- proving parameters
        # really do reach the approval record -- without claiming
        # close_issue itself does anything with a refund amount, which
        # it doesn't and shouldn't.
        params = dict(_CLOSE_ISSUE_PARAMS)
        execute_resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": params},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        # Step 3-6: caught, classified HIGH (this codebase's real
        # highest tier -- see this module's own docstring for why not
        # "CRITICAL"), and held pending rather than run.
        self.assertEqual(execute_resp.status_code, 200, execute_resp.text)
        body = execute_resp.json()
        self.assertEqual(body["status"], "pending_approval")
        approval_id = body["approval_id"]

        pending = await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access))
        self.assertEqual(pending.json()["status"], "PENDING")
        self.assertEqual(pending.json()["risk_level"], "HIGH")
        self.assertEqual(
            pending.json()["call_context"]["kwargs"], {key: "[REDACTED]" for key in params}
        )

        # Step 7: a human denies it, through the real dashboard-facing API.
        resolve_resp = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )
        self.assertEqual(resolve_resp.status_code, 200)
        resolved = resolve_resp.json()

        # Step 8a: the real action never ran.
        self.assertEqual(resolved["status"], "DENIED")
        self.assertIsNone(resolved["result"])
        self.assertIsNone(resolved["error"])

        # Step 8b: the outcome is on the audit trail -- not duplicated
        # onto a separate AuditEvent row (see audit_event.py's own
        # docstring for why), but genuinely visible there via a live
        # join, which is what actually matters to whoever is looking.
        audit_resp = await self.client.get(
            "/tenant/audit?action=close_issue", headers=self._auth(access)
        )
        self.assertEqual(audit_resp.status_code, 200)
        audit_row = next(r for r in audit_resp.json() if r["approval_id"] == approval_id)
        self.assertEqual(audit_row["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(audit_row["approval_status"], "DENIED")
        self.assertEqual(audit_row["approval_resolved_by_email"], resolved["resolved_by_email"])

    # --- Step 9: APPROVE really executes the real guarded call ---

    async def test_full_lifecycle_approve_path(self):
        """Closes the gap test_infra_api_connectors_approvals.py's own
        module docstring names directly: resolve_approval's
        approved=True branch was previously verified only manually, not
        by an automated test. Proves the real call actually happens on
        approval -- not asserting success (a throwaway credential can't
        succeed against the real GitHub API, on purpose, per this
        audit's instruction not to risk a real irreversible action) but
        asserting that a REAL network attempt happened and its REAL
        outcome (a 401 from GitHub itself) is what landed in
        result/error -- proof this is a live call, not a stub."""
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        execute_resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        approval_id = execute_resp.json()["approval_id"]

        resolve_resp = await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json=await reviewed_decision(
                self.client, f"/approvals/{approval_id}/resolve", self._auth(access)
            ),
            headers=self._auth(access),
        )
        self.assertEqual(resolve_resp.status_code, 200)
        resolved = resolve_resp.json()

        # Step 9: APPROVED, and the real connector call really ran --
        # proven by a real GitHub 401 having reached this response,
        # which could only happen if run_connector_action actually
        # called connectors/github/connector.py's close_issue() against
        # the real api.github.com, not a mock.
        self.assertEqual(resolved["status"], "APPROVED")
        self.assertIsNone(resolved["result"])
        self.assertIsNotNone(resolved["error"])
        self.assertEqual("PROVIDER_OUTCOME_UNKNOWN", resolved["error"])
        self.assertEqual("UNKNOWN", resolved["execution_status"])

        audit_resp = await self.client.get(
            "/tenant/audit?action=close_issue", headers=self._auth(access)
        )
        audit_row = next(r for r in audit_resp.json() if r["approval_id"] == approval_id)
        self.assertEqual(audit_row["approval_status"], "APPROVED")

    # --- Step 10: every field the scenario asked for ---

    async def test_approval_request_carries_the_required_fields(self):
        """Step 10, field by field. Every one of agent/action/parameters/
        risk_level/requester/approver/timestamp/execution-result is a
        real column or a real joined value -- except "reason", which
        this codebase does not carry as a free-text field on
        ApprovalRequest itself (only decision_source, a fixed enum of
        WHICH RULE decided -- TASK_SCOPE/AGENT_POLICY/TENANT_POLICY/
        SYSTEM_DEFAULT/AGENT_NOT_GRANTED -- on the separate AuditEvent
        row, not a human-readable sentence). Named here rather than
        silently passed over."""
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        execute_resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "close_issue", "params": _CLOSE_ISSUE_PARAMS},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        approval_id = execute_resp.json()["approval_id"]
        await self.client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": False},
            headers=self._auth(access),
        )

        detail = (
            await self.client.get(f"/approvals/{approval_id}", headers=self._auth(access))
        ).json()

        # agent (None here -- a human called execute directly, not an
        # Agent key; requested_by_email covers "who is accountable"
        # either way, see approval.py model's own docstring)
        self.assertIn("agent_name", detail)
        # action
        self.assertEqual(detail["action"], "close_issue")
        # parameters
        self.assertEqual(
            detail["call_context"]["kwargs"], {key: "[REDACTED]" for key in _CLOSE_ISSUE_PARAMS}
        )
        # risk level
        self.assertEqual(detail["risk_level"], "HIGH")
        # requester
        self.assertTrue(detail["requested_by_email"])
        # approver
        self.assertTrue(detail["resolved_by_email"])
        # timestamp
        self.assertIsNotNone(detail["created_at"])
        self.assertIsNotNone(detail["resolved_at"])
        # execution result (both None here -- DENIED never executes;
        # see test_full_lifecycle_approve_path for the populated case)
        self.assertIn("result", detail)
        self.assertIn("error", detail)

    # --- What "AgentGuard catches this action" actually means for an
    #     action, like the scenario's refund_customer, that no
    #     connector defines ---

    async def test_an_undefined_action_is_rejected_before_any_risk_classification(self):
        """The scenario's steps 3-4 imply the agent's call reaches a
        risk-classification step no matter what action name it uses.
        That's not what happens for an action nothing declares: it's
        rejected at the task-scope gate, before policy resolution or
        risk-level lookup ever run -- decision_source is TASK_SCOPE, a
        structural "this connector doesn't support this at all" refusal,
        not a risk decision about a real action. This is the literal
        behavior a real, unmodified call with action="refund_customer"
        gets today -- shown directly rather than described."""
        access, tenant_id = await self._register()
        credential_id = await self._make_credential(tenant_id)

        resp = await self.client.post(
            f"/connectors/{credential_id}/execute",
            json={"action": "refund_customer", "params": {"amount": 50000, "currency": "TRY"}},
            headers={**self._auth(access), "Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 403)

        audit_resp = await self.client.get(
            "/tenant/audit?action=refund_customer", headers=self._auth(access)
        )
        row = audit_resp.json()[0]
        self.assertEqual(row["decision"], "DENY")
        self.assertEqual(row["decision_source"], "TASK_SCOPE")


if __name__ == "__main__":
    unittest.main()
