from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
import sys
import unittest
import uuid
from typing import Any
from unittest.mock import patch

import httpx

MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "sdk" / "python" / "examples" / "nemotron_agent.py"
)
SPEC = importlib.util.spec_from_file_location("nemotron_agent", MODULE_PATH)
assert SPEC and SPEC.loader
nemotron_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nemotron_agent
SPEC.loader.exec_module(nemotron_agent)
from agentguard_sdk import AgentGuardDenied, AgentGuardError  # noqa: E402


class FakeModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def complete(self, messages, tools):
        self.requests.append((list(messages), list(tools)))
        return self.responses.pop(0)


class FakeGuard:
    def __init__(self, connectors, *, denied: bool = False, auth_failed: bool = False) -> None:
        self.connectors = connectors
        self.denied = denied
        self.auth_failed = auth_failed
        self.calls: list[dict[str, Any]] = []

    async def list_connectors(self):
        return self.connectors

    async def run(self, connector_id, action, params, *, idempotency_key):
        self.calls.append(
            {
                "connector_id": connector_id,
                "action": action,
                "params": params,
                "idempotency_key": idempotency_key,
            }
        )
        if self.denied:
            raise AgentGuardDenied("secret provider detail")
        if self.auth_failed:
            raise AgentGuardError("Execution failed (PROVIDER_AUTHENTICATION_FAILED)")
        return {"status": "completed", "result": {"ok": True}}


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "call-1") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


