"""Guarded NVIDIA or Ollama tool-calling demo routed through AgentGuard.

Nemotron proposes actions. AgentGuard remains the only component allowed to
authorize and execute connector calls. Provider and connector credentials are
never placed in the same request or object.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

# This example must use the SDK shipped in the same AgentGuard checkout.
# A globally installed editable SDK could otherwise point at another project.
SDK_SOURCE_DIR = str(Path(__file__).resolve().parents[1])
if SDK_SOURCE_DIR in sys.path:
    sys.path.remove(SDK_SOURCE_DIR)
sys.path.insert(0, SDK_SOURCE_DIR)

from agentguard_sdk import (  # noqa: E402
    AgentGuardClient,
    AgentGuardDenied,
    AgentGuardError,
    AgentGuardTimeout,
)

NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3:8b"
DEFAULT_AGENTGUARD_URL = "http://localhost:5000/api"
DEFAULT_SYSTEM_PROMPT = (
    "You are an enterprise operations agent. Use only the tools supplied for this request. "
    "AgentGuard independently authorizes every action. Never claim an action succeeded until "
    "the corresponding tool result says it completed. If AgentGuard denies, times out, or "
    "rejects an action, explain that outcome and do not attempt a workaround."
)
MAX_PROVIDER_RESULT_CHARS = 8_000


@dataclass(frozen=True)
class ToolBinding:
    connector_type: str
    action: str
    description: str
    parameters: dict[str, Any]


TOOL_BINDINGS: dict[str, ToolBinding] = {
    "github_list_repos": ToolBinding(
        "github",
        "list_repos",
        "List repositories visible to the connected GitHub credential.",
        {
            "type": "object",
            "properties": {"max_results": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
    ),
    "github_close_issue": ToolBinding(
        "github",
        "close_issue",
        "Close one GitHub issue. AgentGuard normally requires human approval.",
        {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$",
                },
                "issue_number": {"type": "integer", "minimum": 1, "maximum": 2147483647},
            },
            "required": ["repo", "issue_number"],
            "additionalProperties": False,
        },
    ),
    "slack_list_channels": ToolBinding(
        "slack",
        "list_channels",
        "List channels visible to the connected Slack credential.",
        {
            "type": "object",
            "properties": {"max_results": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
    ),
    "slack_send_message": ToolBinding(
        "slack",
        "send_message",
        "Send a message to a Slack channel after AgentGuard authorization.",
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "pattern": r"^[CG][A-Z0-9]{2,30}$"},
                "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["channel", "text"],
            "additionalProperties": False,
        },
    ),
}


class ModelClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class NemotronClient:
    """Small OpenAI-compatible client for NVIDIA's hosted prototype endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = NVIDIA_ENDPOINT,
        model: str = NVIDIA_MODEL,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("NVIDIA_API_KEY must not be empty")
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        self._endpoint = endpoint

    async def __aenter__(self) -> NemotronClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._endpoint,
            json={
                "model": self._model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.1,
                "max_tokens": 2048,
                "chat_template_kwargs": {
                    "enable_thinking": True,
                    "force_nonempty_content": True,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("NVIDIA returned an invalid chat completion") from exc
        if not isinstance(message, dict):
            raise RuntimeError("NVIDIA returned an invalid assistant message")
        return message


class OllamaClient:
    """Adapter from Ollama's local chat API to the shared guarded agent loop."""

    def __init__(
        self,
        *,
        endpoint: str = OLLAMA_ENDPOINT,
        model: str = OLLAMA_MODEL,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._endpoint = endpoint
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama endpoint must be an HTTP(S) URL")
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    async def __aenter__(self) -> OllamaClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _start_local_server() -> bool:
        candidates = [shutil.which("ollama")]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(str(Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"))
        executable = next((item for item in candidates if item and Path(item).is_file()), None)
        if executable is None:
            return False

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        try:
            subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                close_fds=True,
            )
        except OSError:
            return False
        return True

    async def ensure_available(self) -> None:
        tags_url = f"{self._origin}/api/tags"
        try:
            response = await self._client.get(tags_url)
            response.raise_for_status()
            return
        except httpx.ConnectError as exc:
            if not self._is_loopback:
                raise RuntimeError("Configured Ollama endpoint is unreachable") from exc
            if not self._start_local_server():
                raise RuntimeError(
                    "Local Ollama is not running and could not be started automatically"
                ) from exc

        for _attempt in range(20):
            await asyncio.sleep(0.25)
            try:
                response = await self._client.get(tags_url)
                response.raise_for_status()
                return
            except httpx.ConnectError:
                continue
        raise RuntimeError("Local Ollama did not become ready within five seconds")

    @staticmethod
    def _messages_for_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            current = dict(message)
            if current.get("role") == "assistant" and isinstance(current.get("tool_calls"), list):
                calls: list[dict[str, Any]] = []
                for index, call in enumerate(current["tool_calls"]):
                    function = dict(call.get("function") or {})
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    function["arguments"] = arguments if isinstance(arguments, dict) else {}
                    calls.append({"type": "function", "function": function, "index": index})
                current["tool_calls"] = calls
            elif current.get("role") == "tool":
                current.pop("tool_call_id", None)
                current["tool_name"] = str(current.pop("name", ""))
            converted.append(current)
        return converted

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._endpoint,
            json={
                "model": self._model,
                "messages": self._messages_for_ollama(messages),
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
        )
        response.raise_for_status()
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("Ollama returned an invalid assistant message")

        normalized = dict(message)
        raw_calls = normalized.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise RuntimeError("Ollama returned invalid tool calls")
        calls: list[dict[str, Any]] = []
        for index, call in enumerate(raw_calls):
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise RuntimeError("Ollama returned an invalid tool call")
            arguments = function.get("arguments", {})
            if not isinstance(arguments, dict):
                raise RuntimeError("Ollama returned invalid tool arguments")
            calls.append(
                {
                    "id": f"ollama-{index}",
                    "type": "function",
                    "function": {"name": function["name"], "arguments": json.dumps(arguments)},
                }
            )
        if calls:
            normalized["tool_calls"] = calls
        return normalized


def tools_for_connectors(connector_types: set[str]) -> list[dict[str, Any]]:
    """Advertise only tools backed by connectors visible to this agent."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": binding.description,
                "parameters": binding.parameters,
            },
        }
        for name, binding in TOOL_BINDINGS.items()
        if binding.connector_type in connector_types
    ]


def _safe_tool_result(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= MAX_PROVIDER_RESULT_CHARS:
        return rendered
    return json.dumps(
        {
            "status": "completed",
            "result_truncated": True,
            "preview": rendered[:MAX_PROVIDER_RESULT_CHARS],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _safe_non_completed_message(result: dict[str, Any]) -> str:
    status = str(result.get("status", "rejected"))
    reason = str(result.get("reason", "agentguard_rejected_action"))
    messages = {
        "connector_reauthentication_required": (
            "AgentGuard rejected the connector credential. Reconnect the provider account."
        ),
        "human_or_policy_denial": "AgentGuard denied the action by policy or human decision.",
        "approval_or_operation_timeout": (
            "AgentGuard could not confirm the operation outcome before the timeout. "
            "Check the operation ledger before retrying."
        ),
        "unknown_tool": "The model requested a tool that this AgentGuard runner does not expose.",
        "connector_not_granted": "The agent has no grant for the requested connector.",
        "invalid_arguments_json": "The model produced invalid tool arguments.",
        "arguments_must_be_object": "The model produced invalid tool arguments.",
        "agentguard_rejected_action": (
            "AgentGuard rejected the action before execution. Check the agent grant and "
            "connector status."
        ),
    }
    return messages.get(reason, f"AgentGuard returned status={status}, reason={reason}.")


class GuardedAgent:
    def __init__(
        self,
        model: ModelClient,
        guard: AgentGuardClient,
        *,
        max_turns: int = 6,
        max_tool_calls: int = 8,
    ) -> None:
        if max_turns < 1 or max_tool_calls < 1:
            raise ValueError("Agent budgets must be positive")
        self._model = model
        self._guard = guard
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls

    async def run(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")

        connectors = await self._guard.list_connectors()
        connector_by_type: dict[str, str] = {}
        for item in connectors:
            connector_type = item.get("connector_type")
            connector_id = item.get("id")
            if (
                connector_type not in {"github", "slack"}
                or not connector_id
                or item.get("is_revoked") is True
            ):
                continue
            if connector_type in connector_by_type:
                raise RuntimeError(
                    f"Multiple active {connector_type} connectors are granted; "
                    "select one explicitly before running this example"
                )
            connector_by_type[connector_type] = str(connector_id)
        tools = tools_for_connectors(set(connector_by_type))
        if not tools:
            raise RuntimeError("This agent has no granted GitHub or Slack connector")

        run_id = uuid.uuid4()
        tool_call_count = 0
        grounding_retry_sent = False
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        for _turn in range(self._max_turns):
            assistant = await self._model.complete(messages, tools)
            messages.append(assistant)
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                content = assistant.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Nemotron returned neither text nor tool calls")
                if tool_call_count == 0:
                    if grounding_retry_sent:
                        raise RuntimeError("Model did not call a required AgentGuard tool")
                    grounding_retry_sent = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "No tool was called. This operations runner accepts only answers "
                                "grounded in an AgentGuard tool result. Call exactly one relevant "
                                "supplied tool now. Do not infer connector permissions or answer "
                                "from memory."
                            ),
                        }
                    )
                    continue
                return content

            for call in tool_calls:
                tool_call_count += 1
                if tool_call_count > self._max_tool_calls:
                    raise RuntimeError("Tool-call budget exceeded")
                result = await self._execute_tool(call, connector_by_type, run_id)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "missing")),
                        "name": str((call.get("function") or {}).get("name", "")),
                        "content": _safe_tool_result(result),
                    }
                )
                if result.get("status") != "completed":
                    return _safe_non_completed_message(result)

        raise RuntimeError("Agent turn budget exceeded")

    async def _execute_tool(
        self,
        call: dict[str, Any],
        connector_by_type: dict[str, str],
        run_id: uuid.UUID,
    ) -> dict[str, Any]:
        function = call.get("function") or {}
        name = function.get("name")
        call_id = str(call.get("id") or "missing")
        if not isinstance(name, str):
            return {"status": "rejected", "reason": "unknown_tool"}
        binding = TOOL_BINDINGS.get(name)
        if binding is None:
            return {"status": "rejected", "reason": "unknown_tool"}

        connector_id = connector_by_type.get(binding.connector_type)
        if connector_id is None:
            return {"status": "rejected", "reason": "connector_not_granted"}

        try:
            params = json.loads(function.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {"status": "rejected", "reason": "invalid_arguments_json"}
        if not isinstance(params, dict):
            return {"status": "rejected", "reason": "arguments_must_be_object"}

        operation_key = str(uuid.uuid5(run_id, f"{call_id}:{name}"))
        try:
            outcome = await self._guard.run(
                connector_id,
                binding.action,
                params,
                idempotency_key=operation_key,
            )
            return {"status": "completed", "result": outcome.get("result")}
        except AgentGuardDenied:
            return {"status": "denied", "reason": "human_or_policy_denial"}
        except AgentGuardTimeout:
            return {"status": "unknown", "reason": "approval_or_operation_timeout"}
        except AgentGuardError as exc:
            if "PROVIDER_AUTHENTICATION_FAILED" in str(exc):
                return {"status": "rejected", "reason": "connector_reauthentication_required"}
            return {"status": "rejected", "reason": "agentguard_rejected_action"}


async def _main(args: argparse.Namespace) -> None:
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    agent_key = os.environ.get("AGENTGUARD_AGENT_KEY", "")
    if not agent_key:
        print(
            "Set AGENTGUARD_AGENT_KEY in the process environment.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.provider == "nvidia" and not nvidia_key:
        print("Set NVIDIA_API_KEY in the process environment.", file=sys.stderr)
        raise SystemExit(2)

    if args.provider == "nvidia":
        async with (
            NemotronClient(nvidia_key, model=args.model or NVIDIA_MODEL) as model,
            AgentGuardClient(agent_key, args.agentguard_url) as guard,
        ):
            answer = await GuardedAgent(model, guard).run(args.prompt)
            print(answer)
        return

    async with (
        OllamaClient(endpoint=args.ollama_url, model=args.model or OLLAMA_MODEL) as model,
        AgentGuardClient(agent_key, args.agentguard_url) as guard,
    ):
        await model.ensure_available()
        answer = await GuardedAgent(model, guard).run(args.prompt)
        print(answer)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run NVIDIA or local Ollama tool calls through AgentGuard governance."
    )
    parser.add_argument("prompt", help="The task to give the guarded agent")
    parser.add_argument("--provider", choices=("nvidia", "ollama"), default="nvidia")
    parser.add_argument("--model", help="Override the model for the selected provider")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_API_URL", OLLAMA_ENDPOINT))
    parser.add_argument(
        "--agentguard-url",
        default=os.environ.get("AGENTGUARD_API_BASE_URL", DEFAULT_AGENTGUARD_URL),
    )
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
