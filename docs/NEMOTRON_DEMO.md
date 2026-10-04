# Guarded local or hosted model demo

This demo connects NVIDIA Nemotron 3 Ultra's OpenAI-compatible tool calling to
the existing AgentGuard Python SDK. A model can propose a connector action;
only AgentGuard can authorize and execute it. The same runner supports NVIDIA
Nemotron and local Ollama models such as Qwen3.

The demo exposes the existing `github.list_repos`, `github.close_issue`,
`slack.list_channels`, and `slack.send_message` contracts. The platform remains
the enforcement point for agent identity, grants, resource scopes, policy,
approval, input validation, idempotency, execution, and audit.

## Safety properties

- NVIDIA receives no GitHub or Slack credential.
- The runner advertises tools only for connectors visible to the Agent key.
- Unknown tool names never reach AgentGuard.
- AgentGuard validates all parameters again; model-produced JSON is untrusted.
- Every tool call receives a stable UUID idempotency key for its run and call ID.
- Tool and turn budgets stop unbounded autonomous loops.
- Denial and failure details are reduced to safe status codes before going back
  to the model.
- Connector results sent back to the model are capped at 8,000 characters.

## Run

Install the SDK and start the AgentGuard platform as described in the main
local-product documentation. Create an Agent, grant it a test GitHub or Slack
connector, and copy the one-time Agent key.

Set credentials only in the current process environment. Do not commit them or
place them in a repository file:

```powershell
$env:NVIDIA_API_KEY = "PASTE_YOUR_NVIDIA_API_KEY_HERE"
$env:AGENTGUARD_AGENT_KEY = "PASTE_YOUR_AGENTGUARD_AGENT_KEY_HERE"
python sdk/python/examples/nemotron_agent.py "List my GitHub repositories"
```

Tırnak içindeki iki açıklama örnek metindir; gerçek değerleri yalnız kendi
PowerShell oturumunuzda bunların yerine yazın. Değerleri kaynak dosyaya eklemeyin.

Approval-path example:

```powershell
python sdk/python/examples/nemotron_agent.py "Close issue 42 in acme/demo"
```

Keep the terminal running and resolve the request from AgentGuard's Approvals
page. Use a dedicated test repository or workspace for live mutation tests.

## Recommended proof sequence

1. `github.list_repos` in an allowed scope completes automatically.
2. `github.close_issue` creates a human approval request.
3. The same action against an ungranted repository is denied.
4. Revoke the connector grant while approval is pending; approval must no
   longer result in provider execution.
5. Re-submit the same operation with the same idempotency key and confirm the
   provider action is not executed twice.

The NVIDIA endpoint is a hosted prototype/trial service. Availability, quotas,
and terms can change; it should not be presented as a production SLA or a
permanently free dependency.

## Local Qwen3 through Ollama

For the local route, install Ollama and pull the tested model once:

```powershell
ollama pull qwen3:8b
```

Only the AgentGuard key is needed by the runner. Qwen3 runs on this computer;
the connector credentials remain stored and used by AgentGuard, never by
Ollama or the model process.

```powershell
$env:AGENTGUARD_AGENT_KEY = "PASTE_YOUR_AGENTGUARD_AGENT_KEY_HERE"
python sdk/python/examples/nemotron_agent.py --provider ollama "GitHub repolarımı listele. Hiçbir şeyi değiştirme."
```

The runner calls Ollama only at `127.0.0.1:11434` and disables model thinking
to keep the first local tool-call tests responsive. Use `--model` to select an
already-pulled local model. For a non-default platform address, set
`AGENTGUARD_API_BASE_URL` or pass `--agentguard-url`.
