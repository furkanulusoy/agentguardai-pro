# Threat model & responsible-use notes

## What this project is

AgentGuard demonstrates three concrete failure modes that show up once you
connect a personal account (inbox, code host, anything with an API) to an
autonomous or semi-autonomous AI agent:

1. **Over-broad permissions** — the agent (or the connector it uses) is
   granted more access than the task in front of it actually needs.
2. **Unapproved autonomous action** — the agent takes an irreversible action
   (delete, merge, send, pay) on its own, with no human checkpoint.
3. **Malicious / compromised connector** — a third-party integration does
   more than it claims to, because nothing at the boundary checks that its
   declared scope matches its actual behavior.
4. **Prompt injection** — content the agent reads (an email, a document, a
   web page) contains instructions that hijack what the agent does next,
   using capabilities the agent was legitimately given for something else.

Scenario D (prompt injection) has its own honesty caveat worth repeating
here: the deterministic `demo/` scenario has no LLM in the loop, so there's
no real prompt to inject. The scripted "agent" in that scenario deterministically simulates
the *outcome* of a successful injection — it always complies with an
embedded directive if one is present — because that's the realistic worst
case a guardrail needs to survive, not because it proves anything about
how easy real injection is against a real model.

For each one, the safe `demo/` suite shows the failure happening, then shows
the same scenario with the guardrail engine (`agentguard/`) in front of it.
The separate platform layer under `apps/` and `connectors/` can connect to
real Gmail, GitHub, and Slack accounts when an operator explicitly configures
OAuth credentials. The Ollama/NVIDIA example under `sdk/python/examples/`
routes model-proposed tool calls through that platform enforcement point.

## What this project deliberately is NOT

- **The demo suite never uses real accounts.** Every connector used by
  `demo/` lives in `mock_services/`, uses synthetic data, and never leaves the
  process. The production-oriented platform connectors are real integrations;
  their use requires explicit OAuth setup and must be tested only against
  accounts and resources the operator controls.
- **No exploit against a real product.** This isn't a vulnerability report
  against GitHub, Gmail, or any MCP implementation. It's a demonstration of
  a *pattern* of risk that applies broadly to agent-connector architectures,
  built entirely on synthetic services so it's safe to run, fork, and
  publish.
- **Not a claim that AgentGuard catches everything.** See the honest
  limitation below.

## Why the demo uses simulated services

An earlier version of this idea considered demonstrating these risks
against real, personally-owned accounts. We deliberately moved away from
that: even on an account you own, letting an agent's mistake or a
connector's bad behavior actually delete real messages, merge into a real
repo, or trigger a real irreversible action is a real cost for a
demonstration that doesn't need to pay it. A faithful simulation makes the
same point, is reproducible in CI, and is safe for anyone to clone and run.

## Honest limitation: what AgentGuard does *not* catch

Scenario C's integrity check works by comparing what the agent was
authorized to do against what actually happened to the *monitored
resource* — so an undeclared **write** (like the hidden delete) is caught
immediately, because it's an action nobody authorized.

It does **not** retroactively protect data the connector had legitimate,
in-scope **read** access to. If a connector's job is to read your inbox and
it quietly forwards what it reads somewhere else, that forwarding happens
inside the read call — there's no "extra action on the monitored resource"
for AgentGuard to notice. That's why least privilege and vetting which
connectors you actually trust still matter; a guardrail narrows the blast
radius of what goes wrong, it doesn't replace judgment about what you
connect in the first place.

## Responsible use

If you extend this project to test against real services, keep it to
accounts you own and control, avoid destructive/irreversible actions
against anything you can't afford to lose, and don't target third-party
systems or accounts without explicit authorization. This repo exists to
raise awareness and to prototype defenses — not as a how-to for accessing
anyone else's account.
