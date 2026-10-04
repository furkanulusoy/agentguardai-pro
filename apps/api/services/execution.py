"""
Outbound connector boundary, called after a durable operation claim. Platform decisions live
in governance.authorize. Real providers have no monitored_resource integration; provider-side
anomaly detection is not implemented.
"""

from __future__ import annotations

import json
from typing import Any

from agentguard.guardrail import AgentGuard
from connectors.registry import CONNECTOR_CLASSES, POLICY_FACTORIES
from infrastructure.database.models import Credential
from infrastructure.secrets import secret_store


def run_connector_action(
    *, credential: Credential, action: str, kwargs: dict[str, Any], approve: bool
) -> Any:
    """Raises agentguard.guardrail.PermissionDenied / ApprovalDenied /
    ConnectorQuarantined, or connectors.base.ConnectorError -- callers
    translate those into HTTP responses or ApprovalRequest.error_message."""
    connector_cls = CONNECTOR_CLASSES[credential.connector_type]
    connector = connector_cls()
    secret = json.loads(secret_store.decrypt(credential.encrypted_secret))
    connector.authenticate(secret)
    connector.connect()
    try:
        policy = POLICY_FACTORIES[credential.connector_type]()
        guard = AgentGuard(
            connector=connector, policy=policy, approval_callback=lambda a, c: approve
        )
        return guard.call(action, **kwargs)
    finally:
        connector.disconnect()
