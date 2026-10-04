"""
Reference deployment: AgentGuard behind a small HTTP API.

This is meant to show the SHAPE a real deployment takes -- an agent (or
anything acting on its behalf) calls a tool over HTTP; sensitive actions
block that request until a human resolves them from a separate request
(what a real Slack-button or web-approval-queue webhook would do).

Honest limitations, on purpose:
  - Mostly in-memory. Restart the process and the inbox and the pending
    approvals reset. Quarantine state and the audit log are the
    exceptions -- both survive a restart now (SQLite and JSON Lines
    respectively). A real deployment still needs a real datastore for
    everything else (policies, per-tenant config, pending approvals).
  - Auth is opt-in, not on by default. Set AGENTGUARD_SERVER_API_KEY (or
    pass api_key= to create_app()) to require an
    `Authorization: Bearer <key>` header on /tools/*, /approvals/*, and
    /audit -- /healthz stays open (monitoring/load-balancer probes need
    that). With no key configured, behavior is unchanged from before.
    This is a single shared key, not per-agent/per-human identity or
    authorization scopes -- a real deployment still needs that.
  - Single mock connector (the same fake inbox from mock_services/),
    wired to a policy loaded from examples/policies/inbox.json. A real
    deployment needs per-tenant, per-connector configuration.
  - Flask's built-in dev server. A real deployment needs a real WSGI
    server (gunicorn/uwsgi) behind a real reverse proxy, and the
    request that blocks on approval needs a request-level timeout
    that's actually enforced by that stack, not just by QueueApprover.

See docs/ROADMAP_TO_PRODUCTION.md for the full list. This file exists to
make the deployment shape concrete and runnable, not to claim it's ready
to hold a real account's credentials.

Run it:
    pip install agentguard[server]
    python3 -m server.app
Optionally, set AGENTGUARD_SLACK_WEBHOOK_URL to a Slack Incoming Webhook URL
first to get a one-way Slack notification whenever a sensitive action needs
approval (see agentguard/approval.py:SlackNotifyApprover) -- approval itself
still happens through /approvals/<id> below, not a Slack button.
Optionally, set AGENTGUARD_SERVER_API_KEY to require an
`Authorization: Bearer <key>` header on /tools/*, /approvals/*, and
/audit (add `-H "Authorization: Bearer <key>"` to every curl below).
Then, in another terminal:
    curl -X POST localhost:8000/tools/list_messages -d '{}' -H 'content-type: application/json'
    curl -X POST localhost:8000/tools/send_message \
         -d '{"args": ["someone@example.com", "hi", "hello"]}' \
         -H 'content-type: application/json'
    # in a third terminal, while the second command is blocked waiting:
    curl localhost:8000/approvals/pending
    curl -X POST localhost:8000/approvals/1 \
         -d '{"approved": true}' -H 'content-type: application/json'
"""
from __future__ import annotations

import functools
import hmac
import os
from collections.abc import Callable
from pathlib import Path

try:
    from flask import Flask, jsonify, request
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "server/app.py requires Flask. Install it with: pip install agentguard[server]"
    ) from e

from agentguard import AgentGuard, ApprovalDenied, ConnectorQuarantined, PermissionDenied, Policy
from agentguard.approval import QueueApprover, SlackNotifyApprover
from agentguard.audit_log import JsonlAuditLog
from agentguard.monitor import CallRecorder
from agentguard.quarantine_store import SqliteQuarantineStore
from mock_services.inbox import MockInbox

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "examples" / "policies" / "inbox.json"
DEFAULT_AUDIT_LOG_PATH = REPO_ROOT / "server_audit.jsonl"
DEFAULT_QUARANTINE_DB_PATH = REPO_ROOT / "server_quarantine.sqlite3"


