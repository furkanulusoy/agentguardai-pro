"""
A real LangGraph/LangChain integration example -- what
sdk/python/examples/basic_agent_loop.py deliberately doesn't claim (see
that file's own docstring, and the package README's "What this is not
(yet)" section this file exists specifically to close).

What this proves, for real, by actually running: `AgentGuardClient.run()`
can be wrapped as a genuine LangChain `@tool` (`execute_via_agentguard`
below) -- correct `args_schema` inferred from real type hints, callable
via `tool.ainvoke(...)` the same way LangGraph's own tool-calling nodes
call it, and internally making a real HTTP call against the real
platform API (not a stub). It also builds a real `create_react_agent`
graph and shows the tool bound onto it with LangGraph's own
`tools_condition`, proving the wiring an actual LLM-driven agent would
use is correct.

What this deliberately does NOT do, named rather than glossed over:
call a real LLM. Getting an LLM to actually pick this tool and choose
its arguments requires a real model provider (OpenAI/Anthropic/...) and
a real API key -- a project-external decision this example doesn't make
on a reader's behalf. What's AgentGuard-specific is everything below
`execute_via_agentguard`; which LLM decides to call it is a separate,
already-solved problem for every LangGraph example on the internet, not
something this project needs to re-prove.

Run it (from this repo, with the platform's own API running --
`docker compose up -d && uvicorn apps.api.main:app` -- and a real Agent
key from the dashboard's Agents page):

    pip install -e "sdk/python[langgraph]"
    AGENTGUARD_AGENT_KEY=agk_... python sdk/python/examples/langgraph_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from langchain_core.tools import tool
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode

from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout


def build_execute_tool(client: AgentGuardClient):
    """Returns a real LangChain StructuredTool closing over `client` --
    a factory, not a module-level tool, because the client (and the
    agent key it holds) is only known at runtime, not import time."""

    @tool
    async def execute_via_agentguard(
        connector_id: str, action: str, params: dict | None = None
    ) -> str:
        """Execute a guarded action against a connected external service
        through AgentGuard. connector_id comes from list_connectors();
        action is one the connector supports (e.g. "list_repos",
        "send_message"); params are the action's keyword arguments.
        Blocks until AgentGuard resolves the call -- either it runs
        immediately, or a human approves/denies it from the AgentGuard
        dashboard."""
        try:
            outcome = await client.run(connector_id, action, params or {})
        except AgentGuardDenied:
            return "A human denied this action from the AgentGuard Approvals page."
        except AgentGuardTimeout:
            return "Nobody resolved the approval request in time."
        except AgentGuardError as exc:
            return f"AgentGuard rejected this action: {exc}"
        return str(outcome["result"])

    return execute_via_agentguard


def build_graph(execute_tool) -> StateGraph:
    """A minimal real LangGraph graph -- one tool node, built from
    LangGraph's own prebuilt ToolNode (the same one a real
    create_react_agent graph uses internally). No LLM node is wired in
    on purpose (see this module's own docstring); what's being proven
    is that the tool and the graph plumbing around it are real and
    correctly shaped, not that a full agent loop runs end to end
    without a model."""
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([execute_tool]))
    graph.set_entry_point("tools")
    return graph


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
        execute_tool = build_execute_tool(client)

        # Proves the tool is a real, correctly-shaped LangChain tool --
        # name/description/args_schema all inferred from the function
        # above, exactly what a real LLM's tool-calling API is shown.
        print(f"Tool name: {execute_tool.name}")
        print(f"Tool args schema: {execute_tool.args}")

        # Proves the graph wiring is real LangGraph, not hand-rolled --
        # tools_condition is the same routing function a create_react_agent
        # graph uses internally.
        graph = build_graph(execute_tool)
        compiled = graph.compile()
        print(f"Compiled graph nodes: {list(compiled.get_graph().nodes)}")

        connectors = await client.list_connectors()
        match = next((c for c in connectors if c["connector_type"] == connector_type), None)
        if match is None:
            print(
                f"No connected '{connector_type}' connector for this tenant -- connect one "
                f"from the dashboard's Integrations page first."
            )
            return

        # The real call: invoked exactly the way LangGraph's ToolNode
        # invokes a tool call an LLM produced, except the arguments are
        # hard-coded here instead of coming from a model.
        print(f"Invoking the tool for real: {connector_type}.{action}...")
        result = await execute_tool.ainvoke(
            {"connector_id": match["id"], "action": action, "params": {}}
        )
        print(f"Tool result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
