# agentguard-sdk

A thin async client for the [AgentGuard](../..) platform API. Lets your
own agent framework code (a LangGraph tool, a CrewAI tool, a raw agent
loop -- anything that can `await` a Python function) call a real,
guarded connector action, with AgentGuard's policy engine and
human-approval gate applying exactly the same as they do to the
dashboard or the MCP server.

Standalone package: importing `agentguard_sdk` does not pull in any of
the parent monorepo's other code (`agentguard/`, `apps/`,
`infrastructure/`) -- only `httpx`.

## Install

```bash
pip install -e .          # from this directory, until this is published to PyPI
```

## Get an Agent key

From the AgentGuard dashboard's **Agents** page (or `POST
/tenant/agents`, admin-only), create an Agent and copy its key --
shown exactly once, starts with `agk_`.

## Use it

```python
import asyncio
import os
from agentguard_sdk import AgentGuardClient, AgentGuardDenied

async def main():
    async with AgentGuardClient(
        agent_key=os.environ["AGENTGUARD_AGENT_KEY"],
        base_url="http://localhost:5000/api",
    ) as client:
        connectors = await client.list_connectors()
        github = next(c for c in connectors if c["connector_type"] == "github")

        try:
            outcome = await client.run(github["id"], "list_repos")
        except AgentGuardDenied:
            print("a human said no")
            return

        print(outcome["result"])

asyncio.run(main())
```

Anahtarı kaynak koda yazmayın. Yalnız çalıştırdığınız PowerShell oturumunda ayarlayın:

```powershell
$env:AGENTGUARD_AGENT_KEY = "BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN"
```

`run()` blocks (polling) until a human resolves the request if the
action needs approval, then returns the real result -- see
`agentguard_sdk/client.py`'s own docstring for exactly what it mirrors
and why. A full, runnable example: [`examples/basic_agent_loop.py`](examples/basic_agent_loop.py).

## LangGraph example

```bash
pip install -e ".[langgraph]"   # only needed for this example
AGENTGUARD_AGENT_KEY="BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN" python examples/langgraph_agent.py
```

A real, runnable [`examples/langgraph_agent.py`](examples/langgraph_agent.py)
wraps `run()` as a genuine LangChain `@tool` and builds a real LangGraph
graph around it -- see the example's own docstring for exactly what it
proves (a real tool + real graph wiring) and what it deliberately
doesn't (calling an actual LLM, which needs a model provider API key
this package won't choose on your behalf).

## CrewAI example

```bash
python -m venv .venv-crewai        # a DEDICATED venv -- see why below
.venv-crewai/Scripts/pip install -e .
.venv-crewai/Scripts/pip install crewai
AGENTGUARD_AGENT_KEY="BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN" .venv-crewai/Scripts/python examples/crewai_agent.py
```

A real, runnable [`examples/crewai_agent.py`](examples/crewai_agent.py)
wraps `run()` as a genuine `crewai.tools.BaseTool` -- see the example's
own docstring for exactly what it proves and what it deliberately
doesn't (calling an actual LLM or running a full `Crew`).

**Run this one in its own virtual environment, not the one you used for
`langgraph_agent.py` above.** CrewAI's dependency tree (chromadb,
onnxruntime, opentelemetry, its own pinned `mcp`, ...) downgraded this
monorepo's own `mcp` package far enough to break `apps/mcp_server`'s
import, when tried in the shared repo venv -- found by actually running
the full test suite after installing `crewai`, not by inspection. Not a
bug in CrewAI; it's simply not meant to share a Python environment with
an unrelated FastAPI platform and MCP server.

## What this is not (yet)

- No sync client. Async only, matching the rest of this platform.
- Not published to PyPI yet -- `pip install -e .` from this directory
  is the only install path today.
