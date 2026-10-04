"""
MCP HTTP adapter for the platform. Uses the same authorization and durable operation endpoints
as SDKs. Agent keys are preferred. The optional human login uses a cookie jar. No policy or
provider anomaly detection is implemented in this adapter.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

API_BASE_URL = os.environ.get("AGENTGUARD_API_BASE_URL", "http://localhost:5000/api")
# Preferred: a real Agent identity (see module docstring's Auth section).
AGENT_KEY = os.environ.get("AGENTGUARD_AGENT_KEY")
# Fallback: a human's own login.
EMAIL = os.environ.get("AGENTGUARD_EMAIL")
PASSWORD = os.environ.get("AGENTGUARD_PASSWORD")
# Dev/testing shortcut only -- see the module docstring's Auth section.
_DEV_ACCESS_TOKEN = os.environ.get("AGENTGUARD_ACCESS_TOKEN")
_DEV_REFRESH_TOKEN = os.environ.get("AGENTGUARD_REFRESH_TOKEN")

mcp = MCPServer("agentguard")


class AuthError(RuntimeError):
    pass


class PlatformClient:
    """Owns the current access/refresh token pair and refreshes on a
    401 -- same idea as apps/web/src/api/client.ts's response
    interceptor: one shared in-flight refresh so concurrent tool calls
    don't each fire their own /auth/refresh and race its rotation.

    An agent key (AGENT_KEY) is not a JWT and never rotates -- it IS the
    bearer token, permanently, until revoked from the dashboard -- so
    there is no login/refresh dance to do at all in that mode. A 401
    with an agent key set means the key was revoked or is wrong, not
    "needs a refresh"; request() surfaces that as-is rather than
    retrying, so the real cause reaches the caller instead of looping."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0)
        self._access_token: str | None = AGENT_KEY or _DEV_ACCESS_TOKEN
        self._refresh_token: str | None = _DEV_REFRESH_TOKEN
        self._refresh_lock = asyncio.Lock()

    async def _login(self) -> None:
        if AGENT_KEY:
            raise AuthError(
                "AgentGuard rejected AGENTGUARD_AGENT_KEY (401) -- it may have been "
                "revoked. Mint a new one from the dashboard's Agents page."
            )
        if not (EMAIL and PASSWORD):
            raise AuthError(
                "Not authenticated -- set AGENTGUARD_AGENT_KEY (preferred, an Agent's "
                "own identity), AGENTGUARD_EMAIL/AGENTGUARD_PASSWORD, or "
                "AGENTGUARD_ACCESS_TOKEN/AGENTGUARD_REFRESH_TOKEN for local testing."
            )
        resp = await self._client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = self._client.cookies.get("agentguard_refresh_token")

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            if AGENT_KEY or self._refresh_token is None:
                await self._login()
                return
            resp = await self._client.post(
                "/auth/refresh", json={"refresh_token": self._refresh_token}
            )
            # Only a definitive "this token is no good" (401 -- expired,
            # already rotated, revoked) should fall back to a full
            # password login. A transient server error (5xx) or a
            # network hiccup falling through to _login() would mean
            # holding EMAIL/PASSWORD in memory gets used far more often
            # than intended -- flagged by an independent security
            # review, not found by a real failure. Anything else here
            # is a real, unexpected error and should surface as one.
            if resp.status_code == 401:
                await self._login()
                return
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._refresh_token = self._client.cookies.get("agentguard_refresh_token")

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._access_token is None:
            await self._login()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        resp = await self._client.request(method, path, headers=headers, **kwargs)
        if resp.status_code == 401:
            await self._refresh()
            headers["Authorization"] = f"Bearer {self._access_token}"
            resp = await self._client.request(method, path, headers=headers, **kwargs)
        return resp


_platform = PlatformClient()


@mcp.tool()
async def list_connectors() -> list[dict[str, Any]]:
    """List the external services (Gmail, ...) connected to this AgentGuard tenant."""
    resp = await _platform.request("GET", "/connectors")
    resp.raise_for_status()
    return resp.json()


# Matches apps/api/services/approvals.py's EXPIRY_MINUTES -- no point
# polling past the point the platform itself gives up on the request.
_APPROVAL_POLL_INTERVAL_SECONDS = 2.0
_APPROVAL_MAX_WAIT_SECONDS = 30 * 60


@mcp.tool()
async def execute_connector_action(
    connector_id: str,
    action: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Run one action against a connected service through AgentGuard's guardrail.

    Some actions run immediately; others require a human to approve
    them first from the AgentGuard dashboard's Approvals page -- this
    call waits (polling, up to ~30 minutes) until that happens, then
    returns the real result, or raises if the human denies it, the
    action is out of the connector's policy scope, or the wait expires.
    """
    resp = await _platform.request(
        "POST",
        f"/connectors/{connector_id}/execute",
        json={"action": action, "params": params or {}},
        headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
    )
    resp.raise_for_status()
    data = resp.json()
    deadline = asyncio.get_running_loop().time() + _APPROVAL_MAX_WAIT_SECONDS
    while True:
        if data["status"] == "completed":
            return data
        if data["status"] not in {"pending_approval", "ready", "executing"}:
            raise RuntimeError(
                f"Execution {data['status']}; "
                f"operation {data.get('operation_id')}. Never blindly retry."
            )
        if asyncio.get_running_loop().time() >= deadline:
            return data
        await asyncio.sleep(_APPROVAL_POLL_INTERVAL_SECONDS)
        resp = await _platform.request("GET", f"/connectors/operations/{data['operation_id']}")
        resp.raise_for_status()
        data = resp.json()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