class TestGuardedNemotronAgent(unittest.IsolatedAsyncioTestCase):
    async def test_only_granted_connector_tools_are_advertised(self):
        model = FakeModel(
            [
                tool_call("github_list_repos", {}),
                {"role": "assistant", "content": "Done"},
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        answer = await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(answer, "Done")
        names = {item["function"]["name"] for item in model.requests[0][1]}
        self.assertEqual(names, {"github_list_repos", "github_close_issue"})

    async def test_revoked_connector_is_ignored_in_favor_of_active_grant(self):
        model = FakeModel(
            [
                tool_call("github_list_repos", {}),
                {"role": "assistant", "content": "Done"},
            ]
        )
        guard = FakeGuard(
            [
                {"id": "active", "connector_type": "github", "is_revoked": False},
                {"id": "revoked", "connector_type": "github", "is_revoked": True},
            ]
        )

        await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(guard.calls[0]["connector_id"], "active")

    async def test_multiple_active_connectors_of_same_type_fail_closed(self):
        model = FakeModel([])
        guard = FakeGuard(
            [
                {"id": "github-1", "connector_type": "github", "is_revoked": False},
                {"id": "github-2", "connector_type": "github", "is_revoked": False},
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "Multiple active github connectors"):
            await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(guard.calls, [])

    async def test_retries_once_when_model_answers_without_grounding_tool(self):
        model = FakeModel(
            [
                {"role": "assistant", "content": "Permission is missing."},
                tool_call("github_list_repos", {}),
                {"role": "assistant", "content": "Repository list completed."},
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        answer = await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(answer, "Repository list completed.")
        self.assertEqual(len(guard.calls), 1)
        retry_instruction = model.requests[1][0][-1]
        self.assertEqual(retry_instruction["role"], "system")
        self.assertIn("AgentGuard tool result", retry_instruction["content"])

    async def test_fails_closed_when_model_twice_avoids_required_tool(self):
        model = FakeModel(
            [
                {"role": "assistant", "content": "Permission is missing."},
                {"role": "assistant", "content": "Still unavailable."},
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        with self.assertRaisesRegex(RuntimeError, "required AgentGuard tool"):
            await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(guard.calls, [])

    async def test_tool_call_is_routed_through_agentguard_with_uuid_idempotency_key(self):
        model = FakeModel(
            [
                tool_call("github_close_issue", {"repo": "acme/demo", "issue_number": 42}),
                {"role": "assistant", "content": "Issue operation completed."},
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        answer = await nemotron_agent.GuardedAgent(model, guard).run("Close issue 42")

        self.assertEqual(answer, "Issue operation completed.")
        self.assertEqual(len(guard.calls), 1)
        self.assertEqual(guard.calls[0]["connector_id"], "github-1")
        self.assertEqual(guard.calls[0]["action"], "close_issue")
        self.assertEqual(guard.calls[0]["params"], {"repo": "acme/demo", "issue_number": 42})
        uuid.UUID(guard.calls[0]["idempotency_key"])

    async def test_unknown_tool_never_reaches_agentguard(self):
        model = FakeModel(
            [
                tool_call("github_delete_repository", {"repo": "acme/demo"}),
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        answer = await nemotron_agent.GuardedAgent(model, guard).run("Delete the repository")

        self.assertEqual(guard.calls, [])
        self.assertEqual(
            answer,
            "The model requested a tool that this AgentGuard runner does not expose.",
        )
        self.assertEqual(len(model.requests), 1)

    async def test_agentguard_denial_is_sanitized_before_returning_to_model(self):
        model = FakeModel(
            [
                tool_call("slack_send_message", {"channel": "C123", "text": "hello"}),
            ]
        )
        guard = FakeGuard([{"id": "slack-1", "connector_type": "slack"}], denied=True)

        answer = await nemotron_agent.GuardedAgent(model, guard).run("Send hello")

        self.assertEqual(answer, "AgentGuard denied the action by policy or human decision.")
        self.assertNotIn("secret provider detail", answer)
        self.assertEqual(len(model.requests), 1)

    async def test_provider_authentication_failure_requires_reconnection(self):
        model = FakeModel(
            [
                tool_call("github_list_repos", {}),
            ]
        )
        guard = FakeGuard(
            [{"id": "github-1", "connector_type": "github"}], auth_failed=True
        )

        answer = await nemotron_agent.GuardedAgent(model, guard).run("List repositories")

        self.assertEqual(
            answer,
            "AgentGuard rejected the connector credential. Reconnect the provider account.",
        )
        self.assertEqual(len(model.requests), 1)

    async def test_tool_call_budget_stops_repeated_model_actions(self):
        model = FakeModel(
            [
                tool_call("github_list_repos", {}, "one"),
                tool_call("github_list_repos", {}, "two"),
            ]
        )
        guard = FakeGuard([{"id": "github-1", "connector_type": "github"}])

        with self.assertRaisesRegex(RuntimeError, "Tool-call budget exceeded"):
            await nemotron_agent.GuardedAgent(model, guard, max_tool_calls=1).run("Loop")

        self.assertEqual(len(guard.calls), 1)


class TestOllamaAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_starts_loopback_ollama_when_initial_health_check_cannot_connect(self):
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, json={"models": []})

        async with nemotron_agent.OllamaClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch.object(client, "_start_local_server", return_value=True) as start:
                await client.ensure_available()

        start.assert_called_once_with()
        self.assertEqual(requests, 2)

    async def test_never_starts_a_local_process_for_remote_ollama_endpoint(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with nemotron_agent.OllamaClient(
            endpoint="https://ollama.example/api/chat",
            transport=httpx.MockTransport(handler),
        ) as client:
            with patch.object(client, "_start_local_server") as start:
                with self.assertRaisesRegex(RuntimeError, "endpoint is unreachable"):
                    await client.ensure_available()

        start.assert_not_called()

    def test_uses_sdk_from_this_checkout(self):
        expected_sdk_root = MODULE_PATH.parents[1].resolve()
        loaded_client = pathlib.Path(inspect.getfile(nemotron_agent.AgentGuardClient)).resolve()
        self.assertIn(expected_sdk_root, loaded_client.parents)

    def test_local_platform_url_matches_docker_dashboard_proxy(self):
        self.assertEqual(nemotron_agent.DEFAULT_AGENTGUARD_URL, "http://localhost:5000/api")

    async def test_normalizes_ollama_tool_call_and_preserves_local_only_request(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "http://127.0.0.1:11434/api/chat")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "qwen3:8b")
            self.assertFalse(payload["think"])
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["options"]["num_ctx"], 8192)
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "github_list_repos",
                                    "arguments": {"max_results": 5},
                                }
                            }
                        ],
                    }
                },
            )

        async with nemotron_agent.OllamaClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            message = await client.complete(
                [{"role": "user", "content": "List repositories"}],
                [{"type": "function", "function": {"name": "github_list_repos"}}],
            )

        self.assertEqual(message["tool_calls"][0]["id"], "ollama-0")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "github_list_repos")
        self.assertEqual(
            json.loads(message["tool_calls"][0]["function"]["arguments"]), {"max_results": 5}
        )

    def test_converts_generic_tool_result_to_ollama_tool_message(self):
        messages = nemotron_agent.OllamaClient._messages_for_ollama(
            [
                {
                    "role": "assistant",
                    "tool_calls": [tool_call("github_list_repos", {})["tool_calls"][0]],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "github_list_repos",
                    "content": "{}",
                },
            ]
        )

        self.assertEqual(messages[0]["tool_calls"][0]["function"]["arguments"], {})
        self.assertEqual(
            messages[1],
            {"role": "tool", "tool_name": "github_list_repos", "content": "{}"},
        )


if __name__ == "__main__":
    unittest.main()
