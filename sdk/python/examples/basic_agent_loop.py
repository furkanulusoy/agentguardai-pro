"""
A minimal, runnable example of an agent loop calling AgentGuard through
agentguard_sdk -- deliberately framework-agnostic (see the package
README's "What this is not (yet)" section for why this isn't a
LangGraph/CrewAI-specific example). Any framework's tool-calling
convention wraps a plain async function like `run_action` below the
same way; this is the part that's actually AgentGuard-specific.

Run it (from this repo, with the platform's own API running --
`docker compose up -d && uvicorn apps.api.main:app` -- and a real Agent
key from the dashboard's Agents page):

    AGENTGUARD_AGENT_KEY=agk_... python sdk/python/examples/basic_agent_loop.py

Or, to see the human-approval path (rather than the auto-allowed one):

    AGENTGUARD_AGENT_KEY=agk_... AGENTGUARD_ACTION=close_issue \
        python sdk/python/examples/basic_agent_loop.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout


async def run_action(client: AgentGuardClient, connector_type: str, action: str) -> None:
    """The one function a real agent framework's tool-calling
    convention would wrap -- everything above this is just example
    plumbing (finding a connector, printing output)."""
    connectors = await client.list_connectors()
    match = next((c for c in connectors if c["connector_type"] == connector_type), None)
    if match is None:
        print(f"No connected '{connector_type}' connector for this tenant -- connect one "
              f"from the dashboard's Integrations page first.")
        return

    print(f"Calling {connector_type}.{action} through AgentGuard...")
    try:
        outcome = await client.run(match["id"], action)
    except AgentGuardDenied:
        print("A human denied this action from the AgentGuard Approvals page.")
        return
    except AgentGuardTimeout:
        print("Nobody resolved the approval request in time.")
        return
    except AgentGuardError as exc:
        print(f"AgentGuard rejected this action: {exc}")
        return

    print(f"Result: {outcome['result']}")


async def main() -> None:
    agent_key = os.environ.get("AGENTGUARD_AGENT_KEY")
    if not agent_key:
        print("Set AGENTGUARD_AGENT_KEY to a real Agent key from the dashboard's Agents "
              "page (or POST /tenant/agents) first.", file=sys.stderr)
        raise SystemExit(1)

    base_url = os.environ.get("AGENTGUARD_API_BASE_URL", "http://localhost:5000/api")
    connector_type = os.environ.get("AGENTGUARD_CONNECTOR", "github")
    action = os.environ.get("AGENTGUARD_ACTION", "list_repos")

    async with AgentGuardClient(agent_key, base_url) as client:
        await run_action(client, connector_type, action)


if __name__ == "__main__":
    asyncio.run(main())
