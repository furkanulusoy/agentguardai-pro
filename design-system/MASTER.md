# AgentGuard Design System — MASTER

This is the single source of truth for `apps/web`'s visual language. It
is a **documentation file only** — it does not affect the backend, the
API, or any data model. If a rule here and the running code ever
disagree, the code is the bug.

Product identity: **AI Agent Governance / Action Guardrails**, not a
SOC / threat-intelligence product. There is no threat feed, no IOC
database, no MITRE ATT&CK mapping in this product — inventing that
vocabulary in the UI would show data that doesn't exist. Every visual
element maps to a real field in `apps/api`'s response models
(`apps/web/src/types.ts`). See "Data honesty" below.

## Data honesty (non-negotiable)

- Every node, edge, metric, and label in `ActionGraph`/`ActionFlow`
  must trace back to a real field already returned by `/connectors`,
  `/approvals`, or `/tenant/members`. No invented "Agent" entity
  distinct from `requested_by_email` (a real `User`). No fabricated
  threat/vulnerability/IOC data.
- Aggregates (counts, distributions) are computed client-side from the
  real fetched list -- that's honest derivation, not fabrication.
- If a visual concept needs a field the API doesn't return, that's a
  signal to either drop the concept or ask before inventing an
  endpoint -- never fake it in the UI.

## Color -- semantic, not decorative

Neutral base (slate), four semantic accents used *only* for their
meaning, never as background decoration:

| Token | Value | Meaning |
|---|---|---|
| `--c-neutral-*` | slate 50/100/.../900 | structure, text, borders |
| `--c-info` | `#2563eb` (blue-600) | LOW risk, informational |
| `--c-warning` | `#d97706` (amber-600) | MEDIUM risk, needs attention |
| `--c-critical` | `#dc2626` (red-600) | HIGH/CRITICAL risk, denied |
| `--c-healthy` | `#059669` (emerald-600) | APPROVED, connected, healthy |

Mapping to real data:
- `risk_level`: LOW→info, MEDIUM→warning, HIGH/CRITICAL→critical
- `status`: PENDING→warning (attention), APPROVED→healthy,
  DENIED/EXPIRED→neutral-muted (a closed matter, not an active alarm)

No gradients, no glow, no glassmorphism as default surface treatment.
A card is a solid surface with a border and one soft shadow. Color
appears where it means something (a risk badge, a status dot, a
selected node's ring) and nowhere else.

## Typography

Plus Jakarta Sans throughout (already loaded). Scale is about
hierarchy, not size for its own sake:

- Page title: 28px / extrabold / tight tracking
- Section label: 13px / semibold / uppercase / wide tracking / muted
- Stat / risk number: 32-40px / bold, tabular-nums
- Body: 14px / medium
- Metadata (timestamps, emails): 12-13px / regular / muted

## Motion

Purpose-only. Every transition answers "what changed and why should
the eye follow it":

- Hover/selection: 150-200ms, `transform`/`opacity` only (GPU-cheap)
- PENDING attention: one slow (2s) opacity pulse on the status dot,
  never on full surfaces, never strobing
- Panel open/close: 200ms ease, slight translate + fade, no bounce
- Respects `prefers-reduced-motion` globally (see `src/index.css`)

## Components

- `Card` — solid white surface, `rounded-xl`, one border, one shadow.
  No blur, no transparency.
- `Badge` — semantic-color pill, used for `risk_level` and `status`.
- `ActionFlow` — 5-stage horizontal state machine for one
  `ApprovalRequest`, entirely driven by its real `status`/`error`.
- `ActionGraph` — SVG graph of real `User`/`Connector`/`ApprovalRequest`
  nodes and the real edges between them (`requested_by_email`,
  `credential_id`/`connector_type`).
- `ApprovalDetailPanel` — real detail + the real
  `POST /approvals/{id}/resolve` action, with loading/success/error
  states.

## What "premium" means here

Typography + spacing + hierarchy + motion + honest data visualization
+ restraint. Not: gradients, neon, glow, glassmorphism, decorative
shadows layered for their own sake.
