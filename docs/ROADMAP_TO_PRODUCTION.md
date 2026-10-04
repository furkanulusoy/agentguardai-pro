# Roadmap to production

> **Historical implementation record:** This document preserves early project decisions and test evidence. Its old counts and status statements are not the current product status. Use [`README.md`](../README.md), [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md), and [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for the current pilot release.

This document exists so nobody -- including us -- mistakes "a well-built
open-source reference implementation" for "a production-ready security
product the market trusts." Those are different achievements. This file
is the honest distance between where AgentGuard is today and where a
real security product needs to be, organized by what kind of work
closes each gap: more code, or something no amount of code alone can
produce.

Status legend: **Done** (built and tested in this repo) / **Partial**
(started, real gaps remain) / **Not started** (needs work this repo
doesn't do yet).

## 1. Engineering maturity -- mostly code work

| Item | Status | Notes |
|---|---|---|
| Core guardrail logic (scope, approval, integrity check) | Done | `agentguard/guardrail.py`, exercised by 4 before/after demo scenarios |
| Automated test suite | Done | 75 stdlib `unittest` tests: guardrail, quarantine persistence, MCP adapter, policy config, audit log, approval channels (incl. real multi-threaded `QueueApprover` and `SlackNotifyApprover`), CLI, Flask server (incl. real concurrent approval flow and API key auth) |
| Installable package | Done | `pyproject.toml`, verified with a real editable install in this environment |
| Policy as config (not just Python) | Done | `Policy.from_file()`, JSON always, YAML with `pip install agentguard[yaml]` |
| Structured audit logging | Done | `JsonlAuditLog`, JSON Lines, wired into `AgentGuard` |
| Real (non-scripted) approval channels | Partial | `TerminalApprover`, `QueueApprover`, and `SlackNotifyApprover` are real and tested (the last verified against a real Slack workspace, not just mocks); Slack is a one-way notification, not an interactive button, and there's still no PagerDuty/email integration |
| Reference HTTP deployment | Partial | `server/app.py` is a real, tested Flask app (including the blocking-approval flow under real concurrency, connector-integrity monitoring, persisted quarantine state, and opt-in API key auth) -- but auth is a single shared key (not per-identity), and it's still single-tenant, dev server only |
| Real MCP protocol integration | Done | `agentguard/mcp_adapter.py`'s wiring sketch has been **executed** against a real, installed `mcp==2.0.0` SDK (not just read-verified) -- `Server(on_list_tools=..., on_call_tool=...)`, real `mcp.types` objects, and AgentGuard's scope enforcement all confirmed working end to end in this environment |
| Docker image | Partial | `Dockerfile` written to standard conventions but **still unverified** -- Docker itself is not installed in this environment (a different blocker than the earlier "no registry access") |
| Lint / type-check clean | Done | `ruff check .` -- all checks passed; `mypy agentguard` -- no issues found in 9 source files. Run for real in this session, not asserted |
| CI (GitHub Actions) | Done | Runs tests (3-version Python matrix) on every push/PR; a separate `lint` job already ran `ruff`/`mypy` (this line previously, incorrectly, said it didn't -- corrected after actually reading `.github/workflows/tests.yml`); a new `security` job now runs `pip-audit` against the real installed dependencies. Locally verified: without first upgrading `pip`/`setuptools`, `pip-audit` reports 13 CVEs against those two alone (build tooling, not a declared project dependency); after upgrading them, the real dependencies (Flask, PyYAML, click) come back clean. Still missing: SBOM generation, Dependabot |

## 2. Security assurance -- needs people who aren't us

This is the category that actually determines whether "production-ready
security product" is a true statement, and it's the category a coding
session cannot complete on its own:

- **Independent third-party security audit.** A second AI-assisted
  internal review pass (see CHANGELOG 0.3.0) found and fixed 4 real
  issues in `server/app.py`/`agentguard/approval.py`/`agentguard/policy.py`
  -- a real step up from "nobody has looked at this code," but still not
  independent, and still not the same thing as a paid, independent firm
  or recognized researcher going through the codebase adversarially.
  Reviewer and author sharing the same blind spots is exactly the
  failure mode an independent audit exists to catch.
- **Penetration testing against a real deployment**, not the mock
  services in this repo.
- **Formal threat modeling review** by someone other than the author --
  `docs/THREAT_MODEL.md` is a reasonable start, not a substitute for
  outside adversarial review.
- **Fuzzing / property-based testing** of the policy engine and MCP
  adapter against malformed or adversarial input, beyond the
  hand-written unit tests here.
- **Supply-chain review** of every dependency once real ones are added
  (Flask, PyYAML, click, and whatever a real MCP integration pulls in).
  Dependency vulnerability scanning is now wired into CI (`pip-audit`,
  see section 1's CI row) -- SBOM generation, pinned versions (this repo
  still uses version ranges, no lockfile), and Dependabot are still not
  started.
- **A real disclosure process with track record.** `SECURITY.md` states
  a policy; it has no history yet of actually having handled a report.

None of this can be simulated or asserted into existence. It requires
real outside people spending real time on this specific codebase.

## 3. Operational readiness -- needs a real deployment target

- Persistent storage for policies and approval state (audit logs and
  quarantine state are now persisted -- JSON Lines and SQLite
  respectively, both survive a restart). Pending approvals are still an
  in-process dict, and there isn't a meaningful way to persist a
  *blocked HTTP request* across a restart -- that one needs a different
  design (e.g. a durable queue plus a way to re-notify), not just a
  different storage backend.
- Authentication now exists (opt-in `AGENTGUARD_SERVER_API_KEY`, gates
  `/tools/*`, `/approvals/*`, `/audit`) but it's a single shared key, not
  per-identity **authorization** -- every holder of the key can call any
  tool and resolve any approval. A real deployment still needs to
  distinguish "the agent" from "the human clicking approve," and needs
  per-connector/per-tenant scoping, not one key for everything.
- Multi-tenancy: today one process serves one connector with one
  hardcoded policy file.
- Horizontal scaling: `QueueApprover`'s pending-approval state lives in
  one process's memory; a second replica wouldn't see the first's
  pending approvals.
- A production WSGI server (gunicorn/uwsgi) and reverse proxy in front
  of Flask's dev server, with real request timeouts, not just
  `QueueApprover`'s internal timeout.
- Secrets management for whatever real connector credentials this
  eventually holds (this repo currently holds zero real credentials by
  design).
- Monitoring, alerting, and on-call for the guard itself -- if
  AgentGuard is down or wedged, does the agent it's supposed to be
  gating fail open or fail closed? That needs to be a deliberate,
  tested decision, not a side effect of whatever happens to occur when
  the process dies. (Today: if the process is down, nothing gated by it
  can run at all -- fail-closed by accident, not by verified design.)
- Load testing, so "handles real traffic" is a measurement, not an
  assumption.

## 4. Compliance & legal -- not started, not a coding task

- Terms of service, privacy policy, data processing agreement -- needed
  the moment this handles anyone's real account data.
- Depending on target customers: SOC 2, ISO 27001, or similar
  attestations enterprises typically require before connecting a
  security tool to real infrastructure.
- Legal review of the liability story: if AgentGuard fails to catch
  something it claimed to catch, what's the actual exposure? An MIT
  license disclaims warranty, which is right for open source, but a
  *product* with paying customers usually needs more than a license
  file to answer that question.

## 5. Market validation -- not started, not a coding task

"Piyasada talep görecek" (will see market demand) is a claim about the
world, not about this codebase. It requires:

- Real users connecting real (or realistic staging) agent deployments
  and reporting whether this actually solves a problem they have.
- Competitive positioning against whatever agent-security tooling
  already exists or is emerging in this fast-moving space -- this
  repo hasn't done that research.
- Pricing, packaging, and a go-to-market motion, if the goal is a
  commercial product rather than an open-source project.

None of this can be generated in a coding session. It requires shipping
something real to real people and listening.

## What this session actually did

Took a demo-quality proof-of-concept and closed the *engineering
maturity* gap as far as a single sandboxed session reasonably can:
real package, real config loading, real structured audit logging, a
real (tested, concurrent) approval flow, a real reference HTTP
deployment, a 48-test stdlib suite, a clean `ruff`/`mypy` pass, and
an MCP integration sketch checked line-by-line against the real,
official SDK source (cloned from GitHub, since PyPI/Docker Hub are
both network-blocked here but GitHub isn't) -- a check that actually
caught the sketch using a deprecated v1 API and got it corrected to
the current one. Honestly-documented limits remain on the Docker
piece (never built, no registry access) and on the MCP piece (read-
verified against real source, but never executed against a live MCP
client). It did not, and could not, close the security-assurance,
compliance, or market-validation gaps -- those need real time from
real people outside this conversation.

If the goal is an actual product, the next concrete step is picking ONE
item from section 2 (independent security review) or section 5 (get it
in front of a handful of real users) -- not more solo engineering.

## What the 0.3.0 session added

This session had real network access the 0.2.0 session didn't, so it
closed two items this document previously listed as blocked by that:
`agentguard/mcp_adapter.py`'s wiring sketch was executed against a real,
installed `mcp==2.0.0` SDK (Docker remained blocked, for a different
reason -- Docker itself isn't installed here). It then did an internal
security review of `server/app.py`, `agentguard/approval.py`, and
`agentguard/policy.py`, found and fixed 4 real issues (see CHANGELOG
0.3.0), and closed three gaps this document had named directly: a Slack
notification channel (`SlackNotifyApprover`, verified against a real
Slack workspace), persistent quarantine state (`SqliteQuarantineStore`,
verified across real process-restart-equivalent test scenarios, plus a
CLI to inspect/clear it), and opt-in API key auth on every non-`/healthz`
endpoint (closing the "anyone who can reach the port" line this document
used to have verbatim -- though a single shared key is still not
per-identity authorization, see section 3).

It also added opt-in API key auth on `server/app.py` (single shared
key, `hmac.compare_digest`-checked, `/healthz` deliberately exempt), and
a `pip-audit` job in CI -- while fixing this document itself, which had
been claiming CI didn't run lint/type-check at all; it already did (a
separate `lint` job), this file was just wrong about it. That's now
corrected rather than left to compound.

This still did not, and could not, close the *independent* security
review, compliance, or market-validation gaps -- an AI (even a careful
one, even one that caught its own mocking bug and fixed it) reviewing
code in the same session it was written in is not independent review,
and no amount of additional solo engineering changes that. The next
concrete step named above hasn't changed.
