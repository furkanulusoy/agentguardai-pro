/**
 * A minimal, runnable example of an agent loop calling AgentGuard
 * through @agentguard/sdk -- deliberately framework-agnostic (see the
 * package README's "What this is not (yet)" section for why this isn't
 * a LangChain/LangGraph.js-specific example). Any framework's
 * tool-calling convention wraps a plain async function like
 * `runAction` below the same way; that function is the part that's
 * actually AgentGuard-specific.
 *
 * Run it (from this repo, with the platform's own API running --
 * `docker compose up -d && uvicorn apps.api.main:app` -- and a real
 * Agent key from the dashboard's Agents page):
 *
 *   npm install
 *   AGENTGUARD_AGENT_KEY=agk_... npx tsx examples/basic-agent-loop.ts
 *
 * Or, to see the human-approval path (rather than the auto-allowed one):
 *
 *   AGENTGUARD_AGENT_KEY=agk_... AGENTGUARD_ACTION=close_issue \
 *       npx tsx examples/basic-agent-loop.ts
 *
 * (Not `node --experimental-strip-types` -- Node's native type
 * stripping leaves relative import specifiers untouched, so `../src/index.js`
 * resolves against a literal `src/index.js` file that only exists
 * after `npm run build` writes it to `dist/`, not `src/`; the example
 * imports `../src/index.js` because that's the NodeNext-compiled shape
 * this package's own source uses. `tsx` (a devDependency here)
 * resolves `.ts` sources directly, which is what actually runs this
 * file without a separate build step first -- found by actually
 * trying the documented `node --experimental-strip-types` command and
 * watching it fail with `ERR_MODULE_NOT_FOUND`, not by inspection.)
 */
import {
  AgentGuardClient,
  AgentGuardDenied,
  AgentGuardError,
  AgentGuardTimeout,
} from "../src/index.js";

async function runAction(
  client: AgentGuardClient,
  connectorType: string,
  action: string,
): Promise<void> {
  const connectors = await client.listConnectors();
  const match = connectors.find((c) => c.connector_type === connectorType);
  if (!match) {
    console.log(
      `No connected '${connectorType}' connector for this tenant -- connect one from the ` +
        `dashboard's Integrations page first.`,
    );
    return;
  }

  console.log(`Calling ${connectorType}.${action} through AgentGuard...`);
  try {
    const outcome = await client.run(match.id, action);
    console.log(`Result: ${JSON.stringify(outcome.result)}`);
  } catch (err) {
    if (err instanceof AgentGuardDenied) {
      console.log("A human denied this action from the AgentGuard Approvals page.");
    } else if (err instanceof AgentGuardTimeout) {
      console.log("Nobody resolved the approval request in time.");
    } else if (err instanceof AgentGuardError) {
      console.log(`AgentGuard rejected this action: ${err.message}`);
    } else {
      throw err;
    }
  }
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
  await runAction(client, connectorType, action);
}

main();
