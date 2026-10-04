/**
 * Thin, framework-agnostic client for the AgentGuard platform API
 * (apps/api, in the parent agentguard-ai-2 repo this package is
 * developed alongside) -- see docs/PRODUCTIZATION_ROADMAP.md, Phase D,
 * and sdk/python/agentguard_sdk/client.py's own docstring, which this
 * file mirrors on purpose: same shape, same method names, same
 * behavior, a second language front door onto the identical
 * enforcement layer, not a divergent reimplementation.
 *
 * Deliberately standalone: zero runtime dependencies -- native fetch
 * (Node 18+, and every browser) instead of axios/node-fetch, the same
 * "this package has nothing else in it" discipline the Python SDK
 * applies with httpx as its one dependency.
 *
 * Authenticates as an Agent (an AgentGuard "agk_..." API key, minted
 * from the dashboard's Agents page or POST /tenant/agents) -- not a
 * human login. No email/password fallback, on purpose: a library meant
 * to be embedded in someone else's agent framework code shouldn't hold
 * a human's password at all.
 *
 * run() mirrors apps/mcp_server/server.py's own execute_connector_action
 * tool: call POST /connectors/{id}/execute, and if the platform comes
 * back "pending_approval" (Phase 7 -- it never blocks a human-in-the-loop
 * decision behind an open connection), poll GET /approvals/{id}
 * client-side until a human resolves it or it expires, then return the
 * real result or throw. From the caller's point of view this still
 * reads as one awaited call that "waits for the answer."
 */

export const DEFAULT_BASE_URL = "http://localhost:5000/api";
export const AGENT_KEY_PREFIX = "agk_";
const POLL_INTERVAL_MS = 2000;
const MAX_WAIT_MS = 30 * 60 * 1000;

/** Raised for any non-2xx response that isn't one of the more specific errors below. */
export class AgentGuardError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AgentGuardError";
  }
}

/** A human denied this action from the AgentGuard dashboard's Approvals page. */
export class AgentGuardDenied extends AgentGuardError {
  constructor(message: string) {
    super(message);
    this.name = "AgentGuardDenied";
  }
}

/** Nobody resolved this approval request (or the platform itself expired it) within the wait budget. */
export class AgentGuardTimeout extends AgentGuardError {
  constructor(message: string) {
    super(message);
    this.name = "AgentGuardTimeout";
  }
}

export interface AgentGuardClientOptions {
  baseUrl?: string;
  /** Milliseconds. Applies to every individual HTTP request, not the overall run() wait. */
  timeoutMs?: number;
  /** Exposed for testing -- inject a stub instead of the real global fetch. Normal callers never pass this. */
  fetchImpl?: typeof fetch;
}

export interface RunOptions {
  idempotencyKey?: string;
  pollIntervalMs?: number;
  maxWaitMs?: number;
}

export interface ExecuteResult {
  status: "completed" | "pending_approval" | "ready" | "executing" | "denied" | "expired" | "unknown" | "failed";
  operation_id?: string;
  error?: string | null;
  result: unknown;
  approval_id: string | null;
}

export interface ConnectorInfo {
  id: string;
  connector_type: string;
  label: string;
  connected_at: string;
  is_revoked: boolean;
}

export class AgentGuardClient {
  private readonly baseUrl: string;
  private readonly agentKey: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(agentKey: string, options: AgentGuardClientOptions = {}) {
    if (!agentKey.startsWith(AGENT_KEY_PREFIX)) {
      throw new Error(
        `agentKey doesn't look like an AgentGuard agent key (expected an '${AGENT_KEY_PREFIX}' ` +
          `prefix) -- mint one from the dashboard's Agents page, or POST /tenant/agents. This ` +
          `client only authenticates as an Agent, not a human's own login.`,
      );
    }
    this.agentKey = agentKey;
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  /** What's connected for this tenant right now. */
  async listConnectors(): Promise<ConnectorInfo[]> {
    const resp = await this.request("GET", "/connectors");
    return (await resp.json()) as ConnectorInfo[];
  }

  /**
   * Run one action against a connected service through AgentGuard's
   * guardrail -- policy engine, human approval gate, and all.
   *
   * Some actions run immediately; others require a human to approve
   * them first from the AgentGuard dashboard. This call awaits
   * (polling) until that happens, then returns the real result, or
   * throws AgentGuardDenied / AgentGuardTimeout / AgentGuardError.
   */
  async run(
    connectorId: string,
    action: string,
    params: Record<string, unknown> = {},
    options: RunOptions = {},
  ): Promise<ExecuteResult> {
    const pollIntervalMs = options.pollIntervalMs ?? POLL_INTERVAL_MS;
    const maxWaitMs = options.maxWaitMs ?? MAX_WAIT_MS;
    if (pollIntervalMs <= 0 || maxWaitMs <= 0) throw new Error("Polling values must be positive");
    const operationKey = options.idempotencyKey ?? crypto.randomUUID();
    const resp = await this.request("POST", `/connectors/${connectorId}/execute`, {action, params}, operationKey);
    let data = (await resp.json()) as ExecuteResult;
    const deadline = performance.now() + maxWaitMs;
    while (true) {
      if (data.status === "completed") return data;
      if (data.status === "denied") throw new AgentGuardDenied(data.error ?? "Action denied");
      if (data.status === "expired") throw new AgentGuardTimeout("Approval expired");
      if (!["pending_approval", "ready", "executing"].includes(data.status)) {
        throw new AgentGuardError(`Execution ${data.status}: ${data.error ?? "unknown outcome"}. Do not retry with a new key. Operation: ${data.operation_id}`);
      }
      if (performance.now() >= deadline) throw new AgentGuardTimeout(`Operation may still finish. Reuse idempotency key ${operationKey}`);
      await sleep(Math.min(pollIntervalMs, Math.max(0, deadline - performance.now())));
      const poll = await this.request("GET", `/connectors/operations/${data.operation_id}`);
      const next = (await poll.json()) as ExecuteResult;
      data = {...data, ...next};
    }
  }

  private async request(
    method: string,
    path: string,
    body?: unknown,
    idempotencyKey?: string,
  ): Promise<Response> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    let resp: Response;
    try {
      resp = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${this.agentKey}`,
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
    if (!resp.ok) {
      const text = await resp.text();
      throw new AgentGuardError(`AgentGuard rejected this request (${resp.status}): ${text}`);
    }
    return resp;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