def create_app(
    policy_path: Path = DEFAULT_POLICY_PATH,
    audit_log_path: Path = DEFAULT_AUDIT_LOG_PATH,
    approval_timeout_seconds: float = 120.0,
    slack_webhook_url: str | None = None,
    quarantine_db_path: Path = DEFAULT_QUARANTINE_DB_PATH,
    api_key: str | None = None,
) -> Flask:
    app = Flask(__name__)

    # Opt-in: with no key configured (neither api_key= nor the env var),
    # every route below behaves exactly as it did before this existed.
    resolved_api_key = api_key or os.environ.get("AGENTGUARD_SERVER_API_KEY")

    def require_api_key(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if resolved_api_key:
                auth_header = request.headers.get("Authorization", "")
                token = (
                    auth_header[len("Bearer ") :]
                    if auth_header.startswith("Bearer ")
                    else None
                )
                # hmac.compare_digest for a constant-time comparison -- a
                # plain `!=` here would leak how many leading characters of
                # the key a guess got right via response-time differences.
                if token is None or not hmac.compare_digest(token, resolved_api_key):
                    return (
                        jsonify(
                            {
                                "error": "unauthorized",
                                "detail": "gecerli bir 'Authorization: Bearer <key>' "
                                "header'i gerekli",
                            }
                        ),
                        401,
                    )
            return view(*args, **kwargs)

        return wrapped

    # CallRecorder wraps the real resource so AgentGuard can compare what the
    # guard authorized against what actually happened to it -- the same
    # pattern demo/scenario_c_malicious_connector.py uses to catch a
    # connector doing something undeclared. Without this, the reference
    # server only enforces scope + approval, not connector integrity.
    inbox = CallRecorder(MockInbox(), label="inbox")
    policy = Policy.from_file(policy_path)
    quarantine_store = SqliteQuarantineStore(quarantine_db_path)

    # queue_approver is always the thing /approvals/* talks to. If a Slack
    # webhook URL is configured (explicitly, or via AGENTGUARD_SLACK_WEBHOOK_URL),
    # it's wrapped with a one-way Slack notification -- see
    # agentguard/approval.py:SlackNotifyApprover for why this is a
    # notification, not an interactive button.
    queue_approver = QueueApprover(timeout_seconds=approval_timeout_seconds)
    approval_callback: Callable[[str, dict], bool] = queue_approver
    webhook_url = slack_webhook_url or os.environ.get("AGENTGUARD_SLACK_WEBHOOK_URL")
    if webhook_url:
        approval_callback = SlackNotifyApprover(webhook_url, wrapped=queue_approver)

    audit_log = JsonlAuditLog(audit_log_path)
    guard = AgentGuard(
        connector=inbox,
        policy=policy,
        approval_callback=approval_callback,
        audit_log=audit_log,
        monitored_resource=inbox,
        quarantine_store=quarantine_store,
    )

    # Exposed on the app so tests (and a real caller) can reach the
    # approver/audit log without reconstructing internal state. This is
    # always queue_approver itself (not the Slack-wrapped callable) --
    # /approvals/* needs .pending()/.resolve(), which only queue_approver has.
    app.config["GUARD"] = guard
    app.config["APPROVER"] = queue_approver
    app.config["AUDIT_LOG"] = audit_log
    app.config["QUARANTINE_STORE"] = quarantine_store

    @app.post("/tools/<action>")
    @require_api_key
    def call_tool(action: str):
        body = request.get_json(silent=True) or {}
        args = body.get("args", [])
        kwargs = body.get("kwargs", {})
        if not isinstance(args, list):
            return jsonify({"error": "invalid_request", "detail": "'args' must be a list"}), 400
        if not isinstance(kwargs, dict):
            return (
                jsonify({"error": "invalid_request", "detail": "'kwargs' must be an object"}),
                400,
            )
        try:
            result = guard.call(action, *args, **kwargs)
        except PermissionDenied as e:
            return jsonify({"error": "permission_denied", "detail": str(e)}), 403
        except ApprovalDenied as e:
            return jsonify({"error": "approval_denied", "detail": str(e)}), 403
        except ConnectorQuarantined as e:
            return jsonify({"error": "connector_quarantined", "detail": str(e)}), 423
        return jsonify({"result": result})

    @app.get("/approvals/pending")
    @require_api_key
    def list_pending():
        return jsonify(queue_approver.pending())

    @app.post("/approvals/<int:approval_id>")
    @require_api_key
    def resolve_approval(approval_id: int):
        body = request.get_json(silent=True) or {}
        approved = bool(body.get("approved", False))
        found = queue_approver.resolve(approval_id, approved)
        if not found:
            return jsonify({"error": "not_found"}), 404
        return jsonify({"resolved": approval_id, "approved": approved})

    @app.get("/audit")
    @require_api_key
    def get_audit():
        return jsonify(JsonlAuditLog.read_all(audit_log_path))

    @app.get("/healthz")
    def healthz():
        # Deliberately NOT behind require_api_key -- monitoring/load-balancer
        # health probes need to reach this without a credential.
        return jsonify({"status": "ok", "quarantined": guard.quarantined})

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8000, threaded=True)
