"""
MCP-shaped adapter for AgentGuard.

Wraps an AgentGuard instance so it can sit directly inside a real MCP
(Model Context Protocol) server, in front of whatever tools a connector
exposes -- so every real tool call an agent makes goes through the same
scope / approval / integrity checks the demos show.

Honest limitation: this sandbox has no outbound network access to
install the `mcp` PyPI package, so this module deliberately does NOT
import it. Instead it speaks the same *shape* a real MCP server needs
(a list of tool definitions, and a call_tool(name, arguments) -> result
entrypoint) using nothing but the standard library. That shape is fully
tested in tests/test_mcp_adapter.py. Wiring it to the actual `mcp`
package is a thin, mechanical layer -- see the bottom of this file for
exactly what that looks like.

Verification note (2026-08-14): the wiring sketch below was checked
against the real, official `modelcontextprotocol/python-sdk` source
(cloned from GitHub, since GitHub is reachable here even though PyPI
is not -- see src/mcp/server/lowlevel/server.py and docs/migration.md
in that clone). An earlier draft of this sketch used a decorator-based
API (`@server.list_tools()`, `@server.call_tool()`) that turned out to
be the *deprecated v1 shape* -- the current v2 SDK uses constructor-
based `on_list_tools` / `on_call_tool` handlers instead, and that's
what's shown below now. This was checked by reading the real source,
not by running it: `hatchling`, `httpx`, `anyio`, and `starlette`
aren't installable in this sandbox (no PyPI access), so the snippet
below has been read-verified against the SDK's own code and migration
docs, but not executed end to end. Treat it as "matches the real API
shape as of this SDK's current main branch," not as "run in CI here."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentguard.guardrail import AgentGuard


@dataclass
class ToolDef:
    name: str
    description: str = ""


class GuardedMCPServer:
    """
    Exposes the two calls a real MCP server implementation needs:
    list_tools() and call_tool(name, arguments). Every call_tool()
    invocation is routed through the wrapped AgentGuard first, so scope
    enforcement, approval gates, and connector integrity checks all
    apply to real tool calls exactly like they do in the demos.
    """

    def __init__(self, guard: AgentGuard, tool_descriptions: dict | None = None):
        self.guard = guard
        self.tool_descriptions = tool_descriptions or {}

    def list_tools(self) -> list[ToolDef]:
        return [
            ToolDef(name=action, description=self.tool_descriptions.get(action, ""))
            for action in sorted(self.guard.policy.task_scope)
        ]

    def call_tool(self, name: str, arguments: dict) -> Any:
        return self.guard.call(name, **arguments)


# ---------------------------------------------------------------------------
# Wiring this into the real `mcp` package (current v2 SDK API)
# ---------------------------------------------------------------------------
# Once you have network access to `pip install mcp`, a real server looks
# roughly like this. This matches the *current* SDK API (constructor-based
# on_list_tools / on_call_tool handlers) -- read-verified against the real
# SDK source and docs/migration.md as described above, but not executed
# here. Older tutorials/blog posts online may still show the deprecated
# v1 decorator style (`@server.list_tools()`); if you see that pattern,
# check docs/migration.md in the SDK repo before copying it.
#
#   from mcp.server import Server, ServerRequestContext
#   from mcp.types import (
#       CallToolRequestParams,
#       CallToolResult,
#       ListToolsResult,
#       PaginatedRequestParams,
#       TextContent,
#       Tool,
#   )
#   from agentguard import AgentGuard, Policy
#   from agentguard.mcp_adapter import GuardedMCPServer
#
#   guard = AgentGuard(connector=my_real_connector, policy=Policy(...))
#   guarded = GuardedMCPServer(guard, tool_descriptions={"read_message": "..."})
#
#   async def handle_list_tools(
#       ctx: ServerRequestContext, params: PaginatedRequestParams | None
#   ) -> ListToolsResult:
#       return ListToolsResult(
#           tools=[
#               Tool(name=t.name, description=t.description, input_schema={"type": "object"})
#               for t in guarded.list_tools()
#           ]
#       )
#
#   async def handle_call_tool(
#       ctx: ServerRequestContext, params: CallToolRequestParams
#   ) -> CallToolResult:
#       # AgentGuard's own exceptions (PermissionDenied, ApprovalDenied,
#       # ConnectorQuarantined) should be caught here and turned into an
#       # error CallToolResult -- letting them propagate as raw Python
#       # exceptions is not a real integration, just a sketch.
#       result = guarded.call_tool(params.name, params.arguments or {})
#       return CallToolResult(content=[TextContent(type="text", text=str(result))], is_error=False)
#
#   server = Server(
#       "agentguard-inbox",
#       on_list_tools=handle_list_tools,
#       on_call_tool=handle_call_tool,
#   )
#
# Everything upstream of guarded.call_tool() is real MCP boilerplate;
# everything downstream (AgentGuard itself) is exactly what the demos
# already exercise.
