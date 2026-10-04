"""
Standalone async platform client. Explicit agent identity, caller-supplied idempotency keys
and durable operation polling. Unknown outcomes are never automatically retried.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import TracebackType
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:5000/api"
AGENT_KEY_PREFIX = "agk_"
_POLL_INTERVAL_SECONDS = 2.0
_MAX_WAIT_SECONDS = 30 * 60


class AgentGuardError(RuntimeError):
    """Raised for any non-2xx response from the platform API that isn't
    one of the more specific errors below."""


class AgentGuardDenied(AgentGuardError):
    """A human denied this action from the AgentGuard dashboard's
    Approvals page."""


class AgentGuardTimeout(AgentGuardError):
    """Nobody resolved this approval request (or the platform itself
    expired it) within the wait budget."""


class AgentGuardClient:
    def __init__(
        self,
        agent_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not agent_key.startswith(AGENT_KEY_PREFIX):
            raise ValueError(
                f"agent_key doesn't look like an AgentGuard agent key (expected an "
                f"'{AGENT_KEY_PREFIX}' prefix) -- mint one from the dashboard's Agents "
                f"page, or POST /tenant/agents. This client only authenticates as an "
                f"Agent, not a human's own login."
            )
        # transport is exposed for testing (httpx.ASGITransport mounted
        # directly on a FastAPI app, no real network/server needed) --
        # normal callers never pass it; httpx picks a real network
        # transport itself.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {agent_key}"},
            transport=transport,
        )

    async def __aenter__(self) -> AgentGuardClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_connectors(self) -> list[dict[str, Any]]:
        """What's connected for this tenant right now."""
        resp = await self._client.get("/connectors")
        self._raise_for_status(resp)
        return resp.json()  # type: ignore[no-any-return]

    async def run(
        self,
        connector_id: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        max_wait: float = _MAX_WAIT_SECONDS,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run with a durable operation key. Reuse the key after a transport timeout."""
        if poll_interval <= 0 or max_wait <= 0:
            raise ValueError("Polling interval and wait must be positive")
        operation_key = idempotency_key or str(uuid.uuid4())
        resp = await self._client.post(
            f"/connectors/{connector_id}/execute",
            json={"action": action, "params": params or {}},
            headers={"Idempotency-Key": operation_key},
        )
        self._raise_for_status(resp)
        data = resp.json()
        deadline = time.monotonic() + max_wait
        while True:
            state = data["status"]
            if state == "completed":
                return data
            if state == "denied":
                raise AgentGuardDenied(data.get("error") or "Action denied")
            if state == "expired":
                raise AgentGuardTimeout("Approval expired")
            if state not in {"pending_approval", "ready", "executing"}:
                error_code = data.get("error") or "EXECUTION_FAILED"
                raise AgentGuardError(
                    f"Execution {state} ({error_code}); "
                    f"do not retry with a new key. Operation: {data.get('operation_id')}"
                )
            if time.monotonic() >= deadline:
                raise AgentGuardTimeout(
                    f"Wait ended; operation may still finish. Reuse idempotency key {operation_key}"
                )
            await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
            resp = await self._client.get(f"/connectors/operations/{data['operation_id']}")
            self._raise_for_status(resp)
            data = resp.json()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise AgentGuardError(
                f"AgentGuard rejected this request ({resp.status_code}): {resp.text}"
            )
