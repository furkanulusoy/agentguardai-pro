/**
 * A real LangGraph.js integration example -- what
 * examples/langchain-agent.ts deliberately doesn't build (see that
 * file's own docstring, and the package README's "What this is not
 * (yet)" section this file exists specifically to close). Mirrors
 * sdk/python/examples/langgraph_agent.py's shape and honesty for the
 * TypeScript SDK.
 *
 * What this proves, for real, by actually running: the same
 * StructuredTool built in examples/langchain-agent.ts can be dropped
 * into a real LangGraph.js `StateGraph`, wired with LangGraph.js's own
 * prebuilt `ToolNode` (the same node a real `createReactAgent` graph
 * uses internally), compiled for real, and invoked through the graph
 * -- reaching the real platform API over a real HTTP call, the same
 * way it did when invoked directly.
 *
 * What this deliberately does NOT do, named rather than glossed over:
 * call a real LLM. Getting an LLM to actually pick this tool and
 * choose its arguments requires a real model provider (OpenAI/
 * Anthropic/...) and a real API key -- a project-external decision
 * this example doesn't make on a reader's behalf. What's
 * AgentGuard-specific is the tool itself (see langchain-agent.ts);
 * which LLM decides to call it is a separate, already-solved problem
 * for every LangGraph.js example on the internet, not something this
 * project needs to re-prove.
 *
 * Run it (from this repo, with the platform's own API running --
 * `docker compose up -d && uvicorn apps.api.main:app` -- and a real
 * Agent key from the dashboard's Agents page):
 *
 *   npm install
 *   AGENTGUARD_AGENT_KEY=agk_... npx tsx examples/langgraph-agent.ts
 *
 * (`tsx`, not `node --experimental-strip-types` -- see
 * examples/basic-agent-loop.ts's own docstring for why.)
 */
import { AIMessage } from "@langchain/core/messages";
import { StateGraph, MessagesAnnotation } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { tool } from "@langchain/core/tools";
import { z } from "zod";

import { AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout } from "../src/index.js";

const executeSchema = z.object({
  connectorId: z.string().describe("A connector id from listConnectors()"),
  action: z.string().describe('An action the connector supports, e.g. "list_repos"'),
  params: z.record(z.string(), z.unknown()).optional().describe("The action's keyword arguments"),
});

function buildExecuteTool(client: AgentGuardClient) {
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
        "Execute a guarded action against a connected external service through AgentGuard.",
      schema: executeSchema,
    },
  );
}

function buildGraph(executeTool: ReturnType<typeof buildExecuteTool>) {
  // A minimal real LangGraph.js graph -- one tool node, built from
  // LangGraph.js's own prebuilt ToolNode (the same one a real
  // createReactAgent graph uses internally). No LLM node is wired in
  // on purpose (see this module's own docstring); what's being proven
  // is that the tool and the graph plumbing around it are real and
  // correctly shaped, not that a full agent loop runs end to end
  // without a model.
  const graph = new StateGraph(MessagesAnnotation)
    .addNode("tools", new ToolNode([executeTool]))
    .addEdge("__start__", "tools");
  return graph.compile();
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

  // Proves the graph wiring is real LangGraph.js, not hand-rolled.
  const compiled = buildGraph(executeTool);
  console.log(`Compiled graph nodes: ${Object.keys(compiled.getGraph().nodes).join(", ")}`);

  const connectors = await client.listConnectors();
  const match = connectors.find((c) => c.connector_type === connectorType);
  if (!match) {
    console.log(
      `No connected '${connectorType}' connector for this tenant -- connect one from the ` +
        `dashboard's Integrations page first.`,
    );
    return;
  }

  // The real call: routes a real tool-call message through the
  // compiled graph's ToolNode, exactly the way a real LLM's tool-call
  // output would be routed, except the tool call here is hard-coded
  // instead of coming from a model.
  console.log(`Invoking the tool for real, through the graph: ${connectorType}.${action}...`);
  const result = await compiled.invoke({
    messages: [
      new AIMessage({
        content: "",
        tool_calls: [
          {
            id: "call_1",
            name: "execute_via_agentguard",
            args: { connectorId: match.id, action, params: {} },
          },
        ],
      }),
    ],
  });
  const lastMessage = result.messages[result.messages.length - 1];
  console.log(`Tool result (via graph): ${JSON.stringify(lastMessage.content)}`);
}

main();
