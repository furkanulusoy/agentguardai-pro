# AgentGuard: Productization Roadmap

**Status:** Phases A through E all shipped 2026-08-20, same day as this
document — see `CHANGELOG.md`'s Phase 17 through 29 for the full,
dated writeups. In build order: Policy Engine (A), Agent Identity (B),
Audit Trail (C), Python SDK (D), agent credential scoping (an unplanned
closure of a gap Phase B's own sketch left open), an internal security
review (found and fixed one real privilege-escalation bug), the Policy
Simulator (closing Phase C's own signature-feature deferral), the
TypeScript SDK (closing Phase D's own deferral), Slack as the third
connector (half of Phase E), two rounds of real framework examples for
both SDKs (LangGraph + CrewAI for Python, LangChain.js + LangGraph.js
for TypeScript — closing Phase D's remaining deferral), a
developer-experience pass (the other half of Phase E, plus a real bug
found while writing it — see Phase 28), and an Agent's own RBAC
permission set (`AgentPermissionGrant`, Phase 29 — closes a gap named
since Phase B itself). Every phase this document originally sketched
is now shipped, along with every unplanned gap this session's own live
verification surfaced along the way. Next up: real email delivery, an
independent third-party security review, or publishing both SDKs to
PyPI/npm — see Roadmap below, none blocking the others.

**Update, Phase 31 (`CHANGELOG.md`):** self-host with Docker shipped --
`docker compose up` runs the full stack (Postgres + `apps/api` +
`apps/web`), no Python/Node install needed, closing the real
distribution gap a non-technical trial user would have hit ("how do I
even run this") before it could ever become a product someone pays for.
Also switched the license from MIT to AGPL-3.0-or-later and corrected
author/copyright attribution (Phase 30) -- an ownership decision, not
an engineering one, but recorded here because it changes what "someone
forks this and hosts it themselves" actually obligates them to do.

**Update, Phase 32 (`CHANGELOG.md`):** an external audit checked
whether the real "sensitive action needs human approval" scenario
actually works end to end, insisting nothing be invented to make the
code look like it supports capability it doesn't. It confirmed the
mechanism is real and closed the one real gap it found:
`resolve_approval`'s `approved=True` branch (the moment a real guarded
call actually executes) had only ever been verified manually, never by
an automated test -- `tests/test_e2e_approval_lifecycle.py` closes that
for real. It also confirmed, directly and without hedging, what this
platform still does NOT have: any payments/financial connector, a
`CRITICAL` risk tier, or dynamic risk classification based on an
action's actual parameter values (e.g. a specific amount) -- risk is
still a fixed LOW/MEDIUM/HIGH lookup per `(connector_type, action)`.
Neither of those is on this roadmap's Tier 2/3 lists above because
nobody has asked for a payments connector yet -- named here so a future
reader doesn't assume it's already scoped.

**Written:** 2026-08-20, in response to an outside expert's assessment of the
project (full text preserved in the conversation this doc came from).
Supersedes the prioritization in `docs/ROADMAP_TO_PRODUCTION.md`, which
predates the FastAPI/Postgres platform entirely (it's written against the
old single-tenant Flask reference server). That file still has good general
content on compliance/market-validation; this one is the actionable,
platform-era plan.

## Zero: the assessment was right about almost everything, but it's dated

The reviewer's report is dated **19 Ağustos**. Between then and now (this
session, 20 Ağustos), five phases shipped that directly close gaps the
report flags as open. Before adopting the report's plan wholesale, here's
what's already different — verified against the current code, not asserted:

| Report says | Actual current state |
|---|---|
| "backend süreci approval beklerken çökerse request DB'de kalıyor ama bekleyen process kaybolabiliyor" (production blocker) | **Already fixed, Phase 7.** `POST /connectors/{id}/execute` doesn't block at all — it returns `pending_approval` immediately; the guarded call only runs inside `POST /approvals/{id}/resolve`, at the moment a human actually decides, in whatever process happens to handle that request. No in-memory `threading.Event` registry exists anymore. Verified with two real concurrent HTTP requests racing to resolve the same approval (one 200, one 409 — Postgres's row lock does the serializing). This is genuinely solved, not partially. |
| "browser token'ları localStorage içinde tutulduğu belirtiliyor" | **Already fixed, Phase 15.** Refresh tokens are an httpOnly, `SameSite=lax` cookie. `localStorage` holds only the short-lived access token. Verified live in a browser: corrupted the stored access token, confirmed the app silently recovered via the cookie, confirmed logout actually clears server-side state. |
| "Şu anda gerçek connector olarak Gmail var" (implies GitHub is still to build) | **GitHub connector already exists, Phase 8.** Read/write actions, its own risk-level table, its own policy. Two real connectors today, not one. |
| "111 test... rapor... platform katmanının önemli bölümü manuel doğrulanmış" | **No longer true.** 171 tests now, and 7 of the 17 test files (`test_infra_api*.py`, `test_rate_limit.py`) are real HTTP-level integration tests — `httpx.ASGITransport` against the actual FastAPI app, against a real local Postgres, not mocks. They cover register/login/refresh, RBAC, tenant isolation, the connectors→approvals handoff, rate limiting, invites, GitHub-issue notifications, the httpOnly cookie, and password reset. The platform is not "manually verified" anymore; most of it has an automated regression suite. |
| "no rate limiting or account lockout on /auth/login" (Phase 7's own known-gaps list, which the report is implicitly still working from) | **Already fixed, Phase 12.** Per-IP: 5/min login, 5/hour register, 30/min refresh, 5/hour accept-invite. A real regression test exists for the exact opposite failure mode too (a rate-limited route that crashes on a *successful* response — found by hand-testing, not by the automated suite, and now permanently guarded). |
| Team growth / multi-user (not explicitly scored, but implied by "önce ürünleşme") | **Already fixed, Phase 13.** Admin-initiated invites, one-time hashed links, role selection at invite time, OWNER-can't-be-minted-by-a-mere-admin protection. |
| Password reset (report doesn't mention it, but it's the same class of gap as invites) | **Just shipped, this session, Phase 16.** Admin-initiated (not self-service — see that phase's own honestly-stated limit), revokes all of the target's existing refresh tokens on reset. |
| Pending-approval visibility / "birisi onay beklediğini nasıl bilecek" | **Partially addressed, Phase 14.** A pending approval opens a GitHub Issue in an admin-configured repo, closed automatically on resolution. This is a real notification channel, not yet the rich in-app audit timeline the report describes in section 8 — that gap is real and carries forward below. |

**What this changes about the plan:** two of the report's three "before any
paying customer" blockers (approval reliability, the localStorage token
exposure) are done. That moves **Policy Engine** into the clear #1 slot on
its own — not tied for first with hardening work that's already shipped.

## What's still genuinely open (verified against the current code, not the report)

These are real, checked directly rather than taken on faith:

- **No policy engine.** `connectors/registry.py`'s `RISK_LEVELS` is a
  hardcoded `dict[connector_type][action] -> risk_level`. There is no
  `ALLOW`/`DENY`/`REQUIRE_APPROVAL` condition system, nothing configurable
  per tenant, nothing editable from the dashboard. `README.md`'s own Known
  Limitations section already says this plainly: "Policy is fixed per
  connector type, not per tenant." The report's #1 gap is real.
- **No audit trail as a product surface.** `ApprovalRequest` rows carry
  `requested_by`/`resolved_by`/`resolved_at`/`result_json` — real data,
  but only for the sensitive-action path, and there's no timeline UI, no
  event-by-event trail (policy evaluated → risk assigned → approval →
  execution → result), and non-sensitive actions that ran immediately
  leave no record at all.
- **No Agent Identity.** `Credential` belongs to a `tenant_id` + optional
  `user_id` (a human). Every action is attributed to whichever human's JWT
  made the API call — there's no first-class "this is `CustomerSupportAgent`,
  owned by this human, scoped to these connectors" entity. The MCP server
  authenticates as a human account today.
- **No SDK.** `apps/mcp_server` is the only non-dashboard client, and it's
  MCP-specific. No `pip install agentguard-sdk` / `npm install
  @agentguard/sdk` for a framework that isn't MCP-shaped (LangGraph,
  CrewAI, a raw OpenAI Agents loop).
- **No billing.** A `billing.admin` RBAC permission code exists and is
  granted to OWNER — nothing reads or enforces it. No Stripe integration,
  no plan/usage tracking anywhere in the codebase.
- **Independent (non-AI, third-party) security review.** Still hasn't
  happened. Two internal AI-assisted review passes have found and fixed
  real issues (Phase 7's own writeup lists them plainly), which is
  better than nothing but not the same thing.
- **Real email delivery.** Invite links and password-reset links are both
  correct and secure, but copy-pasted by a human today. This is also the
  actual blocker on *self-service* password reset (proving device-holder
  identity pre-authentication needs an out-of-band channel).
- **Per-tenant policy configuration** — same root cause as the policy
  engine gap above, not a separate item.

## Positioning (adopting the reviewer's framing — it's correct)

> **AI agents can act. AgentGuard decides what they're allowed to do.**

Not "another AI security platform." The specific, defensible claim:
action-level interception + policy + human approval + audit, sitting
between any agent and any connector, regardless of which model or
framework is driving the agent. MCP is today's integration point, not the
ceiling — the same enforcement layer belongs in front of LangGraph,
CrewAI, or a raw agent loop eventually. Don't build toward "AI Security
Platform" (SIEM, threat intel, MITRE mapping) — that's a different company
with a different roadmap, and chasing it is the fastest way to end up with
nothing done well.

## The roadmap

Every phase ends the way every phase in `CHANGELOG.md` already has: real
tests against real Postgres, `ruff`/`mypy`/`tsc` clean, a live
browser/API check where the change is observable, an honest note on what's
still not covered, and a git checkpoint before moving on. That discipline
is why the corrections above were possible to make with confidence — keep
it.

### Phase A — Policy Engine (the actual #1 gap) — SHIPPED 2026-08-20

Replaced `RISK_LEVELS`'s static dict with a real per-tenant rule system,
deterministic, not ML/LLM risk-scoring (the reviewer is right about this:
a security product's first version needs rules a customer can read and
predict, not a model's opinion). See `CHANGELOG.md`'s Phase 17 for the
full writeup; summary here for what this document specifically predicted
versus what actually shipped:

- Shipped as planned: `PolicyRule` rows keyed on `(tenant_id,
  connector_type, action) -> ALLOW | DENY | REQUIRE_APPROVAL`, the
  existing hardcoded table remaining the system default every tenant
  starts from (backward-compatible, zero effect on a tenant that never
  sets a rule); `GET/PUT/DELETE /tenant/policies[/{connector_type}/{action}]`;
  `execute_connector_action` evaluating the tenant override before
  falling back to the connector's built-in `Policy`; a dashboard page.
- **Deliberately simplified from this document's original sketch:** no
  `target_pattern`/`environment` dimensions on the rule -- neither
  concept exists anywhere else in the schema yet (there's no
  environment/deployment-stage model at all), and adding them
  speculatively, unused, would have been exactly the kind of
  premature-abstraction this project's own conventions argue against.
  Add them when a real use case needs them, not before.
- **Real gap this phase did NOT close, carried forward:** the "why was
  this blocked" explain feature sketched here. A `DENY`/`REQUIRE_APPROVAL`
  response today is a bare error/approval record, same as before this
  phase -- it doesn't yet say *which* rule (tenant override vs. system
  default) produced the outcome. Cheap to add once Phase C's audit event
  log exists to hang the explanation on; picking it up there rather than
  retrofitting it twice.

### Phase B — Agent Identity — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phase 18 for the full writeup; summary here for
what this document specifically predicted versus what actually shipped:

- Shipped as planned: `Agent` model (`tenant_id`, `name`,
  `owner_user_id`, `api_key_hash`); `apps/mcp_server/server.py` can now
  authenticate as an `Agent` via `AGENTGUARD_AGENT_KEY` instead of a
  human's JWT; `ApprovalRequest.agent_id` alongside
  `requested_by_user_id`, so "which agent" and "which human is
  accountable" are both answerable, independently, in the dashboard and
  the API; `PolicyRule.agent_id` (Phase A) for a per-agent override on
  top of the tenant-wide default; the "what can this agent do" signature
  feature, as a read-only effective-policy view per agent
  (`GET /tenant/agents/{id}/policies`, the `/agents` dashboard page's
  expandable panel) -- genuinely cheap, exactly as predicted, once
  Phases A and B's resolution logic both existed.
- **Deliberately simplified from this document's original sketch:**
  "its own scoped credential grants (which connectors, which actions)"
  did NOT ship as a separate, independent grant system this phase. An
  Agent's effective RBAC *permissions* were its owner's, full stop --
  narrowed only by whatever PolicyRule overrides a tenant admin sets for
  it, not by an agent-specific role/permission set of its own. Building
  a second, parallel RBAC system for agents (distinct roles, distinct
  permission codes) is still real, larger future work, named honestly
  in `README.md`'s Roadmap rather than silently assumed to already
  exist. **Update, Phase 21 (`CHANGELOG.md`):** the "which connectors"
  half of this deferral was closed the same day --
  `AgentCredentialGrant`, deny-by-default. **Update, Phase 29
  (`CHANGELOG.md`):** the remaining half -- "does this agent have its
  own permission codes, independent of its owner's roles" -- closed
  too, via `AgentPermissionGrant`. Not a second, parallel RBAC system
  (no agent-specific roles) as this note once speculated might be
  needed -- a simpler, real mechanism instead: an Agent inherits its
  owner's full permission set by default, and narrows to the
  intersection of that set and its own explicit grants the moment at
  least one exists. See that phase's own CHANGELOG entry for why
  "inherit by default" was the right call here, unlike
  `AgentCredentialGrant`'s own deny-by-default.
- `apps/mcp_server/server.py`'s `AGENTGUARD_EMAIL`/`AGENTGUARD_PASSWORD`
  path was kept working, unchanged, as a fallback -- not removed or
  deprecated, since not every deployment has an Agent set up yet.

### Phase C — Audit Trail as a real product surface — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phase 19 for the full writeup; summary here for
what this document specifically predicted versus what actually shipped:

- Shipped as planned: new `AuditEvent` table -- one row per `execute`
  call regardless of decision, closing the exact gap named here
  (previously only the REQUIRE_APPROVAL path left any record at all).
  `GET /tenant/audit` with connector/action/decision/agent filters (date
  range wasn't added -- see below). A dashboard table view (not a
  timeline visualization -- see below).
- **A real gap this phase's own tests found, not predicted by this
  document:** the ALLOW-path's exception handling only caught three
  known exception types; anything else (a real one was found live --
  Gmail's own OAuth library raising an unwrapped exception type) skipped
  the audit write entirely and 500'd, silently defeating this phase's
  whole premise for exactly the failures most worth auditing. Fixed by
  broadening to catch and record any exception before re-raising it
  unchanged. Worth naming here: this is the kind of gap that specific,
  concrete phase work surfaces and a roadmap sketch never would have.
- **Deliberately simplified from this document's original sketch:** no
  date-range filter on `GET /tenant/audit` yet (only connector/action/
  decision/agent) -- add when a real need for it shows up, not
  speculatively. The dashboard page is a filterable table, not the
  "audit timeline view" graphics work sketched here -- a table answers
  the same real question (what happened, why, with what outcome) with
  far less design investment; revisit only if a real user asks for the
  richer visualization specifically.
- **Signature feature explicitly NOT built this phase, named rather
  than silently dropped: the Policy Simulator.** This phase's event log
  is the prerequisite it needed, and that now exists -- but simulating
  "if this rule had existed for the last 7 days" against it is real,
  separate work (querying/replaying the log against a hypothetical
  rule), not a marginal addition the way Phase B's "what can this agent
  do" turned out to be. Tracked in `README.md`'s Roadmap as a real,
  open Tier 2 item. **Update, Phase 23 (`CHANGELOG.md`):** shipped the
  same day, once this phase's event log had real data to simulate
  against -- see that phase's own writeup for what it turned out to
  mean in practice (arithmetic over real historical counts, scoped to
  exactly what one `PolicyRule` affects, not a full guardrail replay).

### Phase D — SDK — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phase 20 for the full writeup; summary here for
what this document specifically predicted versus what actually shipped:

- Shipped as planned: `agentguard-sdk` (Python), a thin client over the
  same platform API `apps/mcp_server` already calls -- not a
  reimplementation, a second front door onto the same enforcement layer,
  exactly as sketched. Authenticates as an Agent (Phase B). One real
  worked example (`sdk/python/examples/basic_agent_loop.py`), run live
  against the real platform to prove it, not just unit-tested.
- **Real gaps this phase's own tests found, not predicted here:** `GET
  /connectors` and `GET /approvals`/`GET /approvals/{id}` had never
  been extended to accept an Agent's key the way Phase B's
  `execute_connector_action` was -- an Agent (including
  `apps/mcp_server` itself, already shipped) could create a pending
  approval but couldn't discover its own connectors first or poll for
  its own outcome. Fixed the same way `execute_connector_action` already
  worked: Actor-based auth on the reads, human-only kept on the writes
  (`revoke_connector`, `resolve_approval`). This is exactly the kind of
  gap a roadmap sketch can't predict and a real, honest integration test
  does -- worth naming for that reason, same as Phase 19's own exception-
  handling fix.
- **Deliberately simplified from this document's original sketch:** the
  method name is `run()`, not `check()` -- `check` reads as a dry-run/
  permission-check-only call that doesn't actually execute anything,
  which isn't what this does; `run()` says what it is. No
  `target`-level granularity in the call signature (`connector, action`
  only, no `target`) -- no connector action in this codebase takes a
  free-form target parameter distinct from its own `params` yet, so
  adding one speculatively would be exactly the premature-abstraction
  this project's own conventions argue against.
- **NOT shipped this phase, closed later:** the TypeScript SDK
  (`@agentguard/sdk`) -- see its own entry below, shipped the same day
  as `CHANGELOG.md`'s Phase 24. Framework-specific examples -- two per
  SDK now (LangGraph + CrewAI for Python, LangChain.js + LangGraph.js
  for TypeScript) shipped later too, `CHANGELOG.md`'s Phases 26 and 27.
  Neither SDK is published to its package registry (PyPI/npm) yet.

### TypeScript SDK (closes Phase D's own deferred item) — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phase 24 for the full writeup. `sdk/typescript/`
(`@agentguard/sdk`) mirrors `sdk/python`'s `listConnectors()`/`run()`
shape exactly, using native `fetch` (zero runtime dependencies) instead
of `httpx`. Live-verified against the real running platform (a fresh
Agent, granted the tenant's real GitHub connector via Phase 21's grant
endpoint, run as a real separate Node process against `dist/index.js`
-- the compiled artifact a real consumer installs, not the TypeScript
source) -- reached the real GitHub connector and correctly surfaced a
real `401 Bad credentials` (the tenant's stored OAuth token has been
stale since Phase 21's own live check) as a typed `AgentGuardError`,
with the resulting `AuditEvent` confirmed on `/tenant/audit`. Wired
into CI as its own `sdk-typescript` job, mirroring how `sdk/python` was
wired into CI in Phase 20. Same deferrals as the Python SDK: not
published to npm. Real LangChain.js and LangGraph.js examples shipped
later -- `CHANGELOG.md`'s Phases 26 and 27.

### Framework examples for both SDKs (LangGraph, LangChain.js, CrewAI, LangGraph.js) — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phases 26 and 27 for the full writeup. Closes the
"framework-specific example" gap both SDK phases above named as
deferred -- two examples per SDK now:

- Python: `sdk/python/examples/langgraph_agent.py` wraps `run()` as a
  real LangChain `@tool` inside a real LangGraph graph;
  `sdk/python/examples/crewai_agent.py` wraps it as a real
  `crewai.tools.BaseTool`.
- TypeScript: `sdk/typescript/examples/langchain-agent.ts` wraps
  `run()` as a real LangChain.js `StructuredTool`;
  `sdk/typescript/examples/langgraph-agent.ts` drops that same tool
  into a real LangGraph.js `StateGraph`.

All four live-verified against the real running platform, not just
type-checked -- see the CHANGELOG entries for what exactly that proved
and, just as importantly, what it deliberately didn't (none of the four
call a real LLM or run a full agent loop; that needs a model-provider
API key this project won't choose on a reader's behalf). A real,
structural finding surfaced along the way, not a code bug: CrewAI's
dependency tree cannot share this repo's own `.venv` with `apps/api`/
`apps/mcp_server` -- installing it downgraded `mcp` far enough to break
`apps/mcp_server`'s own import, caught by re-running the full test
suite, not by inspection. `crewai_agent.py` now documents and expects
its own dedicated `sdk/python/.venv-crewai` instead.

### Agent credential scoping (not in this document's original plan) — SHIPPED 2026-08-20

Not one of the originally-sketched phases -- this document didn't
anticipate closing Phase B's own "which connectors" deferral as a
standalone piece of work. Done instead of starting Phase E, which was
explicitly deferred (see below), because this was fully self-contained
(no external provider/account decision blocking it, unlike real email
delivery) and directly named as real, tracked debt rather than
something new. See `CHANGELOG.md`'s Phase 21 for the full writeup:
`AgentCredentialGrant`, deny-by-default, checked in
`execute_connector_action` ahead of `task_scope`. A real, intentional
breaking change for every Agent created in Phases B-D -- verified live
against one of this session's own pre-existing demo agents, not just
asserted.

### Agent RBAC permission set (closes Phase B's other original deferral) — SHIPPED 2026-08-20

The other half of "its own scoped credential grants" Phase B's own
sketch named and didn't build -- see `CHANGELOG.md`'s Phase 29 for the
full writeup: `AgentPermissionGrant`
(`infrastructure/database/models/agent_permission_grant.py`). Unlike
`AgentCredentialGrant` above, deliberately NOT deny-by-default -- an
Agent with zero grants keeps inheriting its owner's full RBAC
permission set unchanged, and only narrows to the intersection of the
owner's held permissions and its own granted set once at least one
grant exists. That model's own docstring explains the asymmetry at
length: `agent.execute`/`connector.read`/`approval.read` are what an
Agent *is*, not an optional extra like which connector it may touch, so
defaulting this to empty would have broken every existing Agent's
basic ability to function -- a real regression, not a tightening.
Live-verified against a real pre-existing agent, not just asserted:
granted only `agent.execute` and confirmed two other Actor-gated routes
(`connector.read`, `approval.read`) newly 403'd for that same agent
key while a real `execute` call still got through to the real connector
layer; revoked the grant and confirmed full inheritance came back.

### Phase E — third connector + developer experience — SHIPPED 2026-08-20

- Slack shipped as the reviewer's suggested third connector, and its
  risk shape is the reason it was worth picking specifically: five
  actions across three policy tiers (a safe read, a routine
  auto-allowed write, and three sensitive writes -- channel creation,
  message deletion, user invites -- requiring approval), the first
  connector where "this is a write" and "this needs a human" come
  apart. See `CHANGELOG.md`'s Phase 25 for the full writeup and live
  verification.
- The "developer experience pass" half of this phase's original
  sketch -- quickstart docs/5-minute install, and one example per
  supported agent framework -- is now fully closed too. See the
  Framework examples phases and the Developer experience pass entry
  below.

### Developer experience pass (the other half of Phase E's original sketch) — SHIPPED 2026-08-20

See `CHANGELOG.md`'s Phase 28 for the full writeup. A third quickstart
in the main `README.md`, "Quickstart — call a guarded action from your
own agent code (SDK)," takes a reader from "the platform is running"
to a real Agent key calling a real guarded action from real Python/
TypeScript code, then points at both SDKs' own READMEs for the
framework-specific examples (see the Framework examples phases above).

**A real bug found and fixed while writing this, not this phase's own
goal:** the documented way to run any of the three TypeScript examples
(`node --experimental-strip-types examples/foo.ts`) has never actually
worked, since Phase 24 -- Node's type stripping doesn't rewrite
relative import specifiers, so `../src/index.js` only resolves after a
build step the documented command never ran. Every prior live
verification in this session (Phases 24, 26, 27) had unknowingly worked
around this with a hand-written temporary runner against the already-
built `dist/`, never actually running the documented command until this
phase tried following its own instructions. Fixed with `tsx` (a new
devDependency) and corrected docs across all three examples' own
docstrings and `sdk/typescript/README.md`.

### Ongoing, not a phase — schedule alongside the above, doesn't block them

- **Independent third-party security review.** Doesn't require code
  changes from this session to *schedule*; the right time to have someone
  actually start is once Phase B (Agent Identity) lands, since that phase
  touches the authorization surface the review would most want to look at.
- **Real email delivery.** A real provider integration (SendGrid/SES/
  Postmark) that both the invite flow and password-reset flow switch to
  using instead of the copy-paste link. Self-contained, can land whenever
  — doesn't block or get blocked by A–E.

### Deliberately not doing yet (adopting the reviewer's list — it's correct)

Threat intelligence, SIEM, MITRE mapping, a 10-connector push, mobile app,
ML/LLM-based risk scoring as the *primary* mechanism, a Kubernetes
deployment story, billing (until there's a paying customer to bill),
"AI Security Platform" as the positioning. Each of these is either a
different company's roadmap or premature relative to what's actually
unbuilt above.

## Immediate next step

The reviewer's own suggested first move — a no-code gap audit against the
actual repo before writing a plan — is what produced the correction table
at the top of this document. That's done; this is that audit's output.
The literal next action is starting Phase A (Policy Engine schema +
`/tenant/policies` endpoints), following the same phase discipline as
`CHANGELOG.md` Phase 1–16: plan the schema, migrate, build, test against
real Postgres, verify live, document, checkpoint.
