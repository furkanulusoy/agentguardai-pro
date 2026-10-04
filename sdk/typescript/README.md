# @agentguard/sdk

A thin client for the [AgentGuard](../..) platform API. Lets your own
agent framework code (a LangChain/LangGraph.js tool, a raw agent loop
-- anything that can `await` a function) call a real, guarded connector
action, with AgentGuard's policy engine and human-approval gate
applying exactly the same as they do to the dashboard, the MCP server,
or the [Python SDK](../python).

Zero runtime dependencies: native `fetch` (Node 18+, and every
browser), not axios/node-fetch -- the same "this package has nothing
else in it" discipline `sdk/python` applies with `httpx` as its one
dependency.

## Install

```bash
npm install   # from this directory, until this is published to npm
npm run build
```

## Get an Agent key

From the AgentGuard dashboard's **Agents** page (or `POST
/tenant/agents`, admin-only), create an Agent and copy its key --
shown exactly once, starts with `agk_`.

## Use it

```typescript
import { AgentGuardClient, AgentGuardDenied } from "@agentguard/sdk";

const agentKey = process.env.AGENTGUARD_AGENT_KEY;
if (!agentKey) throw new Error("AGENTGUARD_AGENT_KEY is required");

const client = new AgentGuardClient(agentKey, {
  baseUrl: "http://localhost:5000/api",
});

const connectors = await client.listConnectors();
const github = connectors.find((c) => c.connector_type === "github");
if (!github) throw new Error("no GitHub connector for this tenant");

try {
  const outcome = await client.run(github.id, "list_repos");
  console.log(outcome.result);
} catch (err) {
  if (err instanceof AgentGuardDenied) {
    console.log("a human said no");
  } else {
    throw err;
  }
}
```

Anahtarı kaynak koda yazmayın. Yalnız çalıştırdığınız PowerShell oturumunda ayarlayın:

```powershell
$env:AGENTGUARD_AGENT_KEY = "BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN"
```

`run()` awaits (polling) until a human resolves the request if the
action needs approval, then returns the real result -- see
`src/client.ts`'s own docstring for exactly what it mirrors and why. A
full, runnable example: [`examples/basic-agent-loop.ts`](examples/basic-agent-loop.ts).

## LangChain.js example

```bash
npm install    # @langchain/core, zod, and tsx are already devDependencies
AGENTGUARD_AGENT_KEY="BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN" npx tsx examples/langchain-agent.ts
```

A real, runnable [`examples/langchain-agent.ts`](examples/langchain-agent.ts)
wraps `run()` as a genuine LangChain.js `StructuredTool` (real zod
schema, real `tool.invoke(...)`) -- see the example's own docstring for
exactly what it proves and what it deliberately doesn't (calling an
actual LLM, or building a full LangGraph.js graph -- both need choices
this package won't make on your behalf).

## LangGraph.js example

```bash
npm install    # @langchain/langgraph is already a devDependency
AGENTGUARD_AGENT_KEY="BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN" npx tsx examples/langgraph-agent.ts
```

A real, runnable [`examples/langgraph-agent.ts`](examples/langgraph-agent.ts)
drops the same tool `examples/langchain-agent.ts` builds into a real
`StateGraph`, wired with LangGraph.js's own prebuilt `ToolNode`, and
invokes it by routing a real `AIMessage` tool-call through the compiled
graph -- see the example's own docstring for exactly what it proves and
what it deliberately doesn't (calling an actual LLM, which needs a
model provider API key this package won't choose on your behalf).

## What this is not (yet)

- Not published to npm yet -- `npm install` from this directory is the
  only install path today.
