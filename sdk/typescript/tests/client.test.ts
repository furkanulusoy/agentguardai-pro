/**
 * Unit tests for src/client.ts, against a stubbed `fetch` (injected via
 * the constructor's `fetchImpl` option -- see that option's own
 * comment). This tests the client's own logic deterministically
 * (request shape, polling behavior, error mapping) the way
 * tests/test_sdk_client.py tests the Python SDK against the real
 * platform app instead -- Node has no equivalent of httpx.ASGITransport
 * for an in-process FastAPI app, so a stubbed fetch is the honest
 * substitute here; this package's own live verification against the
 * real running platform (see CHANGELOG.md) covers what this can't.
 */
import { describe, expect, it, vi } from "vitest";
import {
  AgentGuardClient,
  AgentGuardDenied,
  AgentGuardError,
  AgentGuardTimeout,
} from "../src/client.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AgentGuardClient construction", () => {
  it("rejects a key without the agent prefix", () => {
    expect(() => new AgentGuardClient("not-an-agent-key")).toThrow(
      /doesn't look like an AgentGuard agent key/,
    );
  });
});

describe("listConnectors", () => {
  it("sends the agent key and returns the real parsed data", async () => {
    const fetchImpl = vi.fn(async (url: string | URL, init?: RequestInit) => {
      expect(String(url)).toBe("http://localhost:5000/api/connectors");
      const headers = init?.headers as Record<string, string> | undefined;
      expect(headers?.Authorization).toBe("Bearer agk_test");
      return jsonResponse([{ id: "c1", connector_type: "github", label: "GitHub", connected_at: "now", is_revoked: false }]);
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    const connectors = await client.listConnectors();
    expect(connectors).toHaveLength(1);
    expect(connectors[0]?.connector_type).toBe("github");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});

describe("run", () => {
  it("returns immediately for a non-pending response", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ status: "completed", result: { ok: true }, approval_id: null }),
    );
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    const outcome = await client.run("cred-1", "list_repos");
    expect(outcome.status).toBe("completed");
    expect(outcome.result).toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("polls until approved, then returns the real result", async () => {
    let pollCount = 0;
    const fetchImpl = vi.fn(async (url: string | URL) => {
      if (String(url).endsWith("/execute")) {
        return jsonResponse({ status: "pending_approval", result: null, operation_id: "op-1", approval_id: "appr-1" });
      }
      pollCount += 1;
      if (pollCount < 3) {
        return jsonResponse({ status: "pending_approval", result: null, error: null });
      }
      return jsonResponse({ status: "completed", result: { done: true }, error: null });
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    const outcome = await client.run("cred-1", "close_issue", {}, { pollIntervalMs: 1 });
    expect(outcome.status).toBe("completed");
    expect(outcome.result).toEqual({ done: true });
    expect(outcome.approval_id).toBe("appr-1");
    expect(pollCount).toBe(3);
  });

  it("throws AgentGuardDenied when a human denies it", async () => {
    const fetchImpl = vi.fn(async (url: string | URL) => {
      if (String(url).endsWith("/execute")) {
        return jsonResponse({ status: "pending_approval", result: null, operation_id: "op-1", approval_id: "appr-2" });
      }
      return jsonResponse({ status: "denied", result: null, error: null });
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(client.run("cred-1", "close_issue", {}, { pollIntervalMs: 1 })).rejects.toThrow(
      AgentGuardDenied,
    );
  });

  it("throws AgentGuardTimeout when the approval expires", async () => {
    const fetchImpl = vi.fn(async (url: string | URL) => {
      if (String(url).endsWith("/execute")) {
        return jsonResponse({ status: "pending_approval", result: null, operation_id: "op-1", approval_id: "appr-3" });
      }
      return jsonResponse({ status: "expired", result: null, error: null });
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(client.run("cred-1", "close_issue", {}, { pollIntervalMs: 1 })).rejects.toThrow(
      AgentGuardTimeout,
    );
  });

  it("throws AgentGuardTimeout when max_wait elapses while still pending", async () => {
    const fetchImpl = vi.fn(async (url: string | URL) => {
      if (String(url).endsWith("/execute")) {
        return jsonResponse({ status: "pending_approval", result: null, operation_id: "op-1", approval_id: "appr-4" });
      }
      return jsonResponse({ status: "pending_approval", result: null, error: null });
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(
      client.run("cred-1", "close_issue", {}, { pollIntervalMs: 1, maxWaitMs: 3 }),
    ).rejects.toThrow(AgentGuardTimeout);
  });

  it("throws AgentGuardError when approved but execution failed", async () => {
    const fetchImpl = vi.fn(async (url: string | URL) => {
      if (String(url).endsWith("/execute")) {
        return jsonResponse({ status: "pending_approval", result: null, operation_id: "op-1", approval_id: "appr-5" });
      }
      return jsonResponse({ status: "unknown", result: null, error: "connector exploded" });
    });
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(client.run("cred-1", "close_issue", {}, { pollIntervalMs: 1 })).rejects.toThrow(
      /connector exploded/,
    );
  });

  it("throws AgentGuardError for a non-2xx response from the platform", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "policy denied" }, 403),
    );
    const client = new AgentGuardClient("agk_test", { fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(client.run("cred-1", "list_repos")).rejects.toThrow(AgentGuardError);
  });
});

it("sends a caller supplied idempotency key and never treats UNKNOWN as success", async () => {
  const fetchImpl = vi.fn(async (_url, init) => {
    expect(init.headers["Idempotency-Key"]).toBe("stable-key-123456");
    return jsonResponse({status:"unknown", operation_id:"op-1", error:"PROVIDER_OUTCOME_UNKNOWN"});
  });
  const client=new AgentGuardClient("agk_test", {fetchImpl:fetchImpl as unknown as typeof fetch});
  await expect(client.run("c1","send_message",{}, {idempotencyKey:"stable-key-123456"})).rejects.toThrow(/Do not retry/);
  expect(fetchImpl).toHaveBeenCalledTimes(1);
});
