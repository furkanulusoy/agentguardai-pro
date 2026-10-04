/**
 * A real LangChain.js integration example -- what
 * examples/basic-agent-loop.ts deliberately doesn't claim (see that
 * file's own docstring, and the package README's "What this is not
 * (yet)" section this file exists specifically to close). Mirrors
 * sdk/python/examples/langgraph_agent.py's shape and honesty for the
 * TypeScript SDK.
 *
 * What this proves, for real, by actually running: AgentGuardClient's
 * run() can be wrapped as a genuine LangChain.js StructuredTool
 * (`executeViaAgentGuard` below) -- a real zod input schema, callable
 * via `tool.invoke(...)` the same way a LangGraph.js tool-calling node
 * calls it, and internally making a real HTTP call against the real
 * platform API (not a stub).
 *
 * What this deliberately does NOT do, named rather than glossed over:
 * call a real LLM, or build a full LangGraph.js graph. Getting an LLM
 * to actually pick this tool and choose its arguments requires a real
 * model provider (OpenAI/Anthropic/...) and a real API key -- a
 * project-external decision this example doesn't make on a reader's
 * behalf. What's AgentGuard-specific is everything below
 * `executeViaAgentGuard`; which LLM decides to call it, and how it's
 * wired into a LangGraph.js graph, is a separate, already-solved
 * problem for every LangChain.js example on the internet, not
 * something this project needs to re-prove.
 *
 * Run it (from this repo, with the platform's own API running --
 * `docker compose up -d && uvicorn apps.api.main:app` -- and a real
 * Agent key from the dashboard's Agents page):
 *
 *   npm install
 *   AGENTGUARD_AGENT_KEY=agk_... npx tsx examples/langchain-agent.ts
 *
 * (`tsx`, not `node --experimental-strip-types` -- see
 * examples/basic-agent-loop.ts's own docstring for why.)
 */
import { tool } from "@langchain/core/tools";
import { z } from "zod";

import { AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout } from "../src/index.js";

const executeSchema = z.object({
  connectorId: z.string().describe("A connector id from listConnectors()"),
  action: z.string().describe('An action the connector supports, e.g. "list_repos"'),
  params: z.record(z.string(), z.unknown()).optional().describe("The action's keyword arguments"),
});

function buildExecuteTool(client: AgentGuardClient) {
  // A factory, not a module-level tool -- the client (and the agent
  // key it holds) is only known at runtime, not import time.
  return tool(
    async ({ connectorId, action, params }) => {
      try {
        const outcome = await client.run(connectorId, action, params ?? {});
        return JSON.stringify(outcome.result);
      } catch (err) {
        if (err instanceof AgentGuardDenied) {
          return "A human denied this action from the AgentGuard Approvals page.";
        }
        if (err instanceof AgentGuardTimeout) {
          return "Nobody resolved the approval request in time.";
        }
        if (err instanceof AgentGuardError) {
          return `AgentGuard rejected this action: ${err.message}`;
        }
        throw err;
      }
    },
    {
      name: "execute_via_agentguard",
      description:
        "Execute a guarded action against a connected external service through AgentGuard. " +
        "Blocks until AgentGuard resolves the call -- either it runs immediately, or a human " +
        "approves/denies it from the AgentGuard dashboard.",
      schema: executeSchema,
    },
  );
}

async function main(): Promise<void> {
  const agentKey = process.env.AGENTGUARD_AGENT_KEY;
  if (!agentKey) {
    console.error(
      "Set AGENTGUARD_AGENT_KEY to a real Agent key from the dashboard's Agents page " +
        "(or POST /tenant/agents) first.",
    );
    process.exit(1);
  }

  const baseUrl = process.env.AGENTGUARD_API_BASE_URL ?? "http://localhost:5000/api";
  const connectorType = process.env.AGENTGUARD_CONNECTOR ?? "github";
  const action = process.env.AGENTGUARD_ACTION ?? "list_repos";

  const client = new AgentGuardClient(agentKey, { baseUrl });
  const executeTool = buildExecuteTool(client);

  // Proves the tool is a real, correctly-shaped LangChain.js tool --
  // name/description/schema all inferred from the definition above,
  // exactly what a real LLM's tool-calling API is shown.
  console.log(`Tool name: ${executeTool.name}`);
  console.log(`Tool description: ${executeTool.description}`);

  const connectors = await client.listConnectors();
  const match = connectors.find((c) => c.connector_type === connectorType);
  if (!match) {
    console.log(
      `No connected '${connectorType}' connector for this tenant -- connect one from the ` +
        `dashboard's Integrations page first.`,
    );
    return;
  }

  // The real call: invoked exactly the way a LangGraph.js ToolNode
  // invokes a tool call an LLM produced, except the arguments are
  // hard-coded here instead of coming from a model.
  console.log(`Invoking the tool for real: ${connectorType}.${action}...`);
  const result = await executeTool.invoke({ connectorId: match.id, action, params: {} });
  console.log(`Tool result: ${result}`);
}

main();
