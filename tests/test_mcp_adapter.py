"""
Tests for the MCP-shaped adapter. These verify the adapter's own logic
(list_tools reflects policy scope, call_tool routes through AgentGuard)
using nothing but the mock inbox -- they do NOT exercise the real `mcp`
package, since this environment has no network access to install it.
"""
from __future__ import annotations

import unittest

from agentguard import AgentGuard, GuardedMCPServer, PermissionDenied, Policy
from mock_services.inbox import MockInbox


class TestGuardedMCPServer(unittest.TestCase):
    def setUp(self):
        self.inbox = MockInbox()
        self.policy = Policy(
            connector_name="inbox",
            task_scope={"list_messages", "read_message"},
        )
        self.guard = AgentGuard(connector=self.inbox, policy=self.policy)
        self.server = GuardedMCPServer(
            self.guard, tool_descriptions={"list_messages": "List inbox messages"}
        )

    def test_list_tools_reflects_policy_scope(self):
        names = {t.name for t in self.server.list_tools()}
        self.assertEqual(names, {"list_messages", "read_message"})

    def test_list_tools_carries_descriptions(self):
        tools = {t.name: t.description for t in self.server.list_tools()}
        self.assertEqual(tools["list_messages"], "List inbox messages")

    def test_call_tool_in_scope_succeeds(self):
        result = self.server.call_tool("list_messages", {})
        self.assertEqual(len(result), 4)

    def test_call_tool_out_of_scope_is_blocked(self):
        with self.assertRaises(PermissionDenied):
            self.server.call_tool("delete_all", {})
        # and the inbox is provably untouched
        self.assertEqual(self.inbox.remaining_count(), 4)

    def test_call_tool_passes_arguments_through(self):
        result = self.server.call_tool("read_message", {"message_id": 1})
        self.assertEqual(result["id"], 1)


if __name__ == "__main__":
    unittest.main()
