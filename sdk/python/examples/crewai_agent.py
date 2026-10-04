"""
A real CrewAI integration example -- mirrors
sdk/python/examples/langgraph_agent.py's shape and honesty for a
second Python agent framework. See that file's own docstring and the
package README's "What this is not (yet)" section for why this exists
and what it does/doesn't prove.

What this proves, for real, by actually running: `AgentGuardClient.run()`
can back a genuine CrewAI `BaseTool` (`ExecuteViaAgentGuardTool` below)
-- a real pydantic `args_schema`, an async `_arun` override (CrewAI's
own supported async path, not a sync-over-async hack), invoked via
`tool.arun(...)` the same way a real CrewAI agent's tool-calling step
calls it, and internally making a real HTTP call against the real
platform API (not a stub).

What this deliberately does NOT do, named rather than glossed over:
call a real LLM, or run a real `Crew`/`Agent` task loop. Getting an LLM
to actually pick this tool and choose its arguments requires a real
model provider (OpenAI/Anthropic/...) and a real API key -- a
project-external decision this example doesn't make on a reader's
behalf. What's AgentGuard-specific is everything below
`ExecuteViaAgentGuardTool`; wiring it into a real `Agent`/`Crew` is a
separate, already-solved problem for every CrewAI example on the
internet, not something this project needs to re-prove.

Run it -- deliberately in its OWN virtual environment, not the one you
installed agentguard_sdk into for basic_agent_loop.py/langgraph_agent.py:
CrewAI's dependency tree pulled in a stack of transitive packages
(chromadb, onnxruntime, opentelemetry, its own pinned `mcp`, ...) that,
tried in this parent repo's shared dev venv, downgraded `mcp` far
enough to break apps/mcp_server's own import -- found by actually
running the full test suite after installing crewai, not by
inspection. There's nothing wrong with CrewAI itself; it's simply not
meant to share a Python environment with an unrelated FastAPI platform
and MCP server (from this repo, with the platform's own API running --
`docker compose up -d && uvicorn apps.api.main:app` -- and a real Agent
key from the dashboard's Agents page):

    python -m venv sdk/python/.venv-crewai
    sdk/python/.venv-crewai/Scripts/pip install -e sdk/python
    sdk/python/.venv-crewai/Scripts/pip install crewai
    AGENTGUARD_AGENT_KEY=agk_... sdk/python/.venv-crewai/Scripts/python \
        sdk/python/examples/crewai_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout


class ExecuteViaAgentGuardArgs(BaseModel):
    connector_id: str = Field(description="A connector id from list_connectors()")
    action: str = Field(description='An action the connector supports, e.g. "list_repos"')
    params: dict[str, Any] = Field(
        default_factory=dict, description="The action's keyword arguments"
    )


class ExecuteViaAgentGuardTool(BaseTool):
    """A real CrewAI BaseTool subclass -- name/description/args_schema
    are exactly what CrewAI's own agent-loop introspects to hand to an
    LLM's tool-calling API. `client` is a plain runtime attribute (not
    a pydantic field an LLM would ever see or set), assigned in
    __init__ after BaseTool's own pydantic init runs."""

    name: str = "execute_via_agentguard"
    description: str = (
        "Execute a guarded action against a connected external service through "
        "AgentGuard. Blocks until AgentGuard resolves the call -- either it runs "
        "immediately, or a human approves/denies it from the AgentGuard dashboard."
    )
    args_schema: type[BaseModel] = ExecuteViaAgentGuardArgs
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, client: AgentGuardClient, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_client", client)

    def _run(self, connector_id: str, action: str, params: dict[str, Any] | None = None) -> str:
        # CrewAI's BaseTool requires a sync _run to exist even when
        # _arun is overridden (it's the abstract method) -- real
        # callers use arun() below, which CrewAI's own async agent
        # loop calls directly.
        raise NotImplementedError(
            "Use arun() -- this tool is async-only, like AgentGuardClient itself."
        )

    async def _arun(
        self, connector_id: str, action: str, params: dict[str, Any] | None = None
    ) -> str:
        client: AgentGuardClient = self._client  # type: ignore[attr-defined]
        try:
            outcome = await client.run(connector_id, action, params or {})
        except AgentGuardDenied:
            return "A human denied this action from the AgentGuard Approvals page."
        except AgentGuardTimeout:
            return "Nobody resolved the approval request in time."
        except AgentGuardError as exc:
            return f"AgentGuard rejected this action: {exc}"
        return str(outcome["result"])


async def main() -> None:
    agent_key = os.environ.get("AGENTGUARD_AGENT_KEY")
    if not agent_key:
        print(
            "Set AGENTGUARD_AGENT_KEY to a real Agent key from the dashboard's Agents "
            "page (or POST /tenant/agents) first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    base_url = os.environ.get("AGENTGUARD_API_BASE_URL", "http://localhost:5000/api")
    connector_type = os.environ.get("AGENTGUARD_CONNECTOR", "github")
    action = os.environ.get("AGENTGUARD_ACTION", "list_repos")

    async with AgentGuardClient(agent_key, base_url) as client:
        tool = ExecuteViaAgentGuardTool(client)

        # Proves the tool is a real, correctly-shaped CrewAI BaseTool --
        # name/description/args_schema all real, exactly what a real
        # CrewAI Agent's tool-calling introspection is shown.
        print(f"Tool name: {tool.name}")
        print(f"Tool args schema: {tool.args_schema.model_json_schema()}")

        connectors = await client.list_connectors()
        match = next((c for c in connectors if c["connector_type"] == connector_type), None)
        if match is None:
            print(
                f"No connected '{connector_type}' connector for this tenant -- connect one "
                f"from the dashboard's Integrations page first."
            )
            return

        # The real call: invoked exactly the way CrewAI's own async
        # agent loop invokes a tool call an LLM produced, except the
        # arguments are hard-coded here instead of coming from a model.
        print(f"Invoking the tool for real: {connector_type}.{action}...")
        result = await tool.arun(connector_id=match["id"], action=action, params={})
        print(f"Tool result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
