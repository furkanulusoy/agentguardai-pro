# Changelog

All notable changes to this project are documented here. Versioning
follows [SemVer](https://semver.org/); this project stays below `1.0.0`
until the gaps in `docs/ROADMAP_TO_PRODUCTION.md` close enough to
honestly call it stable.

## [0.4.0] - 2026-09-15

### Added

- FastAPI/PostgreSQL governance platform, React dashboard, durable recovery worker, MCP server, and Python/TypeScript SDKs.
- Tenant- and agent-scoped connector grants, deterministic policy decisions, payload-bound approvals, and strict Gmail/GitHub/Slack action schemas.
- Durable idempotent operation lifecycle with explicit `UNKNOWN` handling and authorization re-check before provider execution.
- HttpOnly refresh-cookie flow, rotation/revocation, encrypted connector credentials, audit redaction, and encrypted sensitive execution details.
- Docker-based localhost product profile at `http://localhost:5000` with generated local secret files outside Git.
- Real PostgreSQL integration coverage and frontend/SDK/CI validation. The release verification records 299 passing Python tests with zero failures, errors, or skips.

### Security

- Closed cross-agent connector and approval visibility paths.
- Added transaction-safe approval resolution and execution claiming to reduce duplicate provider actions.
- Kept authorization and policy enforcement in the platform governance service; SDK and MCP layers remain thin clients.

### Known limitations

- This is a pilot release. SSO/SCIM, managed KMS/Vault, WORM audit export, retention automation, distributed rate limiting, provider reconciliation, and HA deployment remain roadmap work.
- Provider APIs cannot offer a universal exactly-once guarantee. Ambiguous outcomes stop as `UNKNOWN` and require reconciliation.

The older entries below preserve the implementation history. Statements about then-current test counts or deployment status are historical and are superseded by this release and `docs/RELEASE_NOTES.md`.

## Platform (unreleased -- SaaS migration in progress, see docs/ROADMAP_TO_PRODUCTION.md)

This track is separate from the `agentguard` library's own SemVer above:
`apps/`, `infrastructure/`, and (later) `core/`/`connectors/` are a new,
additive SaaS platform layer being built *around* the existing library,
not a new version of it. Decision, made explicit: FastAPI for the new
API layer, gradual migration (Flask's `server/app.py` stays exactly as
it is, untouched, as the reference implementation), same repository
(monorepo), core business logic to be kept framework-independent when
it's extracted from `agentguard/` into `core/` in a later phase.

### Phase 1, Step 1 -- FastAPI + real PostgreSQL vertical slice
- `docker-compose.yml` -- `postgres:16`, named volume, healthcheck.
  `.env.example` (committed, placeholders) / `.env` (gitignored, real
  local dev values, random-generated password).
- `infrastructure/config.py` -- typed `Settings` (pydantic-settings),
  reads `DATABASE_URL` from `.env`; nothing hard-coded, missing values
  fail at startup instead of silently defaulting.
- `infrastructure/database/session.py` -- async SQLAlchemy engine
  (asyncpg driver) + `check_database_connection()`, a real query
  (`SELECT 1`) against the real database, not an assumption.
- `apps/api/main.py` -- minimal FastAPI app, `GET /health` reports
  `database: connected` or `unreachable` based on that real check.
- New optional extra `agentguard[api]` in `pyproject.toml` (fastapi,
  uvicorn, sqlalchemy[asyncio], asyncpg, pydantic-settings) -- separate
  from the existing `server` extra (Flask), which is untouched.
- `pyproject.toml`: added the `pydantic.mypy` plugin -- `BaseSettings`
  subclasses look like they're missing constructor args to plain mypy
  (they're populated from the environment at runtime); the official
  plugin understands this instead of needing a scoped `type: ignore`.

**Verified, not asserted:** real `docker compose up -d` brought up
Postgres 16 (container healthy); a real host-to-container query
(`psycopg`) succeeded before any app code was written; `uvicorn
apps.api.main:app` really started and `GET /health` returned
`{"status":"ok","database":"connected"}` against the real container;
stopping the container made the same endpoint return `{"status":
"degraded","database":"unreachable"}`, and it recovered when the
container came back -- proving the check is real, not hard-coded to
always report healthy. All 75 existing `agentguard` tests still pass
unchanged; `server/app.py` (Flask) was not modified.

### Phase 1, Step 2 -- Tenant/User/RBAC schema
- `infrastructure/database/models/` -- `Tenant`, `User`, `Role`,
  `Permission`, plus `role_permissions`/`user_roles` association tables.
  UUID primary keys throughout (sequential integer IDs make BOLA/IDOR
  probing trivial; UUIDs don't). `User.email` is unique **per tenant**
  (`UniqueConstraint(tenant_id, email)`), not globally -- two tenants can
  each have their own "admin@company.com". `Role.tenant_id` is nullable
  (NULL = a system role available to every tenant); a partial unique
  index enforces one system role per name despite Postgres treating
  NULL != NULL in ordinary unique constraints.
- `infrastructure/auth/passwords.py` -- Argon2 (`argon2-cffi`) password
  hashing. Not passlib: unmaintained relative to current Argon2
  guidance; argon2-cffi is maintained by the reference implementers.
- Alembic wired up (`alembic/`, `alembic.ini`): `DATABASE_URL` from
  `.env` is the single source of truth (not duplicated in `alembic.ini`);
  Alembic's sync engine needed an explicit `+psycopg` dialect (a bare
  `postgresql://` URL defaults to psycopg2, which isn't installed here
  -- this project uses psycopg v3 for migrations, asyncpg for the app).
  Two real migrations: schema (6 tables), then a data migration seeding
  5 system roles (OWNER/ADMIN/APPROVER/OPERATOR/VIEWER) against 11
  permission codes with a least-privilege mapping (VIEWER: read-only;
  each higher role is a proper superset).
- `alembic/versions/` excluded from `ruff` (`pyproject.toml`) --
  Alembic-generated migrations are a frozen historical record edited
  once, not restyled on every lint run; Alembic's own template doesn't
  match this project's `Union[]`-vs-`X|Y`/import-order rules anyway.

**Verified, not asserted:** both migrations applied to the real
container (`alembic upgrade head`); `\dt`/`\d users` in a real `psql`
session confirmed all 6 tables, the composite unique constraint, and
the cascading FK; a SQL query against the real seeded data confirmed
the exact expected permission counts per role (VIEWER 5, APPROVER 6,
OPERATOR 7, ADMIN 9, OWNER 11) and that OWNER's 11 permissions are all
11 defined codes. A full async-SQLAlchemy round trip (not raw SQL)
created a real tenant + user + role assignment, queried it back with
relationships eager-loaded, and specifically proved the per-tenant
email uniqueness design: the same email in two different tenants
succeeded, the same email twice in one tenant raised `IntegrityError`.
All 75 existing tests still pass; `mypy`/`ruff` clean across old and
new code (fixed two real issues found along the way: `Mapped["X"]`
string forward-references need a `TYPE_CHECKING`-guarded import to
resolve for mypy even though SQLAlchemy resolves them fine at runtime;
Postgres's NULL-handling in unique constraints needed a partial index,
not a plain composite `UniqueConstraint`).

### Phase 1, Step 3 -- real authentication (register/login/refresh/logout/me)
- **Design correction (transparent, not silently patched):** `User.email`
  was per-tenant-unique in Step 2. Building login exposed why that's
  wrong for this model: login identifies a user by email alone (no
  separate tenant-slug field), and this schema gives each user exactly
  one tenant (no cross-tenant membership table) -- so per-tenant
  uniqueness only created an ambiguous login for no real benefit. Fixed
  via a new migration (drop the composite constraint, add a plain unique
  index on `email`), not by editing the Step 2 changelog entry.
- `infrastructure/database/models/refresh_token.py` -- `RefreshToken`,
  stored **hashed** (sha256 -- high-entropy random value, not a
  low-entropy secret, so no need for Argon2's deliberate slowness here).
  `revoked_at` supports both rotation and logout/revocation.
- `infrastructure/auth/jwt.py` -- access tokens are real signed JWTs
  (HS256, `JWT_SECRET_KEY` from `.env`), stateless and short-lived
  (30 min default). Refresh tokens are deliberately NOT JWTs -- opaque
  random strings (`secrets.token_urlsafe(48)`), because rotation and
  revocation both require a database row to act on, which a
  signed-but-unrevokable JWT can't give you. Every access token carries
  a `jti` (RFC 7519) so two tokens issued in the same second aren't
  byte-for-byte identical (found via a failing test -- see below).
- `apps/api/routers/auth.py` -- `POST /auth/register` (creates a new
  Tenant + its first User as OWNER -- no invite system yet, so this is
  the only way a tenant currently comes into existence), `POST
  /auth/login`, `POST /auth/refresh` (rotates: the presented token is
  revoked and a new pair issued), `POST /auth/logout` (revokes), `GET
  /auth/me` (the first real protected endpoint). Login and registration
  return the same generic "Invalid email or password" for a wrong
  password and a nonexistent email -- a caller must not be able to tell
  the two apart.
- `apps/api/dependencies.py` -- `get_current_user`, the dependency every
  future protected route will use. Deliberately eager-loads `roles`
  (`selectinload`): SQLAlchemy's async ORM does not support implicit
  lazy-loading outside of `awaitable_attrs`, so reading
  `current_user.roles` on an unloaded relationship would raise
  `MissingGreenlet`, not quietly issue an extra query.
- Switched every route/dependency from `param: T = Depends(x)` to
  `Annotated[T, Depends(x)]` (`BearerCredentials`, `DbSession`,
  `CurrentUser` type aliases) -- not just style: plain `= Depends(...)`
  in a default argument is flagged by `ruff`'s bugbear rule B008 (a real
  rule protecting against a real Python footgun in general, false
  positive for FastAPI specifically, since FastAPI re-evaluates it per
  request); `Annotated` is FastAPI's own current recommended style and
  sidesteps the false positive entirely instead of needing a `noqa` on
  every route.
- New deps in the `api` extra: `pyjwt`, `email-validator` (`EmailStr`
  needs this installed explicitly, it's not bundled with pydantic).

**Verified, not asserted:** a real running server (`uvicorn`, real
Postgres) exercised through 16 real HTTP checks via `httpx`: register,
duplicate-email rejection (409), wrong-password rejection (401),
login, `/me` with no token (401) and with a valid token (200, correct
email/roles), refresh issuing a genuinely different token pair, **the
old refresh token rejected after rotation** (401 -- proves rotation
isn't just issuing extra tokens without invalidating the old one),
the new access token working, logout (204), refresh-after-logout
rejected (401), and a garbage bearer token rejected (401). One real
bug found and fixed by this test, not by inspection: the first version
of the "new access token differs" check failed, because two tokens
issued within the same wall-clock second have identical claims and JWT
encoding is deterministic -- not a security bug (short-lived, stateless
tokens), but the `jti` fix above is the correct, standard remedy rather
than loosening the test. All 75 pre-existing tests still pass; `ruff`/
`mypy` clean across old and new code.

### Phase 1, Step 4 -- RBAC wired to a real endpoint
- `apps/api/dependencies.py`: `require_permission(code)`, a dependency
  factory that 403s unless `current_user` holds a role granting `code`.
  Deny-by-default -- a user with zero matching permissions is rejected,
  there's no implicit "everyone can" fallback. This is the same
  enforcement-point principle `agentguard/guardrail.py` already applies
  to tool calls (deterministic policy engine decides, never the
  agent/LLM itself) -- RBAC is that same idea one layer up, for the
  platform's own HTTP API. `get_current_user`'s query was extended to
  nested-eager-load `Role.permissions` (`selectinload(...).selectinload
  (...)`) for the same MissingGreenlet reason `roles` alone was eager
  -loaded in Step 3.
- `apps/api/routers/tenant.py` -- `GET /tenant/members`, gated by
  `require_permission("tenant.admin")`. First real permission-gated
  endpoint (exists specifically to prove the mechanism blocks, not just
  to be useful): lists every user in the caller's own tenant.

**Verified, not asserted:** registered a real OWNER (has `tenant.admin`
via the Step 2 seed data) -- `GET /tenant/members` returned 200. Created
a second user directly via the ORM (no invite endpoint exists yet) with
the VIEWER role (does not have `tenant.admin`), logged them in through
the real `/auth/login` endpoint, and called the same endpoint with
their real access token: **403**, with the response body actually
naming the missing permission (`tenant.admin`). Confirmed the 401-
before-403 ordering (no token still fails auth, not authorization,
first). All 75 pre-existing tests still pass; `ruff`/`mypy` clean.

### Full-project review pass -- 2 real security bugs, 1 structural gap closed
Requested explicitly: re-read every file in Steps 1-4 fresh, not just
the parts touched most recently.

**Fixed:**
- **Timing side-channel in `POST /auth/login`.** `user is None or ...
  or not verify_password(...)` short-circuits on `or` -- for a
  nonexistent email, `verify_password` (deliberately slow, Argon2)
  was never called at all, making "no such account" measurably faster
  to reject than "wrong password for a real account." The response
  *body* was already identical for both (by design), but timing alone
  is enough to enumerate valid emails. Fixed by always calling
  `verify_password` against a fixed dummy hash when no user is found,
  so a rejected login always pays the same cost regardless of which
  reason it failed for.
- **Unhandled `IntegrityError` race in `POST /auth/register`.** The
  "is this email already registered" check and the actual `INSERT` are
  two separate round trips -- two concurrent registrations for the
  same email can both pass the check before either commits. The loser
  used to get an unhandled 500 (only the *tenant slug* flush was
  wrapped in `try/except IntegrityError`, not the user flush that
  follows it). Now both are, with the same clean `409 Email already
  registered` either way.
- `.gitignore` had a stale `postgres_data/` entry left over from an
  earlier draft that used a bind mount; `docker-compose.yml` actually
  uses a named Docker volume, which never creates that local directory.
  Removed rather than left as harmless-but-wrong.

**Added -- the most significant gap found, not a bug fix:** every
verification of Steps 1-4 so far was a throwaway script, deleted after
each manual run. None of it was a permanent, re-runnable regression
test -- a real product's auth/RBAC layer having zero automated test
coverage is a genuine gap, not a nice-to-have. Added:
- `tests/test_infra_auth_unit.py` -- pure unit tests (password hashing,
  JWT encode/decode/tamper-detection, refresh token generation), no
  database, always runnable.
- `tests/test_infra_api.py` -- the full HTTP flow from Steps 3-4
  (register, login, refresh rotation + replay rejection, logout,
  `/me`, RBAC allow/deny) via `httpx.ASGITransport` against `apps/api`
  in-process, exercised against the real local PostgreSQL. Skips
  (doesn't fail) if the `api` extras aren't installed or Postgres isn't
  reachable, matching how `tests/test_server.py` already skips when
  Flask isn't installed.
- **A real bug the new tests caught immediately:**
  `unittest.IsolatedAsyncioTestCase` gives every test method its own
  event loop; `infrastructure/database/session.py`'s `engine` is a
  module-level singleton (correct for the real app, which has one loop
  for its whole lifetime). asyncpg connections aren't safe to reuse
  across event loops, so the second test onward crashed deep inside
  asyncpg's socket layer (`AttributeError` on a `None` proactor socket)
  the moment it touched a pooled connection left over from the previous
  test's now-closed loop. Fixed by disposing the engine's pool at the
  end of every test (and after the module-level reachability check) --
  a testing-harness fix, not a production code change.
- `.github/workflows/tests.yml`: the `test` job now provisions a real
  `postgres:16` service and runs `alembic upgrade head` before the test
  suite -- without this, the new tests would have silently self-skipped
  in CI forever (DB unreachable), which would have meant `infrastructure`/
  `apps` had a permanent test suite that never actually ran anywhere.
  Also: the `lint` job's `mypy` step only ever checked `agentguard/` --
  `infrastructure`/`apps` were never type-checked in CI at all, despite
  being checked manually every step of this session. Added a second
  `mypy infrastructure apps` step.

**Verified, not asserted:** 103 tests (75 original + 28 new) pass
together against the real database; `ruff check .` and `mypy` (both
`agentguard` and `infrastructure`/`apps`) clean; confirmed the database
is left with zero leftover rows after a full test run (teardown
cascade-deletes work); CI workflow YAML re-parsed to confirm the
`postgres` service block is syntactically valid.

**Assessment of the "are we heading toward a real product correctly"
question this review was asked to answer:** yes, with the gap above
now closed. The concerning finding wasn't a bug in the auth logic
itself (which held up) -- it was that a growing, security-critical
subsystem had been verified by hand every time instead of automatically
every time, which is exactly the kind of gap that stops mattering right
up until someone changes something and nothing catches it.

### Phase 2 -- Connector Framework (abstraction only, no real connector yet)
Deliberately stops short of Gmail/GitHub/etc. -- those need real OAuth
app registration with each provider, a separate, bigger step. This
step proves the *shape* works against the real, unmodified core engine
before any real external service is involved.

- **Architecture decision, stated explicitly rather than picked
  silently:** `connectors/base.py`'s `BaseConnector` is synchronous.
  `agentguard.guardrail.AgentGuard.call()` invokes
  `getattr(connector, action)(*args, **kwargs)` synchronously, and 103
  tests depend on that -- not rewriting it. A connector instance IS the
  `connector` object `AgentGuard` already wraps; its action methods
  (`read_message`, `send_email`, ...) are exactly the action names
  `Policy.task_scope`/`sensitive_actions` already govern. There is no
  separate "capability" concept layered on top. When called from the
  async `apps/api` layer, the route bridges with a threadpool
  (`starlette.concurrency.run_in_threadpool` / `asyncio.to_thread`) --
  that's the API route's job, not the connector's.
- **Deviation from the original spec, noted rather than silently
  resolved:** the spec listed `read()/create()/update()/delete()` as
  suggested method names, then separately said each connector should
  "only expose the capabilities it supports" -- those two instructions
  conflict (what would an SMS connector's `update()` even mean?). Only
  `authenticate/connect/disconnect/validate` are abstract/required
  (every real connector needs a credential lifecycle); each concrete
  connector defines its own meaningfully-named action methods instead
  of stubbing unsupported generic ones.
- `infrastructure/secrets/store.py` -- `SecretStore` Protocol +
  `FernetSecretStore`, real symmetric encryption (`cryptography`'s
  Fernet: AES-128-CBC + HMAC, authenticated) keyed from
  `SECRET_ENCRYPTION_KEY` in `.env`. A real, working implementation for
  a single-instance deployment -- explicitly not the end state; a
  production multi-instance deployment should swap this for a cloud
  KMS/Vault-backed implementation behind the same `Protocol`, and
  nothing above this layer needs to change to make that swap.
- `infrastructure/database/models/credential.py` -- `Credential` model,
  `encrypted_secret` always written/read through `SecretStore`, never
  plaintext. `user_id` is nullable with `ON DELETE SET NULL` (not
  `CASCADE`) -- some connectors (e.g. a Slack workspace bot token) are
  tenant-wide, not tied to whoever happened to connect them; deleting
  that person must not silently break a shared integration.

**Verified, not asserted:** a real (test-only) `ExampleConnector`
wrapped by the real, unmodified `AgentGuard`/`Policy` -- an in-scope
action succeeds through `guard.call()`, an action that exists on the
connector but was never granted in `task_scope` is genuinely blocked
(`PermissionDenied`) even though the method is technically callable,
and a sensitive action genuinely requires approval (`ApprovalDenied`
when the approval callback returns `False`) -- proving the framework
composes with the existing enforcement engine, not just that it has
the right method names. Separately: encrypted a real secret, stored a
`Credential` row in the real database, fetched it back via a *separate*
query, decrypted it correctly -- and independently read the raw column
via a raw SQL query (bypassing the ORM entirely) to confirm the
plaintext genuinely never touches the database; confirmed a wrong
encryption key cannot decrypt it. New tests: `tests/test_infra_connectors.py`
(8 tests). 111 tests total now pass; `ruff`/`mypy` (including the new
`connectors/` package) clean; database left with zero leftover rows.

### Phase 3 -- Gmail connector, real OAuth, real inbox (first real connector)
The first connector against a real external service, using a real
Google Cloud OAuth app and a disposable test Gmail account created
specifically for this -- not a personal/production account, matching
how the Slack integration was tested earlier in this project.

- `connectors/gmail/connector.py` -- `GmailConnector(BaseConnector)`,
  `gmail.readonly` scope only for this first pass (`list_messages`,
  `read_message`) -- compose/send are a real, separate risk step up,
  deliberately not built yet.
- `connectors/gmail/oauth.py` -- authorization-code flow via
  `google-auth-oauthlib`'s `Flow` (Google's own recommended approach,
  not hand-rolled).
- `apps/api/routers/connectors/gmail.py` -- `GET /connectors/gmail/authorize`
  (protected by `CurrentUser`, returns the Google consent URL) and
  `GET /connectors/gmail/callback` (NOT protected by our auth -- Google
  calls it directly and can't carry a bearer token; `state`, a signed
  JWT, is the only proof of which of our users this belongs to).
- `infrastructure/auth/jwt.py` -- `create_oauth_state_token`/
  `decode_oauth_state_token`, reusing the existing JWT infrastructure
  for a short-lived (10 min), signed OAuth `state` value.

**A real bug, found only by actually running the flow against real
Google infrastructure, not by inspection:** the first version failed
every token exchange with `(invalid_grant) Missing code verifier`.
Root cause: `/authorize` and `/callback` are two separate HTTP requests,
each building its own `google_auth_oauthlib.flow.Flow` object;
`Flow.authorization_url()` auto-generates a PKCE `code_verifier` and
keeps it as instance state, which the *second* Flow object (built fresh
in `/callback`) never had. Fixed by generating the verifier explicitly
and embedding it in the signed `state` token, so the same value reaches
both halves of the flow without needing separate server-side session
storage. Two more real issues surfaced and fixed along the way, both
by actually running the flow rather than by reasoning about it:
Google's OAuth Consent Screen UI was replaced by "Google Auth Platform"
(Branding/Audience/Clients tabs) at some point before this session,
which is why the first attempt hit a generic Google-side 500 rather
than a specific error -- and separately, the test account genuinely
had to be added to the "Audience -> Test users" list before Google
would even show the consent screen instead of a 403.

**Verified, not asserted, against real infrastructure end to end:**
registered a real platform user, called the real `/authorize` endpoint,
completed Google's actual OAuth consent screen with a real (disposable,
purpose-created) Gmail account, hit the real `/callback`, and confirmed
a `Credential` row was created. Then, separately: decrypted that real
credential, built a real `GmailConnector`, wrapped it with the
**unmodified** `agentguard.guardrail.AgentGuard`/`Policy` -- the same
engine 111 tests already depend on -- and called `guard.call
("list_messages")`, which returned 5 real message IDs from the real
inbox; `guard.call("read_message", ...)` on the first one returned its
real `From`/`Subject`/`Date` headers. Confirmed AgentGuard's scope
enforcement still applies to a real (not mock/example) connector: a
`delete_message` call outside `task_scope` was rejected with
`PermissionDenied` before ever reaching the connector. This dev
database's `Credential`/`Tenant`/`User` rows from this run are left in
place on purpose (a real connection, not test pollution) -- unlike the
automated test suites, which clean up after themselves.

Real-world security review pass (independent internal review, not a
substitute for the third-party audit `docs/ROADMAP_TO_PRODUCTION.md`
still lists as needed) plus two real gaps this repo's own roadmap had
flagged as open: nobody gets notified when an approval is pending, and
quarantine resets on restart.

### Added
- `agentguard.approval.SlackNotifyApprover` -- wraps another approval
  callback (typically `QueueApprover`) and posts a one-way notification
  to a Slack Incoming Webhook whenever a sensitive action needs approval.
  Approval itself still happens through the existing `/approvals/<id>`
  API, not a Slack button (an interactive button needs a public HTTPS
  endpoint plus Slack request-signature verification -- a materially
  bigger and more security-sensitive piece of surface, deliberately not
  built yet). Wired into `server/app.py` via an optional
  `slack_webhook_url` parameter / `AGENTGUARD_SLACK_WEBHOOK_URL` env var;
  verified against a real Slack workspace, not just mocked tests.
- `agentguard.quarantine_store.SqliteQuarantineStore` -- persists
  connector quarantine state (stdlib `sqlite3`, no new dependency). A
  connector that AgentGuard quarantines for an undeclared side effect now
  stays quarantined across a process restart, until a human explicitly
  clears it -- closes the gap `docs/ROADMAP_TO_PRODUCTION.md` called
  "fail-closed by accident, not by verified design." `AgentGuard` gained
  an optional `quarantine_store` parameter (restores prior quarantine on
  construction, persists new quarantines as they happen); `server/app.py`
  wires this up via `quarantine_db_path`.
- `agentguard quarantine list <db>` / `agentguard quarantine clear <db>
  <connector>` -- CLI commands to inspect and lift a persisted quarantine.
- Opt-in API key auth on `server/app.py`: set `AGENTGUARD_SERVER_API_KEY`
  (or pass `api_key=` to `create_app()`) to require an
  `Authorization: Bearer <key>` header on `/tools/*`, `/approvals/*`, and
  `/audit`, checked with `hmac.compare_digest` (constant-time, not `==`).
  `/healthz` is deliberately never gated, so monitoring/load-balancer
  probes keep working. No key configured -- the default -- behaves
  exactly as before. Closes the `docs/ROADMAP_TO_PRODUCTION.md` line
  "today anyone who can reach the port can call any tool or resolve any
  approval," though it's a single shared key, not per-identity auth or
  authorization scopes -- a real deployment still needs that.
- `security` job in `.github/workflows/tests.yml` -- runs `pip-audit`
  against the real installed dependencies on every push/PR. Upgrades
  `pip`/`setuptools` first: verified locally that without this, `pip
  -audit` reports 13 CVEs against those two alone (build tooling, not a
  declared dependency of this project -- `dependencies = []` in
  `pyproject.toml`); with it, the real dependencies (Flask, PyYAML,
  click) come back clean.
- Test suite grew from 48 to 75 tests.

### Fixed (found during this review, all in `server/app.py` unless noted)
- The reference server never wired up `monitored_resource` at all --
  `demo/scenario_c_malicious_connector.py` was the *only* place
  connector-integrity checking was ever exercised. The one deployment
  shape meant to represent "real" usage was silently missing one of
  AgentGuard's three advertised defenses. Now wired up via `CallRecorder`.
- `POST /tools/<action>` didn't validate the shape of `args`/`kwargs`
  before splatting them into `guard.call()` -- a malformed body (e.g.
  `{"kwargs": "not-a-dict"}`) crashed into an unhandled `TypeError`
  instead of a clean `400`. Reproduced against a real Flask test client
  before and after the fix.
- `agentguard.approval.QueueApprover.resolve()` wasn't idempotent -- a
  duplicate/replayed `resolve()` call for the same approval id could
  overwrite a human's decision (e.g. flip an already-denied action to
  approved) in a narrow race window before `AgentGuard.call()` consumed
  the result. Now the first `resolve()` for an id wins; later ones are
  ignored.
- `agentguard.policy.Policy.from_dict()` didn't reject action names
  starting with `_` in `task_scope` -- since `AgentGuard.call()` reaches
  the connector via `getattr(connector, action)`, a policy author
  mistake (or malicious policy file) naming e.g. `"__init__"` as an
  action could reach unintended object internals.
- `tests/test_server.py` hardcoded `/tmp/...` for its audit-log path,
  which isn't a valid absolute path on Windows -- 6 tests failed there
  with `FileNotFoundError`, unrelated to any of the fixes above. Now uses
  `tempfile.gettempdir()`.
- `mypy agentguard` had 1 error in `cli.py`'s `click = None` fallback
  pattern (the module's own `[tool.mypy]` config predates whatever mypy
  version first flagged this). Resolved with a scoped `type: ignore`.
- (Caught while building `SlackNotifyApprover`, before this became a
  finding rather than a shipped bug): its `post_fn` parameter was
  originally a plain default argument (`post_fn: ... = _post_to_slack`),
  which Python binds once at import time -- `unittest.mock.patch
  ("agentguard.approval._post_to_slack")` silently would not have
  applied to it, so a test using that (very standard) mocking approach
  would have quietly made a real network call instead of being
  intercepted. Fixed by resolving the default inside `__init__` instead
  of as a parameter default, which is late-bound and does see the patch.
- `docs/ROADMAP_TO_PRODUCTION.md` itself claimed CI "does not yet run
  lint/type-check/security-scan" -- lint/type-check were already there
  (a separate `lint` job in `.github/workflows/tests.yml`, missed rather
  than actually absent); corrected after actually reading the workflow
  file instead of trusting the existing prose.

### Known gaps (see `docs/ROADMAP_TO_PRODUCTION.md`)
- Still no independent third-party security audit or pen test -- this
  review was internal (AI-assisted, human-directed), which is a real
  step up from "nobody has looked at this code" but not the same thing.
- Pending approvals (`QueueApprover`'s in-memory queue) still do not
  survive a restart -- unlike audit logs and quarantine state, there's
  no meaningful way to "resume" a blocked HTTP request across a process
  restart; a restart while an approval is pending loses that specific
  request, same as before this release.
- Docker image still unverified end-to-end (Docker itself unavailable in
  this environment, not just registry access this time).
- Slack integration is a one-way notification, not an interactive
  button -- see "Added" above for why that's deliberate for now.

### Phase 4 -- Dashboard (`apps/web`, first frontend)
A real dashboard for the platform layer built in Phase 1-3, not a mock:
register/login, view team members, and connect/view/revoke connectors,
all against the real FastAPI backend and real Postgres data.

- Two new backend endpoints, first real use of the `connector.read`/
  `connector.write` permission codes that had been seeded but never
  wired to a route: `GET /connectors` (list this tenant's credentials)
  and `POST /connectors/{id}/revoke` (soft-revoke via `revoked_at`,
  same 404 for "not found" and "belongs to another tenant" so the
  response doesn't leak which case it was).
- CORS middleware on `apps/api/main.py`, origins from a new
  `CORS_ALLOWED_ORIGINS` setting (defaults to the Vite dev server).
- `apps/web` -- Vite + React 19 + TypeScript + Tailwind v4 +
  react-router-dom + axios + lucide-react. Pages: login, register,
  dashboard home, team, integrations. Access/refresh tokens in
  `localStorage` for this first version (a deliberate, documented
  simplification vs. an httpOnly cookie -- readable by any script on
  the page, a real gap to close before this is customer-facing) with
  an axios response interceptor that shares one in-flight
  `/auth/refresh` call across concurrent 401s instead of racing
  separate refreshes against each other's rotation (same class of fix
  as `QueueApprover.resolve()`'s idempotency, applied on the client).

**Verified, not asserted, against the real running stack (Postgres in
Docker, `uvicorn` on :8000, Vite dev server on :5173) via a real
browser, not just `tsc`/build passing:** registered a real user
end to end (real `POST /auth/register`, real 201, real tenant/user rows),
landed on the dashboard with real team/connector counts, navigated
Team and Integrations, clicked "Gmail bağla" and confirmed it redirects
to a real `accounts.google.com` consent URL, signed out and logged back
in with the same credentials, and exercised revoke against a disposable
test connector row (inserted and deleted directly in Postgres for this
one check) -- confirmed it disappeared from the UI without touching the
real Gmail credential connected during Phase 3. One real bug caught this
way: `apiErrorMessage()` only handled a string `detail` field; FastAPI's
422 validation responses return `detail` as a list of `{msg, loc, type}`
objects, so a validation error (e.g. registering with a `.dev`-adjacent
reserved-TLD-shaped email during testing) rendered a generic "Registration
failed" instead of the real reason. Fixed to read the first validation
error's `msg` when `detail` is a list.

### Known gaps (see `docs/ROADMAP_TO_PRODUCTION.md`)
- Tokens in `localStorage`, not an httpOnly cookie -- see "Added" above.
- No password-reset, email-verification, or member-invite flow yet --
  the dashboard can only show the team, not grow it.
- Dashboard has no settings/billing/audit-log views yet; the platform
  has no billing at all yet.
- No frontend test suite (no Vitest/Playwright) -- verification for this
  phase was real, but manual (this session's own browser walkthrough).

### Phase 5 -- human approval, wired end to end (execute + approvals)
Everything built through Phase 4 was scaffolding around the actual
point of this project: an AI agent's sensitive action against a real
connected service now genuinely blocks on a human decision, visible
and resolvable from the dashboard -- not just the underlying
`agentguard` library's own tests, a real platform API call against a
real Gmail inbox.

- `infrastructure/database/models/approval.py` -- new `ApprovalRequest`
  table (tenant/credential/requester/action/risk_level/status,
  PENDING|APPROVED|DENIED|EXPIRED). Persisted so a pending decision is
  visible on the dashboard and survives a page refresh -- a real step
  up from `agentguard.approval.QueueApprover`'s in-memory-only queue
  (the one the Flask reference server uses), though not a full fix; see
  Known gaps below for the part that's still process-local.
- `connectors/registry.py` + `connectors/gmail/policy.py` -- maps
  `Credential.connector_type` to a connector class and its default
  `agentguard.policy.Policy`. One connector today (Gmail):
  `list_messages` (metadata only) is LOW risk and auto-approved,
  `read_message` (actual content) is MEDIUM risk and gated behind
  approval -- a deliberate, defensible split, not arbitrary. Not yet a
  per-tenant editable policy; see Known gaps.
- `apps/api/services/approvals.py` -- `DbApprover`, the
  `approval_callback` `agentguard.guardrail.AgentGuard.call()` actually
  invokes: writes a real `ApprovalRequest` row, then blocks (up to 2
  minutes, same default as `QueueApprover`) on a `threading.Event`
  until `POST /approvals/{id}/resolve` wakes it or the timeout expires
  (fails closed -- unresolved becomes EXPIRED, not silently approved).
  Runs inside a threadpool worker via a synchronous SQLAlchemy session
  (psycopg driver), not the app's normal async one -- asyncpg
  connections aren't safe to use from a thread with no event loop.
- `POST /connectors/{id}/execute` (`agent.execute` permission) --
  decrypts the real credential, builds the real connector, wraps it
  with the **unmodified** `AgentGuard`/`Policy` engine (the same one
  111 library tests already depend on), and calls the requested action.
  `PermissionDenied` -> 403 (out of scope), `ApprovalDenied` -> 403
  (denied or timed out), `ConnectorQuarantined` -> 423.
- `GET /approvals` / `POST /approvals/{id}/resolve` (`approval.read` /
  `approval.approve`) -- list recent requests for the tenant; resolve a
  still-PENDING one and wake this process's own blocked call, if it has
  one.
- Dashboard: new **Approvals** page (pending queue + history, 4s poll,
  approve/deny), and an "Ajan aksiyonu test et" button on Integrations
  for each Gmail connector -- since there's no real external agent
  client yet, this simulates one by calling `list_messages` then
  `read_message` through the real execute endpoint, so the whole loop
  is clickable end to end from the UI itself.

**Verified, not asserted, against real infrastructure -- both halves:**
(1) via direct API calls against a real, already-connected Gmail
credential from an earlier session (its owner's password is Argon2-
hashed and genuinely not recoverable, so a real dev-only access token
was minted locally for this account, in this project's own database,
to test the new endpoints as that tenant): `list_messages` completed
immediately (LOW risk, no approval needed) and returned real message
IDs; `read_message` created a real PENDING `ApprovalRequest`, blocked
the HTTP request, and -- resolved from a second call -- returned the
real message content (`Subject: "Güvenlik uyarısı"`, an actual Google
security-alert email) within about 18 seconds of being approved, not
after the 2-minute timeout; a second `read_message` request, denied
instead, returned 403 with AgentGuard's own real
`"'read_message' için insan onayı reddedildi"` message; a
`delete_message` call (outside `task_scope`) was rejected before ever
reaching the connector. (2) via the real dashboard in two real browser
tabs: clicked "Ajan aksiyonu test et" in tab 1 (blocked, waiting),
clicked "Onayla" on the resulting pending row in tab 2's Approvals
page, watched tab 1's request complete with the real email content --
the full agent-to-human-to-agent loop, through the actual UI, not a
mock of it.

One real bug found and fixed during this verification, in the UI, not
the guardrail logic: `DashboardHomePage.tsx` rendered a loading
`<Spinner>` (which renders a `<div>`) inside a `<p>` tag for the two
stat counts -- invalid HTML nesting, logged as a real React
DOM-nesting/hydration error in the browser console. Fixed by using
`<div>` instead of `<p>` for those two counters.

### Known gaps (see `docs/ROADMAP_TO_PRODUCTION.md`)
- The blocking channel is process-local (`apps/api/services/
  approvals.py`'s `threading.Event` registry) -- if this process
  restarts while a call is blocked in `DbApprover.__call__`, that
  specific HTTP request is lost even though its `ApprovalRequest` row
  is still sitting there as PENDING; nothing is listening to wake it
  when it's later resolved. A production version needs an async/
  webhook-driven redesign instead of a worker thread parked in
  `threading.Event.wait()`.
- Policy is fixed in code per connector_type (`connectors/gmail/
  policy.py`), not a per-tenant, dashboard-editable policy yet.
- No audit-log view on the dashboard yet -- `ApprovalRequest` rows are
  themselves a real audit trail for approval decisions, but there's no
  UI for the full `agentguard` event stream (allowed/blocked_scope/
  anomaly_detected/quarantined) at the platform level.
- The "agent" triggering `execute` in this phase is a manual dashboard
  test button, not a real autonomous AI agent client -- addressed in
  Phase 6 below (a real MCP server), though that still isn't a live,
  unattended autonomous agent making its own decisions.
- `POST /connectors/{id}/execute` ties up a threadpool worker for the
  full approval wait (up to 2 minutes) -- fine at today's scale, a real
  scaling concern before this handles meaningful concurrent traffic.

### Phase 6 -- a real MCP server, so a real AI agent client can drive this
Phase 5 proved the guardrail loop end to end, but the thing triggering
it was a dashboard button pretending to be an agent. This phase removes
that pretense: any real MCP-speaking AI agent client (Claude Desktop,
Claude Code, or anything else that speaks Model Context Protocol) can
now connect to AgentGuard directly and have its actions genuinely
gated by the same, unmodified guardrail -- no separate code path, no
weaker enforcement for "real" agents than for the dashboard test button.

- `apps/mcp_server/server.py` -- a real MCP server (stdio transport,
  official `mcp` SDK) exposing exactly two tools: `list_connectors()`
  and `execute_connector_action(connector_id, action, params)`.
  Deliberately generic rather than one tool per connector action --
  both mirror `apps/api`'s own REST endpoints 1:1, so a new connector
  type needs zero changes here. This process never touches Postgres,
  never decrypts a credential, and never imports `agentguard.guardrail`
  directly -- it's a plain HTTP client of `apps/api`, the exact same
  endpoints the dashboard itself calls, so it cannot add a new
  enforcement surface or bypass the platform's own.
- Auth: logs in with `AGENTGUARD_EMAIL`/`AGENTGUARD_PASSWORD` like any
  other client and refreshes its access token on a 401 with the same
  "one shared in-flight refresh" fix as `apps/web/src/api/client.ts`
  (`AGENTGUARD_ACCESS_TOKEN`/`AGENTGUARD_REFRESH_TOKEN` are also
  accepted, as a local-dev-only shortcut). A real service-account/API-
  key mechanism, distinct from a human's personal login, is still real
  future work -- see Known gaps.
- New `agentguard[mcp_server]` extra (`mcp>=1.0`, `httpx>=0.27`) and a
  `agentguard-mcp-server` console script, kept separate from the `api`
  extra -- a platform deployment doesn't need this process running in
  the same place, and vice versa.

**Verified, not asserted, with a real MCP client-server round trip --
not a mock of the protocol:** spawned `apps/mcp_server/server.py` as a
real subprocess over stdio (exactly how Claude Desktop/Claude Code
would launch it) using the official `mcp` SDK's own `ClientSession`,
against a real, already-connected Gmail credential. `list_connectors`
returned the real connector; `execute_connector_action(list_messages)`
completed immediately with real message IDs (LOW risk, auto-approved).
`execute_connector_action(read_message)` genuinely blocked the MCP tool
call -- confirmed a real `PENDING` `ApprovalRequest` row appeared,
resolved it via `POST /approvals/{id}/resolve` (the same endpoint the
dashboard's Approvals page uses), and the still-open MCP tool call
returned the real message content (`Subject: "Güvenlik uyarısı"`)
seconds later, not after the 2-minute timeout. Same result whether the
caller is a browser dashboard button or a real MCP client -- because
it's the same backend endpoint enforcing the same policy either way.

### Known gaps (see `docs/ROADMAP_TO_PRODUCTION.md`)
- No real service-account/API-key auth for the MCP server -- it logs in
  with a human's own email/password today, same as the dashboard.
- Not tested against an actual GUI MCP client (Claude Desktop) in this
  session -- verified with the `mcp` SDK's own client library instead,
  which speaks the identical protocol but isn't the same as configuring
  a real desktop app's `claude_desktop_config.json` and watching it work.
- Still no live, unattended autonomous agent -- this phase proves a real
  agent *client* can drive AgentGuard correctly; an agent that decides
  *on its own*, on a schedule, without a human in the loop starting it,
  is a different (and riskier) thing this project hasn't built yet.

### Phase 7 -- process-independent approvals, plus an independent security review
Closes Phase 5's biggest known gap: nothing blocks on a human decision
anymore, anywhere. Also includes fixes from a real, independent
security review of the auth/RBAC/approval/OAuth code -- run twice
(Cyber AI's real product-workspace access is scoped to nothing yet by
design, so the review ran against the actual file contents pasted
directly into its task rather than filesystem access -- the honest,
sanctioned path its own rejection format pointed to, not a workaround).

**Approval flow redesign:**
- `POST /connectors/{id}/execute` no longer blocks. A non-sensitive
  action still runs immediately; a sensitive one is recorded as a
  PENDING `ApprovalRequest` and the call returns right away with
  `{"status": "pending_approval", "approval_id": ...}`. The real guarded
  call now happens inside `POST /approvals/{id}/resolve`, at the moment
  a human actually decides -- possibly a different backend process than
  the one that created the request, possibly after a restart in
  between. `apps/api/services/execution.py` is the one shared, pure
  (no DB access) function both paths call.
- New `GET /approvals/{id}` for polling one request; `ApprovalRequest`
  gained `result_json`/`error_message` columns so a caller (the
  dashboard, `apps/mcp_server`) can retrieve the outcome after the fact
  instead of needing a still-open connection.
- `apps/api/services/approvals.py`'s `threading.Event`/`_waiting`
  registry is gone entirely -- replaced by `expire_stale_approvals()`,
  a lazy sweep (PENDING + older than 30 minutes -> EXPIRED) run on every
  list/get/resolve. The approval window went from 2 minutes to 30 for
  the same reason: nothing is tying up a worker thread for however long
  a human takes anymore, so there's no cost to giving them longer.
- Dashboard (`waitForApprovalOutcome` in `apps/web/src/api/approvals.ts`)
  and the MCP server (`execute_connector_action`) both now poll for the
  result client-side instead of relying on the backend to hold the
  connection open -- same "wait for the answer" experience from the
  caller's point of view, none of the old fragility.

**Security fixes (from the independent review):**
- `POST /approvals/{id}/resolve` now claims a request with a single
  conditional `UPDATE ... WHERE status = 'PENDING'` instead of a
  read-then-write -- two concurrent resolve calls for the same id can
  no longer both appear to succeed (Postgres serializes the competing
  UPDATEs; the loser gets a clean 409). The prior read-then-write
  version couldn't double-execute the guarded connector call (Phase 5's
  own design already made that fail-safe), but it could leave the audit
  trail -- status/resolved_by/resolved_at -- inconsistent with what
  actually ran. **Verified with two real concurrent HTTP requests
  racing to resolve the same approval**, not just reasoned about: one
  got 200 with the real result, the other got 409.
- A second, related bug found *while verifying the fix above with a
  live poll*, not by inspection: claiming the row and recording the
  guarded call's result were two separate commits, leaving a real
  window where a poller could observe `status: "APPROVED"` with
  `result: null`. Fixed by folding both into one transaction/commit --
  a poller now only ever sees the row before resolution starts or
  fully after, never mid-way.
- The OAuth PKCE `code_verifier` (`infrastructure/auth/jwt.py`) is now
  encrypted (via the existing Fernet `secret_store`, the same one
  credentials are encrypted with) before going into the signed `state`
  JWT, not left as a plaintext claim. A JWT's signature proves the
  payload wasn't tampered with but doesn't hide it, and `state` round-
  trips through the browser on Google's redirect back -- anything that
  could observe that (browser history, a proxy access log, a leaked
  Referer) could otherwise read the verifier, undermining part of what
  PKCE (RFC 7636) exists to prevent.
- `apps/mcp_server/server.py`'s token-refresh logic fell back to a full
  password login (`AGENTGUARD_EMAIL`/`AGENTGUARD_PASSWORD`) on *any*
  non-200 response from `/auth/refresh`, not just a definitive 401 --
  meaning a transient 5xx or network hiccup would re-authenticate with
  the stored password far more often than intended. Now only a 401
  triggers re-login; anything else surfaces as a real error.

**Known gaps surfaced by the review, not fixed this phase (tracked, not
ignored):**
- No refresh-token reuse detection or family-based revocation, and no
  absolute maximum session lifetime (today's rotation is a sliding 30-
  day window) -- a stolen refresh token used regularly could stay valid
  indefinitely.
- `ApprovalRequest.call_context` (an agent's action parameters) is
  stored and shown in plain text to anyone with `approval.read` -- fine
  for Gmail's `message_id`-only actions today, but a future connector
  whose parameters carry real content (an email body, a customer
  record) would need redaction/classification before this is safe to
  widen.
- `/auth/register` reveals whether an email/tenant-slug already exists
  via a direct 409, unlike `/auth/login`'s timing-safe non-disclosure --
  a generally-accepted, low-severity inconsistency for a registration
  flow, flagged for awareness rather than fixed.
- No rate limiting or account lockout on `/auth/login`.
- The platform's own Google OAuth `client_secret` is duplicated inside
  every encrypted Gmail credential row (needed for `google-auth`'s own
  token-refresh calls) rather than read from one central place --
  widens blast radius slightly if `SECRET_ENCRYPTION_KEY` is ever
  compromised.

### Phase 8 -- second connector (GitHub): proving the platform isn't Gmail-shaped
Every piece of the platform through Phase 7 -- the registry, the
generic `execute`/`approvals` endpoints, the dashboard, the MCP server
-- was built to be connector-agnostic in principle. This phase is the
actual proof: a second, structurally different connector added without
touching `apps/api/routers/connectors/general.py`, `apps/api/routers/
approvals.py`, `apps/api/services/execution.py`, the dashboard's
Approvals page, or `apps/mcp_server/server.py` at all.

- `connectors/github/` -- `GitHubConnector` (PyGithub), two actions:
  `list_repos` (LOW risk, auto-approved) and `close_issue` (HIGH risk,
  gated). Gmail's first pass only ever exercised the approval gate on a
  *read* (`read_message`); `close_issue` is a real, consequential
  *write*, proving the same mechanism covers mutating actions on an
  entirely different kind of service, not something special-cased for
  email.
- `connectors/github/oauth.py` -- GitHub OAuth Apps use a confidential-
  client authorization-code flow with no PKCE (unlike Google's), so
  `infrastructure/auth/jwt.py`'s `create_oauth_state_token`/
  `decode_oauth_state_token` had their `code_verifier` parameter made
  optional (`str | None`) rather than writing a second, parallel
  state-token mechanism just for GitHub.
- `connectors/registry.py` gained one more entry (`CONNECTOR_CLASSES`,
  `POLICY_FACTORIES`, `RISK_LEVELS`) -- the whole reason this phase
  needed no changes to the generic execute/approve/poll code, which
  only ever looks a connector_type up in this registry.
- Dashboard: "GitHub bağla" button, a "Depoları listele" trigger, and a
  small inline `owner/repo` + issue-number form wired to `close_issue`
  through the exact same `runGuardedAction` polling helper Gmail's test
  button already used -- extracted from Gmail's handler in this phase
  once there was a second, real caller of the same pattern.

**Not yet verified against real GitHub infrastructure in this
session** -- unlike Gmail (Phase 3) and the approval-flow rewrite
(Phase 7), this needs a real GitHub OAuth App (client ID/secret,
callback URL registered at github.com/settings/developers), which only
the human operator can create and consent through, the same standing
rule that applied to every real OAuth flow in this project. Backend
startup and route mounting were verified (the new `/connectors/github/
authorize` route correctly 401s unauthenticated rather than 404ing),
`ruff`/`mypy`/the 111-test core suite all pass, and the frontend
type-checks and lints clean -- but the actual authorize -> consent ->
callback -> `list_repos`/`close_issue` round trip against
api.github.com is still open, pending real OAuth App credentials.

**Update, same session:** the real round trip above did happen --
`gelecege1yatirim-jpg` connected GitHub for real, `list_repos` and
`close_issue` both ran against a real repo/issue through the real
guarded flow. One real bug surfaced by that live run, not by
inspection: the first two `close_issue` attempts 404'd -- not a code
bug, the target issue genuinely didn't exist yet (confirmed by querying
the GitHub API directly with the stored token: `open_issues_count: 0`).
Once a real issue existed, the third attempt succeeded end to end,
approval gate included.

### Phase 9 -- frontend-only signature redesign (Approvals as the product's actual moment)
UI/UX-only pass (no backend/API/DB changes, per explicit instruction)
using the `ui-ux-pro-max` skill (github.com/nextlevelbuilder/
ui-ux-pro-max-skill, installed into `.claude/skills/`) for design
reasoning, not as a drop-in theme. Two earlier attempts in this same
session were walked back after real feedback: a first pass was too
subtle to notice (a color-token swap alone), a second overcorrected
into generic AI-dashboard glassmorphism/gradient/glow. This phase
replaces both with a restrained system and one real, data-grounded
signature interaction.

**Data honesty, enforced as a hard rule this phase (see
`design-system/MASTER.md`):** no invented entities. There is no
"Threat", "IOC", "Vulnerability", "MITRE ATT&CK", or "Agent" (distinct
from a real `User`) anywhere in this product's data model, so none of
those appear in the UI. Every node, edge, and metric traces to a real
field already returned by `/connectors`, `/approvals`, or
`/tenant/members`.

- `design-system/MASTER.md` -- new, central design source: semantic
  color only (info/warning/critical/healthy mapped to real
  `risk_level`/`status` values, never decorative), typography scale,
  motion rules (`transform`/`opacity` only, respects
  `prefers-reduced-motion`, already-global).
- `components/ActionFlow.tsx` -- the real signature moment: a
  5-stage state-machine visualization (*Eylem İstendi -> Risk
  Değerlendirildi -> Onay Gerekiyor -> İnsan Kararı -> Eylem
  Yürütüldü*) whose per-stage visual state is computed **only** from a
  real `ApprovalRequest`'s `status`/`error` fields -- PENDING pulses at
  the approval gate, DENIED stops red at the decision stage, EXPIRED
  stops muted at the gate, APPROVED lights all five (red on the last
  stage if the post-approval execution itself failed).
- `components/ActionGraph.tsx` -- SVG graph of the real relationship
  `User (requested_by_email) -> Connector (credential_id) -> Action
  (ApprovalRequest)`. Hovering a connector or user highlights its real
  edges and dims the rest; clicking an action node selects it. No
  fabricated node types.
- `components/ApprovalDetailPanel.tsx` -- real detail (risk, params,
  result/error) plus the **real** Approve/Deny buttons, still calling
  the unchanged `POST /approvals/{id}/resolve`, with loading/success/
  error states.
- `pages/ApprovalsPage.tsx` rebuilt around these three as one page:
  graph on top, pending/history list, detail panel. Per instruction,
  this page got the full-quality pass first; `Layout`/`ui.tsx` and the
  other pages got a lighter consistency pass (semantic `Badge` tones
  everywhere, restrained `Card`/`Button`, no blur/gradient) rather than
  the same depth of new interaction.

**Verified, not asserted, with real data already sitting in the
database from this session's own testing** (three resolved
`github.close_issue` approvals, plus a freshly triggered
`gmail.read_message` one): loaded `/approvals` in a real browser tab,
confirmed the graph rendered the real user/connector/action nodes,
selected the live PENDING item, clicked the real "Onayla" button, and
watched it move from Bekleyenler to Geçmiş with the real Gmail result
rendered in the panel -- no page reload, no mock. `tsc -b`, `oxlint`,
and `vite build` all clean throughout.

**Known gap:** only `ApprovalsPage` received the full redesign depth
this phase, as instructed ("önce signature experience, sonra aynı dili
diğer sayfalara taşı") -- `DashboardHomePage`/`IntegrationsPage`/
`TeamPage` share the new restrained tokens but not yet the same level
of custom interaction.

### Phase 10 -- dark theme, decorative companion mascot, user-toggleable panel
Frontend-only again (no backend/API/DB changes). Direct follow-up to
real feedback on Phase 9's decorative additions: a duplicated
`ActionGraph` pasted onto the Dashboard was removed and replaced with
`components/HeroParallax.tsx` (mouse-reactive parallax banner, no real
data). This phase adds a full dark theme and a second decorative
companion element, both explicitly requested with an escape hatch
("Belki bu gifi falan beğenmezsem eski haline geçeriz tekrar" -- if I
don't end up liking it, we go back).

- `context/ThemeContext.tsx` -- new. `light`/`dark`, persisted to
  `localStorage['agentguard_theme']`, initial value falls back to
  `prefers-color-scheme` when nothing is stored yet. Class-based
  (`.dark` on `<html>`), not `prefers-color-scheme`-only, so the user's
  explicit choice always wins over their OS setting.
- `index.css` -- `@custom-variant dark (&:where(.dark, .dark *));`
  (required by Tailwind v4 for class-based dark mode), a `.dark {}`
  token override block, and new `--graph-*` variables so
  `ActionGraph.tsx`'s SVG `fill`/`stroke` attributes (which Tailwind's
  `dark:` classes can't reach) stay theme-aware too.
- `dark:` variants propagated across every shared primitive
  (`components/ui.tsx`: `Card`, `Button`, `Badge` tones, `EmptyState`,
  `Spinner`) and every page/component that renders real data --
  `ApprovalDetailPanel.tsx`, `ActionFlow.tsx`, `ApprovalsPage.tsx`,
  `DashboardHomePage.tsx`, `TeamPage.tsx`, `IntegrationsPage.tsx`,
  `LoginPage.tsx`, `RegisterPage.tsx`. `HeroParallax.tsx`/
  `MascotBird.tsx` needed no changes -- both already use fixed,
  self-contained colors that hold up in either theme.
- `components/AIChatCompanions.tsx` -- new, purely decorative (same
  category as `MascotBird.tsx`, explicitly documented as such in the
  file so the data-honesty rule stays legible to the next read): two
  small chibi computers with cursor-tracking eyes and a pulsing shield
  between them standing in for AgentGuard keeping that exchange safe.
  Never claims to show a real agent conversation. Lives in the
  right-side panel the user marked in a screenshot.
- `components/Layout.tsx` -- two new sidebar controls: an "Animasyonlar"
  pill toggle (shows/hides `AIChatCompanions`, persisted to
  `localStorage['agentguard_show_companions']`, defaults to visible)
  and a Sun/Moon theme toggle calling `useTheme().toggleTheme()`. The
  companion panel also has its own inline close button wired to the
  same handler -- two ways to turn it off, one state.

**Verified, not asserted:** `tsc -b --noEmit`, `oxlint`, and `vite build`
all clean (`dist/` removed after). In a real browser tab against the
running dev server: clicking the theme toggle flips `document
.documentElement.className` to `dark`, updates `localStorage
['agentguard_theme']`, and repaints `body`'s computed background to the
dark token -- confirmed on Dashboard, Approvals (including the detail
panel and its `ActionFlow` stage labels, which read correctly as
light-on-dark, not a light panel stranded on a dark page), and Team.
Clicking "Animasyonlar" removes the companion `<aside>` from the DOM
and persists `false`; reloading the page confirms both the theme and
the companions-hidden state survive a full reload. The companion
panel's structure was confirmed in the DOM (two computer-face `<svg>`s,
the pulsing shield, the close button, the caption text) -- restored to
the defaults (light theme, companions visible) after testing.

**Known limitation, stated rather than glossed over:** this session's
browser tool reports the tab as `document.hidden` when not actively
displayed, which pauses `requestAnimationFrame` in Chromium. That
blocks visually confirming the *live* motion -- the companions' eyes
tracking the cursor, the traveling connection dot, `HeroParallax`'s
tilt -- from this tool. Everything structural (DOM presence, classes,
computed static styles) was verified; the animation itself needs a
human check in a real browser.

### Phase 10.1 -- swapped the cute mascot panel for a restrained "signal" visual
Direct follow-up: the two-computer mascot from Phase 10 "wasn't bad,"
but the ask was for something more professional and more fitting for a
security-governance product. Pointed at https://senthora.ai/ as a
reference. Inspected it (no code/asset copied, technique only): a
fixed full-viewport WebGL (Three.js) background behind all content,
plus a fixed, low-opacity (`0.07`), `mix-blend-mode: overlay` film-grain
canvas on top -- the "alive but calm" feel comes from an always-on
ambient layer + grain texture, not from a literal character animation.

- `components/AIChatCompanions.tsx` deleted; replaced by new
  `components/SecurityPulse.tsx` -- no chibi faces. An abstract scene:
  small dots (standing in for AI agent actions, no claim to be a real
  feed) travel down a vertical channel through a shield checkpoint that
  pings softly on a slow, restrained cadence, inside a fixed dark
  panel with a soft ambient glow blob (same blurred-circle technique as
  `HeroParallax.tsx`) and a static SVG `feTurbulence` grain overlay
  (`opacity-[0.05]`, `mix-blend-overlay`) -- the grain idea taken
  directly from what Senthora's background layer does, reimplemented
  from scratch in plain SVG rather than WebGL, since Three.js was
  already ruled out earlier in this project for being unnecessary
  weight for decorative chrome.
- The glow blob drifts a few pixels toward the cursor (same
  rAF-throttled, `prefers-reduced-motion`-gated mousemove pattern used
  throughout this session) -- keeps the "kayan görsel" (mouse-reactive
  drifting visual) requirement from Phase 10 without eyeballs.
- Unlike the old panel, `SecurityPulse` has a fixed dark background
  regardless of the app's light/dark theme -- same idea as
  `HeroParallax`, and it now bookends the layout with the sidebar
  (both permanently dark "chrome"), which reads more like a console
  strip than a toggled UI panel.
- `index.css`: `companion-dot` keyframes removed (dead code, only
  consumer deleted), replaced with `signal-dot` (same dot-travel shape,
  longer 220px throw for the taller channel) and a new `radar-ping`
  keyframe for the shield's slow outward ping rings.
- `Layout.tsx`: the toggle wiring is unchanged in behavior, renamed for
  accuracy -- `showCompanions`/`toggleCompanions` to `showPulse`/
  `togglePulse`, storage key `agentguard_show_companions` to
  `agentguard_show_pulse` (existing users see the toggle default back
  to visible once, harmless).

**Verified, not asserted:** `tsc -b --noEmit`, `oxlint`, `vite build`
all clean. Confirmed no remaining references to the deleted component,
old keyframe, or old storage key anywhere in the repo. In a real
browser tab: the new panel's fixed dark background renders identically
whether `document.documentElement` has `.dark` or not (confirmed both
ways), the show/hide toggle removes/restores it from the DOM and
persists to `localStorage['agentguard_show_pulse']`, and its structure
was confirmed present (grain `<svg>` with `filter#pulse-grain`, the
pulsing shield, 2 traveling dots, 2 ping rings, the close button).
Same limitation as Phase 10 applies to the live motion itself (cursor
drift, dot travel, ping rings) -- structurally verified, not visually,
due to this tool's `document.hidden` behavior when the pane isn't
displayed.

### Phase 10.2 -- brand accent moved from generic blue to teal
Direct request: move the whole site off "the colors every AI dashboard
uses" (Tailwind's default `blue-600`, present in essentially every
generic AI-product template) to something more deliberately chosen for
this specific product. AgentGuard's job is continuous, calm oversight
of autonomous agents -- teal/cyan is the conventional color of
monitoring, radar, and surveillance tooling, and it directly echoes the
"signal/ping" visual language already built into `SecurityPulse.tsx`
and the `ActionFlow`/`ActionGraph` review journey, rather than being an
arbitrary swap.

**Split kept deliberate, not an oversight:** semantic risk/status color
(`Tone` type in `ui.tsx`: `info`/`warning`/`critical`/`healthy`, and
`--c-info`/`--c-warning`/`--c-critical`/`--c-healthy` in `index.css`)
is untouched and stays blue/amber/red/emerald -- those colors carry
real meaning (`riskTone`/`statusTone`, mapped from actual
`risk_level`/`status` fields) and changing them isn't a brand decision,
it's a data-honesty one (`design-system/MASTER.md`). Only brand/chrome
-- buttons, active nav, logo, focus rings, links, decorative accents --
moved to teal. New `--c-brand` CSS token (`#0d9488` light / `#2dd4bf`
dark) was added specifically so `ActionGraph.tsx`'s connector-node
color no longer borrows the semantic `--c-info` variable for a
non-semantic purpose (it used to, which was an unrelated hazard: a
future edit to "info" for semantic reasons would have silently
recolored connector nodes too).

- `index.css` -- `--color-ring`, `--graph-connector-fill/-stroke` (both
  light/dark), new `--c-brand` token.
- `components/ui.tsx` -- `Button`'s `primary` variant, `Spinner`.
  `TONE_CLASSES`/`TONE_DOT` (semantic) untouched.
- `components/ActionGraph.tsx` -- connector node icon/label now read
  `var(--c-brand)`; `TONE_HEX.info` (semantic, used for real risk/status
  coloring) untouched.
- `components/Layout.tsx`, `LoginPage.tsx`, `RegisterPage.tsx` -- logo
  badge, active nav highlight, focus rings, submit buttons, links.
- `components/HeroParallax.tsx`, `SecurityPulse.tsx`, `MascotBird.tsx`
  -- decorative accents recolored for a consistent brand feel across
  every decorative element built this session.
- `DashboardHomePage.tsx`, `IntegrationsPage.tsx`, `TeamPage.tsx`,
  `ApprovalsPage.tsx`, `ProtectedRoute.tsx` -- links, connector-icon
  badges, member-count pill, selected-row highlight, loading spinner.
  (`ProtectedRoute`'s loading screen also picked up the `dark:`
  variants it was missing from the Phase 10 retrofit, noticed in
  passing.)

**Verified, not asserted:** grepped the whole `src/` tree afterward for
every blue hex/class this phase targeted -- the only four remaining
hits are the intentionally-preserved semantic `info` tone (`ui.tsx`
`TONE_CLASSES.info`/`TONE_DOT.info`, `ActionGraph.tsx`
`TONE_HEX.info`, `index.css` `--c-info`). `tsc -b --noEmit`, `oxlint`,
`vite build` all clean. In a real browser tab: confirmed computed
background color on the sidebar logo, active nav link, and
`SecurityPulse`'s shield all resolve to the new teal (`--c-brand`,
`#0d9488`); confirmed `ActionGraph`'s connector node text/icon render
teal; confirmed `--color-ring`/`--c-brand` read teal while `--c-info`
still reads the original blue; confirmed the Team page mascot's body
and wing render the new teal fills.

### Phase 10.3 -- real logo, toggle-knob overflow fix
Two small, unrelated fixes landed together.

**Sidebar toggle overflow.** The "Animasyonlar" switch's round knob
could render outside its pill track. Root cause: the knob was
positioned `absolute` with only `top-0.5` set and no `left`, so its
horizontal position fell back to the CSS static-position algorithm
instead of a fixed anchor -- inconsistent depending on the track's own
`display` (which had the same underlying issue: the track was a bare
`<span>` with `w-7 h-4` classes but no `display` override, and `width`/
`height` don't apply to non-replaced *inline* elements, so the track's
box never actually sized itself). Fixed both: `Layout.tsx`'s track
`<span>` gained `inline-block`; the knob gained an explicit `left-0.5`
anchor. Separately, the `translate-x-3` Tailwind utility this build
was generating turned out not to produce any CSS rule (verified via
computed style: `translate-x-0`/`translate-x-0.5` worked,
`translate-x-3`/`translate-x-3.5` did not, for reasons not fully
chased down) -- rather than debug the utility generation further, the
knob's slide moved to an inline `style={{ transform: ... }}`, which
sidesteps the question entirely.

**Real logo.** User supplied a finished logo after reviewing six
AI-generated alternatives (five original directions plus one "no
shield, more abstract" concept requested after the first round) and
preferred their own. Found at `~/Desktop/AgentGuard-Logo.png` (1254x1254,
opaque near-black `#010513` background baked in). Chroma-keyed the flat
background to transparency (per-pixel distance threshold against the
sampled background color, soft-edged to preserve anti-aliasing) rather
than dropping the flat PNG onto the app's own dark surfaces, which
don't share that exact near-black and would have shown as a visible
mismatched box. Cropped the shield mark alone (excluding the
"AgentGuard" wordmark/tagline, which aren't used at the small sizes
this logo appears at anywhere in the current UI) to `public/logo-mark.png`
(512x512) and a smaller `public/favicon.png` (256x256); replaced the
placeholder teal `ShieldCheck` icon+box in `Layout.tsx`'s sidebar
header, `LoginPage.tsx`, and `RegisterPage.tsx` with `<img
src="/logo-mark.png">`. Left `SecurityPulse.tsx`'s and
`HeroParallax.tsx`'s own `ShieldCheck` icons alone -- those are
decorative UI chrome, not brand-logo placements, so swapping them
wasn't in scope. `index.html` -- `<title>` was still the Vite default
`web`, now `AgentGuard`; favicon now points at the real mark instead of
the generic scaffolded one (`favicon.svg`, unrelated to this product,
deleted since nothing else referenced it).

**Verified, not asserted:** `tsc -b --noEmit`, `oxlint`, `vite build`
all clean. In a real browser tab: confirmed `/logo-mark.png` loads
(`naturalWidth/Height` 512x512, `.complete === true`) on the sidebar
and on the login page; confirmed the tab title reads "AgentGuard" and
the favicon link points at `/favicon.png`. The toggle-knob geometry fix
was verified at the DOM/style level (inline `transform` correctly
alternates between `translateX(0)` and `translateX(12px)`, track sized
28x16px via `inline-block`, knob anchored at `left: 2px`, math checks
out to a symmetric 2px inset on both ends in either state) rather than
by pixel measurement -- `getBoundingClientRect()` returned stale values
in this tool while the browser pane isn't actively displayed, a
known limitation from earlier phases, not a rendering problem in the
app itself.

### Phase 11 -- product/security audit, HTTP-level tests for connectors and approvals
Full audit of `apps/api`, `infrastructure/`, and `connectors/` against
"is this a real product" and "is this secure," at the user's request,
before shifting this project's focus from frontend design to backend
coding. Findings, not fixed yet in this phase (queued for follow-up):

- **No rate limiting anywhere**, including `/auth/login`, `/auth/register`,
  `/auth/refresh` -- nothing here mitigates brute-force/credential-stuffing.
  The most important open security gap.
- **No local git repository.** `agentguard-ai-2/agentguard` has a
  `.gitignore` and `.github/workflows/` but no `.git` -- the CI defined
  there has never actually run, and this entire session's work (every
  phase above) exists only on disk, uncommitted.
- **No team-invite flow.** `POST /auth/register` always creates a brand
  new tenant + OWNER; there's no endpoint to add a second user to an
  *existing* tenant, so the Team page can never show more than one
  member today.
- **No password reset / email verification.**
- **`docs/ROADMAP_TO_PRODUCTION.md` and `SECURITY.md` don't mention
  `apps/api`/`apps/web` at all** -- both still describe only the
  `agentguard` library and the Flask `server/app.py` reference
  deployment. The actual live product (the dashboard this whole
  session has been building) has no roadmap gaps or security scope
  documented anywhere. Not corrected in this phase -- flagged for the
  user to decide whether/how to fold the SaaS platform track into
  those docs.
- Policy (`task_scope`/risk levels) is hardcoded per connector type,
  identical for every tenant -- no per-tenant customization yet, a real
  limitation for a product whose whole pitch is governance.
- No dependency lockfile on the Python side (`pyproject.toml` uses
  `>=` ranges throughout).

**What WAS genuinely solid, stated for calibration rather than left
implicit:** `infrastructure/auth/jwt.py`'s access/refresh split,
`apps/api/routers/auth.py`'s timing-attack-safe login (a fixed dummy
Argon2 hash always gets verified, even for a nonexistent email) and
refresh-token rotation, `apps/api/dependencies.py`'s deny-by-default
RBAC, `apps/api/routers/approvals.py`'s atomic re-resolve race guard
(`UPDATE ... WHERE status='PENDING'`, re-checked in the same statement,
not read-then-write), tenant isolation enforced on every single query
across every router, `infrastructure/secrets/store.py`'s Fernet
encryption-at-rest for connector credentials, and
`agentguard/policy.py`'s explicit rejection of any `task_scope` entry
starting with `_` (closing exactly the `getattr(connector, action)`
attribute-injection risk that dispatch mechanism would otherwise open)
-- several of these already carry comments citing a *previous*
self-review that found and fixed the version before this one. This is
not a from-scratch amateur codebase; the gaps above are real, but they
sit on top of an unusually careful foundation for a solo/AI-assisted
project.

**Closed one of the findings above, this phase:** zero HTTP-level test
coverage for `/connectors/*` and `/approvals/*` -- the two routers
that carry this project's actual point, previously exercised only
manually (this session's own browser verification) and indirectly
(agentguard/'s own unit tests against a fake connector, not the real
HTTP routes). New `tests/test_infra_api_connectors_approvals.py`, same
real-Postgres-or-skip discipline as `tests/test_infra_api.py`: 19 new
tests covering tenant isolation on every route, RBAC (VIEWER blocked
from write/execute/resolve, allowed read), policy-scope enforcement
(403 for an out-of-scope action), the pending-approval hand-off,
denying (verified to never touch the connector), and the atomic
re-resolve race guard (a second resolve on an already-resolved request
returns 409). Deliberately out of scope, stated rather than silently
skipped: `resolve_approval`'s `approved=True` branch's real network
call to `api.github.com` -- no disposable, OAuth-linked test GitHub
account exists in this environment to call it with safely; that path
was verified manually against real data in Phase 9.

**Verified, not asserted:** ran the new file alone (19/19 pass), ran
the full existing suite alongside it (130/130 pass, no regressions),
`ruff check`/`mypy` clean on the new file.

### Phase 12 -- first real push to GitHub, CI fixed, rate limiting

**Getting this repo onto GitHub at all took three broken attempts,**
worth recording honestly rather than glossing over: (1) GitHub Desktop's
"Create New Repository" pointed at the wrong folder and published an
essentially empty repo; (2) a second attempt nested the entire project
one directory too deep inside a throwaway wrapper repo Desktop had
auto-generated (with its own default `LICENSE`/`.gitattributes` and, critically,
no `.gitignore`); (3) a stray `.git` left over from attempt #1, sitting
*inside* the `agentguard/` library subfolder, made git treat that whole
subfolder as an embedded repo and silently exclude its real contents
(`guardrail.py`, `policy.py`, etc.) from every commit -- the local
working tree looked complete, but nothing of substance had actually
reached GitHub. Fixed each in turn: moved the project back out of the
wrapper, deleted the stray nested `.git`, re-added the now-real package
contents, and initialized/committed/pushed from the correct root via a
mix of direct `git`/`gh` commands and GitHub Desktop (Desktop's own
credential session completed the actual `push`/`publish` step; local
`git push` from this environment has no working GitHub credentials).
`.gitignore` gained `.idea/`, `.mypy_cache/`, `.ruff_cache/`, and
`.claude/` (Claude Code's own installed skill tooling -- never part of
this project's source, and its ~150 files were the actual cause of the
first real CI lint failure: 354 `ruff` errors, 100% of them inside
`.claude/skills/`, 0% in this project's own code).

**CI, fixed for real this time:** the first push surfaced two genuine
gaps `docs/ROADMAP_TO_PRODUCTION.md`'s "CI is green" claim never
covered, because CI had never actually run against a real GitHub push
before now:
- `.github/workflows/tests.yml`'s `env:` block set `DATABASE_URL` and
  `JWT_SECRET_KEY` but never `SECRET_ENCRYPTION_KEY` -- `alembic
  upgrade head` failed before touching the database at all, inside
  `Settings()`'s own pydantic validation. Fixed by generating a real
  (CI-only, not a production secret) Fernet key and adding it to the
  workflow, then confirming `Settings()`/`FernetSecretStore` construct
  and round-trip correctly with those exact three env vars set.
- The `.claude/` removal above, which also fixed `lint`.

**Rate limiting**, closing the top item from Phase 11's audit: per-IP,
in-memory (`apps/api/rate_limit.py`, `slowapi`/`limits`) on
`/auth/login` (5/minute), `/auth/register` (5/hour), and `/auth/refresh`
(30/minute) -- before this, none of the three had any brute-force or
registration-spam mitigation at all. `RATE_LIMIT_ENABLED` (new Settings
field, default `true`) lets tests/CI turn it off, since
`tests/test_infra_api.py` and `tests/test_infra_api_connectors_approvals.py`
both legitimately call `/auth/register` dozens of times per run --
disabled via an env var set *before* `apps.api.main` is first imported
in each of those files, since the `Limiter` is constructed at module
load time. New `tests/test_rate_limit.py` is the one place that turns
the real `app.state.limiter` back on and proves the mechanism actually
blocks (not just that the decorator is present in source): the 6th
`/auth/login` call within a minute gets a real `429` with a
`Retry-After` header, and `/auth/refresh`'s separate limit is
confirmed unaffected by `/auth/login`'s being exhausted.

**Verified, not asserted:** `ruff check`, `mypy` (both `agentguard` and
`infrastructure`+`apps`), and the full suite -- 132 tests, 0 skipped --
all clean against the real local Postgres. Pushed to GitHub via
Desktop; confirmed in GitHub Actions that both the `test` and `lint`
jobs, which had failed on the first real push for the reasons above,
now pass.

### Phase 13 -- team invites, and a real bug rate-limit tests missed

**Team invites**, closing the next item from Phase 11's audit:
`POST /auth/register` always created a brand new tenant, so a workspace
could never grow past its first user. New `Invitation` model
(`infrastructure/database/models/invitation.py`, migration
`ba2865050d34`) plus `POST/GET /tenant/invites` and
`DELETE /tenant/invites/{id}` (`apps/api/routers/tenant.py`, all
`tenant.admin`-gated) and `GET /auth/invites/{token}` (public preview)
+ `POST /auth/accept-invite` (`apps/api/routers/auth.py`). Token is
opaque (`secrets.token_urlsafe`), stored sha256-hashed -- same
principle as `RefreshToken`, a database leak alone must not hand out a
usable invite. The raw token is returned exactly once, on creation, for
the admin to send however they like (Slack, email client, whatever) --
this project doesn't send email itself yet, that's real future work,
stated rather than implied-done. `OWNER` is deliberately not an
invitable role: a mere `tenant.admin` (ADMIN, not necessarily OWNER)
issuing invites must not be able to mint a peer owner, wider privilege
than they were granted. `accept-invite` takes the email from the
invite record, never from the request body, so a token can only ever
create the account it was issued for. Dashboard: `TeamPage.tsx` gained
an "Davet et" button (shown only to OWNER/ADMIN client-side --
`require_permission("tenant.admin")` is what actually enforces it), an
inline invite form, a copyable invite-link box, and a pending-invites
list with revoke; new `AcceptInvitePage.tsx` (`/accept-invite?token=`)
previews the invite (tenant/role/email) before asking for a password,
then auto-logs the new member in via the same `AuthContext` flow
`login`/`register` already use.

**A real bug this phase's own automated tests didn't catch, found by
hand in a real browser:** `apps/api/rate_limit.py`'s `headers_enabled=True`
(added in Phase 12 to expose `X-RateLimit-*` quota headers) crashes
every SUCCESSFUL response from a rate-limited route with a 500 --
slowapi's header-injection step needs the raw `Response` object, but a
FastAPI route declared with `response_model=...` returns a plain
Pydantic model until well after slowapi's wrapper has already run.
`tests/test_rate_limit.py` only ever drove `/auth/login` into its
REJECTED path (401 wrong-password, 429 rate-limited) -- both raise
before slowapi tries to inject anything, so nothing in the suite
exercised a genuine 2xx through a rate-limited route. Manually testing
the new invite flow in a real browser hit it immediately: creating an
invite worked (write-only, no rate limit on that route), but the
*preview* endpoint (`GET /auth/invites/{token}`, rate-limited) 500'd on
a perfectly valid token. Confirmed with `curl` that a genuinely
successful `/auth/register` had the identical crash -- meaning this bug
reached `main` in Phase 12 and would have broken real registration and
login on first successful use, not just the new invite routes. Fixed:
`headers_enabled=False`. New regression test,
`test_a_successful_request_through_a_rate_limited_route_does_not_crash`,
drives a real `/auth/register` call through the enabled limiter and
asserts `201`, specifically so this exact mistake can't silently come
back. The `Retry-After` header claim in Phase 12's entry above no
longer holds -- `headers_enabled=False` means 429 responses no longer
carry rate-limit headers at all; that assertion was removed from
`test_sixth_login_attempt_within_a_minute_is_rate_limited` rather than
left to bit-rot.

**Verified, not asserted:** `ruff check`, `mypy`, and the full suite --
147 tests, 0 skipped -- all clean against the real local Postgres.
Manually verified the complete invite flow in a real browser end to
end against the running dev server: created an invite as the OWNER,
confirmed it in the pending list, opened the accept link as a separate
identity, confirmed the preview showed the correct tenant/role/email,
accepted it (auto-login worked), confirmed the new OPERATOR account
correctly got a 403 on `tenant.admin`-gated routes (RBAC applied
correctly to an invite-created user, not just a registered one),
confirmed the OWNER's own Team page then showed 2 real members, and
confirmed re-inviting the same now-registered email correctly 409'd
without clearing the form. The `headers_enabled` bug above was caught
during exactly this manual pass, not invented after the fact.

### Phase 14 -- GitHub Issue notifications for pending approvals

Closes a real UX gap, not from the audit list but found by thinking
about the actual approval flow: a sensitive action landing in Approvals
had no signal at all beyond someone happening to have the dashboard
open. Considered Slack first, chose GitHub instead -- the platform
already has a real, per-tenant GitHub OAuth credential connected for
`close_issue`/`list_repos`; reusing it needs no new secret, no new
dependency, no new OAuth grant, unlike Slack (a new app + webhook) or
email (a new provider integration, no existing code to build on at
all).

New `apps/api/services/notifications.py`: `notify_pending_approval`
opens a GitHub Issue (title/body: action, risk level, requester) in a
tenant-configured `notification_repo` when a sensitive action creates a
PENDING `ApprovalRequest`; `close_notification_issue` comments the
outcome and closes it when the request is resolved. Deliberately NOT
routed through `agentguard.guardrail.AgentGuard.call()` -- gating a
notification *about* a pending approval behind another approval would
be circular; this talks to PyGithub directly, same as
`connectors/github/connector.py` does. Both functions swallow every
exception on purpose (a GitHub outage, revoked token, or missing repo
must never block the real thing) and are called best-effort from
`apps/api/routers/connectors/general.py` and `apps/api/routers/approvals.py`.

Schema: `Tenant.notification_repo` (nullable, admin-configured, "owner/repo")
and `ApprovalRequest.notification_repo`/`notification_issue_number`
(migration `fae57e4eeed1`) -- the issue's repo/number is snapshotted
onto the approval at creation time, not read live from the tenant
setting, so resolving it later still closes the right issue even if
the setting has since changed. New `GET`/`PATCH /tenant/settings`
(`tenant.admin`-gated, format-validated). Dashboard: `IntegrationsPage.tsx`
gained a "Bildirimler" card (OWNER/ADMIN only, same client-side-hint
pattern as `TeamPage.tsx`'s invite button) to set the repo.

**Verified, not asserted, including a real failure case a mock
couldn't have produced:** set a real (but nonexistent) repo as
`notification_repo` for an account with a genuinely connected GitHub
credential, then triggered a real sensitive Gmail action from the
running dev server. The server log shows a real `github.GithubException.
UnknownObjectException: 404` from api.github.com -- and
`POST /connectors/{id}/execute` still returned `200 OK`, proving the
best-effort isolation actually holds against a real upstream failure,
not a simulated one. Approving that same request afterward returned a
clean `200` with no exception, confirming `close_notification_issue`
correctly no-ops when no issue was ever created. New
`tests/test_infra_api_notifications.py` (7 tests) covers `GET`/`PATCH
/tenant/settings` (round-trip, format validation, RBAC) and both
network-free skip paths (no `notification_repo` configured; configured
but no GitHub credential exists) by reading the `ApprovalRequest` row
directly from the database. `ruff check`, `mypy`, and the full suite --
154 tests, 0 skipped -- all clean against the real local Postgres.

### Phase 15 -- refresh tokens moved to an httpOnly cookie

Closes the last Tier-1 item from Phase 11's audit that was actually a
coding task: `apps/web/src/api/client.ts` used to read and write the
refresh token to `localStorage`, a documented (not hidden) v1 gap --
readable by any script on the page, so an XSS payload running on any
later page load could exfiltrate it. Not anymore: `tokenStorage` no
longer has a `getRefreshToken`/`setTokens` for the refresh half at all,
only `access_token` (short-lived, 30 min, the normal tradeoff for the
one thing every request actually needs).

`apps/api/routers/auth.py`'s `/register`, `/login`, `/refresh`, and
`/accept-invite` now set the refresh token as an `HttpOnly`,
`SameSite=lax`, `Path=/auth` cookie (`COOKIE_NAME`) on every response
that issues a token pair -- new `settings.cookie_secure` (default
`false`, dev-only; any real HTTPS deployment must set it `true` or
browsers refuse to set the cookie at all) controls the `Secure`
attribute. Deliberately NOT a breaking change for
`apps/mcp_server/server.py`: the JSON body still carries the real
`refresh_token` value too (a non-browser client has no cookie jar of
its own and legitimately needs it), and `/auth/refresh`/`/auth/logout`
accept the token from either source -- **body first, cookie as
fallback**, not the other way around. That priority order isn't
arbitrary: this session's own test suite (`httpx.AsyncClient`'s cookie
jar persists automatically across calls on the same client, exactly
like a browser) surfaced a real bug while writing the tests --
"cookie always wins" silently substituted a freshly-rotated cookie for
a deliberately-replayed OLD token a test was checking gets rejected,
defeating the actual assertion. Fixed before it shipped; a real
browser never sends a body at all, so the priority never matters there
-- `test_explicit_body_token_takes_priority_over_a_stale_cookie` in
the new test file is the regression guard.

**Verified, not asserted, end to end in a real browser against the
running dev server**, not just via the automated suite: logged in
through the real form (reset the seeded test account's password first,
since this session had only ever minted JWTs directly), confirmed
`localStorage` held only the access token and `document.cookie` was
empty (the correct outcome for an httpOnly cookie -- JS genuinely
cannot see it, which is the entire point), then manually corrupted the
stored access token and navigated to a protected page: the app
recovered silently, proving the browser sent the httpOnly cookie
automatically and `/auth/refresh` issued a real new access token
without me supplying anything. Logging out redirected to `/login`,
cleared the access token, and a direct follow-up call to
`/auth/refresh` correctly came back `401` -- the cookie was genuinely
gone server-side, not just hidden from the page. New
`tests/test_infra_api_refresh_cookie.py` (6 tests): the cookie's exact
attributes, refresh succeeding from the cookie alone with an empty
body, logout clearing it (`Max-Age=0`), the MCP-server-style body-only
path still working with zero cookie involvement, and the priority
regression guard above. `ruff check`, `mypy`, and the full suite --
160 tests, 0 skipped -- all clean against the real local Postgres.

### Phase 16 -- admin-initiated password reset

Closes another gap Phase 11's audit flagged: a locked-out teammate had
no way back into their account short of an OWNER manually editing the
database. Same shape as Phase 13's invite flow, deliberately: `POST
/tenant/members/{user_id}/reset-password` (`RequireTenantAdmin`) mints
a `PasswordReset` row and returns the raw one-time token exactly once
-- only its sha256 hash is stored (`token_hash`, unique+indexed). The
admin copies the link from the Team page and sends it to the teammate
however they currently do that (no real email delivery yet -- same
documented gap as invites). `GET /auth/password-reset/{token}` previews
the target email with no auth (the teammate doesn't have a valid
session to prove who they are); `POST /auth/reset-password` consumes
the token, hashes the new password, marks the token used, revokes
**every** existing refresh token for that user (a compromised or
forgotten-about session shouldn't survive a password reset), and logs
the teammate straight in via the same `_issue_token_pair` helper every
other auth endpoint uses.

Same OWNER-exclusion reasoning as `INVITABLE_ROLES` (Phase 13): a mere
ADMIN forcing a reset on the OWNER's account would be a real
privilege-escalation path (reset it, log in as them), not just an
inconvenience -- `target_is_owner and not requester_is_owner` blocks it
with a 403, the same pattern as invites blocking ADMIN from minting a
peer OWNER.

**Deliberately not self-service.** A real "forgot password" flow needs
an out-of-band channel to prove "the person on this device is actually
the account owner" *before* they're authenticated -- at minimum, real
email delivery, which this project doesn't have yet. A solo OWNER
locked out of their own only account still has no one to ask; that gap
is stated here, not hidden, and tracked in
`docs/ROADMAP_TO_PRODUCTION.md` alongside the same gap for invites.

Frontend: `apps/web/src/pages/ResetPasswordPage.tsx` mirrors
`AcceptInvitePage.tsx`'s structure closely (preview the email before
asking for a new password, lazy `useState` initializer for the
"no token in URL" case rather than setting state inside an effect --
same oxlint fix Phase 13 already established). `TeamPage.tsx` gained a
"Şifreyi sıfırla" action per member row (admin-only, same client-side
`canInvite` hint as the invite button -- real enforcement is still
`RequireTenantAdmin`/the OWNER check server-side) that shows the
returned link in the same copyable-box pattern already built for
invites.

New `tests/test_infra_api_password_reset.py` (11 tests): RBAC and
tenant-isolation on the trigger endpoint, the OWNER-protection 403, the
preview endpoint, and the consume endpoint -- successful reset +
login with the new password, the old password rejected afterward, a
token can't be reused, garbage tokens rejected, and (the one that
matters most) a refresh token minted *before* the reset is confirmed
`401` on `/auth/refresh` *after* it.

**Verified, not asserted:** all 11 new tests pass against the real
local Postgres; full suite -- 171 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean across `apps`/`infrastructure`; frontend
`tsc -b`/`oxlint`/`vite build` all clean. Then the entire flow live
against the running dev server and a real Postgres-backed account: as
the seeded OWNER, triggered a reset for a real OPERATOR teammate,
copied the real link, opened it in a second tab, confirmed the preview
showed the correct email, set a new password, confirmed the app
auto-logged the teammate in (landing on the dashboard, correctly
getting a real `403` from `/tenant/members` since an OPERATOR isn't a
tenant admin -- proving this was a genuine new session, not a stale
owner one), then confirmed directly against the API that the new
password logs in (`200`) and the old one no longer does (`401`). The
second tab sharing `localStorage` with the first (same origin) briefly
overwrote the OWNER's own session in tab one -- the same test-harness
artifact Phase 13's invite verification hit, not a product bug -- fixed
by re-minting a fresh OWNER token for that tab afterward.

### Phase 17 -- Policy Engine (per-tenant ALLOW/DENY/REQUIRE_APPROVAL)

`docs/PRODUCTIZATION_ROADMAP.md`'s Phase A -- written the same day, after
an outside reviewer's assessment was checked against the actual repo
and confirmed as the one genuinely open #1 gap: every tenant ran under
the exact same hardcoded `RISK_LEVELS`/`Policy.sensitive_actions`
table (`connectors/gmail/policy.py`, `connectors/github/policy.py`),
with no way for a tenant to say "we don't need approval for this" or
"block this outright for us," short of editing code.

New `PolicyRule` (`infrastructure/database/models/policy_rule.py`):
`(tenant_id, connector_type, action) -> ALLOW | DENY | REQUIRE_APPROVAL`,
unique per tenant+connector+action. Deliberately bounded, not a general
rule engine yet: a rule can only target an action already in the
connector's own `Policy.task_scope` -- that scope is a structural
capability ceiling ("can this connector do this at all"), not a
business rule, and no tenant override can widen it. Within scope, a
rule can tighten the system default (DENY something normally allowed,
or DENY something that normally needs approval, which never even
creates an `ApprovalRequest` for it to be pending against) or loosen it
(ALLOW something the system default gates behind approval).

`apps/api/routers/policies.py` -- `GET /tenant/policies` (effective
policy for every action across every connector the platform can
execute against, merging any tenant override with the system default so
the dashboard never re-derives the merge itself), `PUT
/tenant/policies/{connector_type}/{action}` (upsert, not
create-or-409 -- an admin editing a rule twice should just update it),
`DELETE .../{connector_type}/{action}` (clears the override, reverting
to system default; idempotent -- clearing a rule that was never set is
a 204, not a 404). All three `RequireTenantAdmin` -- a mere OPERATOR
loosening their own agent's approval requirement would defeat the
point, same reasoning as every other tenant-admin-gated endpoint this
project has.

`apps/api/routers/connectors/general.py`'s `execute_connector_action`
now looks up a `PolicyRule` for the exact `(tenant_id, connector_type,
action)` after the existing `task_scope` check and before deciding
approve-vs-execute-immediately: no rule found -> unchanged behavior
(the connector's built-in `Policy.requires_approval()`); a rule found
-> its decision wins, `DENY` returning 403 without ever touching
`ApprovalRequest` or the connector at all.

Frontend: new `/policies` page and nav entry -- one card per connector,
one row per action, a select per row (system default plus the three
explicit decisions) with a "Özel kural" badge whenever a tenant
override is active, matching Team/Integrations' existing visual
language.

`tests/test_infra_api_policies.py` (new, 16 tests): the CRUD endpoints
(RBAC, tenant isolation, action-outside-scope validation, upsert
semantics, idempotent clear), and four tests proving the override
actually changes execution behavior -- DENY blocking a normally
auto-allowed action, DENY blocking a normally approval-gated one
(and confirming no `ApprovalRequest` gets created for it), REQUIRE_APPROVAL
gating a normally auto-allowed action, and ALLOW skipping the approval
gate entirely for a normally-gated one. That last test can't complete a
real GitHub call with this suite's throwaway fake credential (same
limitation `test_infra_api_connectors_approvals.py`'s own module
docstring already states), so it asserts the real, honest outcome of
attempting one -- a `502` from the connector layer failing to
authenticate -- rather than faking a `200`; what it proves is narrower
but real: the override routed the call to the immediate-execution path
at all, instead of creating a pending approval. A fifth test confirms
an ALLOW override set in one tenant has zero effect on another tenant's
identical connector/action.

**Verified, not asserted:** all 16 new tests pass against the real
local Postgres; full suite -- 187 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean across `apps`/`infrastructure`; frontend
`tsc -b`/`oxlint`/`vite build` all clean. Then live against the running
dev server and dashboard: as the seeded OWNER, opened `/policies`,
confirmed it listed the real system defaults for both connectors
(`list_messages`/`read_message` for Gmail, `close_issue`/`list_repos`
for GitHub) with no overrides, set GitHub's `close_issue` to `DENY`
through the actual `<select>`, confirmed the real `PUT` request, the
"Özel kural" badge appearing, and the row persisting across a reload;
then reset it back to system default through the same dropdown and
confirmed the real `DELETE` request and the badge disappearing.

### Phase 18 -- Agent Identity (Phase B of the productization roadmap)

`docs/PRODUCTIZATION_ROADMAP.md`'s Phase B. Before this, every executed
action was attributed to whichever human's JWT happened to call the
API -- `apps/mcp_server/server.py`'s own module docstring already
named a real service-account/API-key mechanism as future work. "Which
agent did this" could only ever answer with a person's name.

New `Agent` model (`infrastructure/database/models/agent.py`):
`tenant_id`, `name`, `owner_user_id` (whose RBAC permissions the agent
runs under -- an Agent deliberately does NOT get its own independent
role set this phase; see the model's own docstring for why that's a
named, tracked gap rather than a silent assumption), `api_key_hash`.
`POST /tenant/agents` (`apps/api/routers/agents.py`, admin-only) mints
one and returns the raw key (`agk_...` prefix) exactly once -- same
one-time-reveal-then-hash pattern as invites and password resets.

New `apps.api.dependencies.Actor`/`get_current_actor`/
`require_actor_permission`: a second, narrower auth entry point used
only by `POST /connectors/{id}/execute` (the one route an Agent, not
just a human, needs to call directly) -- recognizes the `agk_` prefix
and, if valid and not revoked, resolves to the agent's owner for
permission checks. Every other route in the codebase keeps depending on
the original `get_current_user`/`require_permission`, completely
untouched, so this carried zero risk to anything that didn't
explicitly opt in.

`PolicyRule` (Phase A) gained an optional `agent_id`: NULL is the
existing tenant-wide rule, unchanged; set, it applies to one specific
Agent and wins over the tenant-wide rule for the same
`(connector_type, action)` -- resolution order is agent-scoped >
tenant-wide > connector's own built-in default, evaluated in
`execute_connector_action` and mirrored in `agents.py`'s
`list_agent_policies` ("what can this agent do," the roadmap's own
named signature feature -- genuinely cheap to build now that Phase A's
resolution logic and this phase's agent-scoped rows both exist).
Because Postgres treats NULL != NULL, the old single `UniqueConstraint`
couldn't enforce "at most one tenant-wide rule" once agent-scoped rows
shared the table -- replaced with two partial unique indexes (same
fix already used for system roles, `infrastructure/database/models/rbac.py`).

`ApprovalRequest` gained `agent_id` (nullable, `SET NULL`) --
`requested_by_user_id` stays populated either way (the agent's owner,
or the calling human directly), so "who's accountable" always has an
answer even when "which agent" doesn't. Surfaced as `agent_name` in
`GET /approvals` and shown as a badge in the dashboard's Approvals list
and detail panel.

`apps/mcp_server/server.py`: `AGENTGUARD_AGENT_KEY` is now the
documented, preferred way to run it -- no login or refresh dance at
all in that mode, the key itself is the permanent bearer token until
revoked. `AGENTGUARD_EMAIL`/`AGENTGUARD_PASSWORD` still works exactly
as before as a fallback; nothing about that path changed.

New `apps/web/src/pages/AgentsPage.tsx` (`/agents`): create/revoke, and
an expandable per-agent policy table (tenant rule vs. this agent's
override, editable inline).

`tests/test_infra_api_agents.py` (new, 15 tests): agent CRUD and RBAC,
owner defaulting/cross-tenant-owner rejection, a revoked or garbage
agent key both correctly 401 on execute, a human's own execute still
carries no agent attribution, agent-scoped override beating tenant-wide
(and *not* leaking to a second agent with no override of its own), and
-- matching Phase 17's own honest-outcome discipline for the one
scenario a throwaway fake credential can't complete for real -- an
agent-scoped ALLOW genuinely routing execution past the approval gate,
asserted via the real `502` a fake credential's real connector-layer
failure produces, not a faked `200`.

**Verified, not asserted:** all 15 new tests pass against the real
local Postgres; full suite -- 202 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean across `apps`/`infrastructure`; frontend
`tsc -b`/`oxlint`/`vite build` all clean. Then live against the running
dev server and dashboard, using this tenant's real, already-connected
GitHub credential (not a throwaway fake one, so real destructive calls
were deliberately avoided -- see below): created a real Agent
("SupportBot") from the dashboard, got a real `agk_...` key, expanded
its policy panel and set an agent-scoped override, confirmed the "Bu
ajana özel" badge and the real `PUT`. Deliberately did NOT exercise an
agent-scoped `ALLOW` against this real credential live (that would
immediately attempt a real `close_issue` against a real repository with
no human checkpoint at all -- correctly proven safe already, against a
fake credential, by the automated suite); instead set a
**REQUIRE_APPROVAL** override on the normally-auto-allowed `list_repos`
action, called `POST /connectors/{id}/execute` directly with the raw
agent key, and confirmed it came back `pending_approval` instead of
auto-listing -- proving the agent-scoped override genuinely intercepts
execution for a real credential too, without touching anything
destructive to prove it. The resulting approval showed
`agent_name: "SupportBot"` in both the raw API response and the
dashboard's Approvals list/detail badge, confirming attribution end to
end. Resolved (denied) that approval directly via the API afterward
-- `list_repos` is read-only, so nothing GitHub-side needed cleanup.

### Phase 19 -- Audit Trail (Phase C of the productization roadmap)

`docs/PRODUCTIZATION_ROADMAP.md`'s Phase C. Before this, `ApprovalRequest`
was the only record of anything `POST /connectors/{id}/execute` did, and
it only ever existed for the REQUIRE_APPROVAL path -- an auto-ALLOWed
action (the common case; most actions aren't sensitive) ran and left
zero trace anywhere, and a DENY (whether from task scope or a Phase A/B
policy rule) wasn't recorded at all either.

New `AuditEvent` (`infrastructure/database/models/audit_event.py`):
one row per `execute` call, whichever of the three decisions it took.
`decision_source` answers the "why" Phase A's own writeup named as a
real gap it deliberately wasn't closing yet -- `TASK_SCOPE` (the
connector doesn't support this action at all), `AGENT_POLICY` /
`TENANT_POLICY` (a Phase A/B `PolicyRule` matched), or `SYSTEM_DEFAULT`
(the connector's own built-in `Policy.sensitive_actions` decided it).
For a `REQUIRE_APPROVAL` row, `approval_id` points at the
`ApprovalRequest` that carries the eventual outcome -- deliberately not
duplicated onto this row too (`GET /tenant/audit`,
`apps/api/routers/audit.py`, resolves it live via a join instead), so
there's exactly one place that state can drift out of sync: none. For
an `ALLOW` row, `result_json`/`error_message` are populated directly
here, since Phase 7 made that execution synchronous -- this row is the
only record of it that will ever exist.

**A real gap found and fixed by this phase's own tests, not by
inspection:** `execute_connector_action`'s ALLOW-path exception
handling only caught `(PermissionDenied, ConnectorQuarantined,
ConnectorError)` -- any other exception type a connector's underlying
client library raises (found live: Gmail's real
`google.auth.exceptions.RefreshError`, reached with a throwaway test
credential, which `connectors/gmail/connector.py` never wraps in
`ConnectorError`) would skip the new audit-write entirely and surface
as a raw 500, silently defeating this phase's entire premise for
exactly the failures most worth auditing. Broadened to `except
Exception`: the three known types still translate to their specific
HTTP status (403/423/502) after being recorded; anything else is
recorded too, then re-raised unchanged (a real 500, not silently
swallowed) rather than mis-translated into a status it doesn't mean.

New `GET /tenant/audit` (filters: `connector_type`, `action`,
`decision`, `agent_id`), gated behind `approval.read` -- the same
permission that already governs seeing `ApprovalRequest.call_context`,
since an `AuditEvent` carries the identical informational-only content.
New `apps/web/src/pages/AuditPage.tsx` (`/audit`): a filterable table,
one row per decision, with a plain-language outcome summary (the
linked approval's live status for a `REQUIRE_APPROVAL` row; "Başarıyla
çalıştı" / the real error for an `ALLOW` row; "Çalıştırılmadı" for a
`DENY` row).

**Deliberately not built this phase, named rather than silently
dropped:** the Policy Simulator this document's Phase C sketch named as
a signature feature ("if this rule had existed for the last 7 days, N
actions would have been ALLOW/REQUIRE_APPROVAL/DENY") -- it needs this
phase's event log to exist first, which it now does, but building the
simulation itself is real, separate work, not a marginal addition the
way "what can this agent do" turned out to be in Phase B. Tracked in
`docs/PRODUCTIZATION_ROADMAP.md` rather than assumed done.

`tests/test_infra_api_audit.py` (new, 9 tests): every decision path
gets its own row with the correct `decision_source` (including the
`AGENT_POLICY` case, end to end through a real agent key), a
`REQUIRE_APPROVAL` row's `approval_status` correctly reads "PENDING"
before resolution and the real resolved status after, connector/decision/
agent filters, and tenant isolation. One test
(`test_filter_by_connector_type`) deliberately exercises the exact
unwrapped-Gmail-exception scenario that found the exception-handling gap
above -- a real regression guard, not a synthetic one.

**Verified, not asserted:** all 9 new tests pass against the real local
Postgres; full suite -- 211 tests, 0 skipped -- still green; `ruff
check`/`mypy` clean across `apps`/`infrastructure`; frontend `tsc -b`/
`oxlint`/`vite build` all clean. Then live against the running dev
server and dashboard, using this tenant's real, already-connected
credentials rather than fakes: ran a real `list_messages` against the
real Gmail credential (read-only, no side effects) and confirmed it
appeared on `/audit` correctly labeled ALLOW / Sistem varsayılanı /
"Başarıyla çalıştı"; set a real tenant-wide DENY on GitHub's
`list_repos`, confirmed the real 403, confirmed the resulting audit row
read DENY / Workspace kuralı / "Çalıştırılmadı," then immediately
cleared that policy override via the API so it didn't linger; confirmed
the dashboard's connector-type filter dropdown correctly narrowed the
list to just the Gmail row.

### Phase 20 -- Python SDK (Phase D of the productization roadmap)

`docs/PRODUCTIZATION_ROADMAP.md`'s Phase D. Before this, the only way
code outside this repo could drive AgentGuard was `apps/mcp_server`
(MCP-protocol-specific) or hand-rolled HTTP calls -- no installable
library for a raw agent loop, or a framework's own tool-calling
convention, to call directly.

New `sdk/python/agentguard_sdk` -- a real, separately-installable
package (`sdk/python/pyproject.toml`, its own name `agentguard-sdk`)
with **zero import-time dependency on this monorepo's own
`agentguard`/`apps`/`infrastructure` packages**, only `httpx`. Two
methods: `list_connectors()` and `run(connector_id, action, params)`,
the latter mirroring `apps/mcp_server/server.py`'s own
`execute_connector_action` tool almost exactly, on purpose --
POST /connectors/{id}/execute, and if the platform comes back
`pending_approval` (Phase 7), poll `GET /approvals/{id}` client-side
until a human resolves it, then return the real result or raise
`AgentGuardDenied`/`AgentGuardTimeout`/`AgentGuardError`. Authenticates
as an Agent (Phase B) only -- deliberately no human-login fallback the
way `apps/mcp_server` still keeps one; a library meant to be embedded
in someone else's agent framework code shouldn't hold a human's
password at all.

**Two real, pre-existing gaps this phase's own tests found, not
predicted by anything in this document -- both fixed:** `GET
/connectors` and `GET /approvals`/`GET /approvals/{id}` were still
`get_current_user`/`require_permission`-only (human JWT), never
extended to accept an Agent's key the way Phase B's
`execute_connector_action` was. In practice this meant an Agent
(including `apps/mcp_server` running with `AGENTGUARD_AGENT_KEY`, not
just this new SDK) could create a pending approval via `execute()` but
could not discover its own connectors first, nor poll for its own
request's outcome -- both routes 401'd on a real agent key, discovered
the moment this phase's own tests tried exactly that real flow.
`apps/api/routers/connectors/general.py`'s `list_connectors` and
`apps/api/routers/approvals.py`'s `list_approvals`/`get_approval` are
now Actor-based (`apps.api.dependencies.require_actor_permission`),
matching `execute_connector_action`'s own pattern; `revoke_connector`
and `resolve_approval` deliberately stay human-only -- same reasoning
in both cases: an agent discovering/polling its own things is a read
it should be able to do itself, revoking a connector or resolving an
approval is an admin/human decision, not something an agent calls on
itself.

New `sdk/python/examples/basic_agent_loop.py` -- a real, runnable,
framework-agnostic example (deliberately not LangGraph/CrewAI-specific;
see the package's own README "What this is not (yet)" section for why
claiming a specific framework's support needs a real example proving
it, which this phase doesn't build). `tests/test_sdk_client.py` (new,
5 tests, run against the real platform app via `httpx.ASGITransport`
-- construction validation, `list_connectors()` against real data, a
policy-driven `DENY` surfacing as `AgentGuardError`, an unknown
credential surfacing as `AgentGuardError`, and (the one that caught the
`GET /approvals/{id}` gap) a real concurrent human denial while `run()`
is mid-poll correctly raising `AgentGuardDenied`).

**Deliberately not built this phase, named rather than silently
dropped:** a TypeScript SDK (`@agentguard/sdk`) -- this document
sketched both from the start; only the Python one shipped. A sync
Python client -- async-only, matching the rest of this platform, until
a real caller needs otherwise. Framework-specific examples/wrappers
(LangGraph, CrewAI, OpenAI Agents) -- `run()` is a plain async function
any framework's own tool-calling convention wraps directly, but this
phase doesn't claim explicit support for any one of them without a
real, runnable example proving it first.

**Verified, not asserted:** all 5 new SDK tests plus the connector/
approval tests affected by the auth fix pass against the real local
Postgres; full suite -- 216 tests, 0 skipped -- still green; `ruff
check`/`mypy` clean across `apps`/`infrastructure` *and* the standalone
`sdk/python` package (its own, separate `pyproject.toml`/lint config,
checked on its own). Then live end to end, genuinely outside this
monorepo's own test harness: minted a real Agent key from the running
platform via a direct API call, ran
`AGENTGUARD_AGENT_KEY=agk_... python sdk/python/examples/basic_agent_loop.py`
as an actual separate process against the real Gmail connector
(`list_messages`, read-only), got the real message list back, and
confirmed the resulting `AuditEvent` (Phase C) showed up correctly on
the dashboard's `/audit` page labeled with the real agent's name --
proving the full chain (standalone SDK package -> real HTTP call ->
real policy resolution -> real Gmail execution -> real audit record ->
real dashboard) end to end, not just through this repo's own test
runner.

### Phase 21 -- Agent credential scoping (closing Phase B's original sketch)

Phase B's own writeup (docs/PRODUCTIZATION_ROADMAP.md) named "its own
scoped credential grants (which connectors, which actions)" as the
original design; "which actions" shipped there (`PolicyRule.agent_id`),
"which connectors" was deliberately deferred and simplified away, with
the honest note that an Agent's effective access was its owner's full
set, narrowed only by policy. Skipping the originally-planned Phase E
(third connector + developer experience -- explicitly deferred, not
dropped) to close that gap instead, since it's fully self-contained
(no external provider/account decision the way real email delivery
needs) and directly named as real, tracked debt.

New `AgentCredentialGrant`
(`infrastructure/database/models/agent_credential_grant.py`):
`(agent_id, credential_id)`, deny-by-default -- matching this project's
own established RBAC philosophy (`apps/api/dependencies.py`'s own
docstring: "a user with zero roles... is rejected -- there is no
implicit 'everyone can' fallback"). An Agent now needs an explicit
grant for a specific Credential before touching it at all, checked in
`execute_connector_action` ahead of `task_scope` -- the same
structural-ceiling position, since "can this agent touch this
credential" is more fundamental than "is this action in scope."
**Real, intentional breaking change for any Agent created in Phases
B-D**: it could act on any credential its owner could see; after this
phase it can act on none until explicitly granted. Deliberately not
backfilled for already-existing agents (including this session's own
demo ones) -- the honest way to prove deny-by-default actually holds,
not worked around.

`apps/api/routers/agents.py` gained `GET/PUT/DELETE
/tenant/agents/{id}/connectors[/{credential_id}]` (RequireTenantAdmin);
new `/agents` dashboard section (toggle per connector, alongside the
existing per-action policy panel). New `AuditEvent.decision_source`
value `"AGENT_NOT_GRANTED"` for this specific denial, distinct from
`TASK_SCOPE`/policy-driven ones.

`tests/test_infra_api_agents.py` gained 7 new tests (grant/revoke round
trip, idempotent re-grant, cross-tenant credential grant is 404, RBAC,
new-agent-starts-with-nothing-granted, the denial is audited with the
right source) and every existing test that authenticates as an agent
to reach real policy/execution logic was updated to grant first --
the same breaking change described above, applied to this session's
own test suite.

**Verified, not asserted:** all 7 new tests plus every agent-key test
across `test_infra_api_agents.py`/`test_infra_api_audit.py`/
`test_sdk_client.py` pass against the real local Postgres; full suite
-- 223 tests, 0 skipped -- still green; `ruff check`/`mypy` clean.
Then live against the running dashboard and this tenant's real,
already-connected credentials: expanded the existing "SDK Live Test
Agent" (from Phase 20's live verification, genuinely pre-dating this
phase) and confirmed its Connector erişimi panel correctly showed both
GitHub and Gmail as ungranted -- proving the breaking change really
took effect on a real, previously-working agent. Granted it GitHub
only, then called execute directly against both credentials with its
real key: GitHub reached the real connector layer (a real 401 "Bad
credentials" from api.github.com -- that stored OAuth token has since
expired, unrelated to this phase, but proof the grant gate let it
through); Gmail correctly 403'd with the new message, and the
resulting `/audit` row read "Ajana connector erişimi verilmemiş"
(`AGENT_NOT_GRANTED`) exactly as intended.

### Phase 22 -- internal security review of the Phase 12-21 surface, one real finding fixed

Not the independent, outside, third-party review `docs/PRODUCTIZATION_ROADMAP.md`
and `README.md`'s own Roadmap both still correctly list as **Not
started** -- that gap is real and this did not close it. This was a
manual, adversarial self-review of everything added since Phase 12
(rate limiting through agent credential scoping), the same "second
internal pass, honestly labeled as exactly that" shape as Phase 7's own
review. `docs/ROADMAP_TO_PRODUCTION.md`'s own section 2 says it best:
"Reviewer and author sharing the same blind spots is exactly the
failure mode an independent audit exists to catch" -- still true here,
named rather than glossed over.

**Confirmed finding, fixed:** `POST /tenant/agents` let any
`tenant.admin` holder -- including a mere ADMIN, not just OWNER --
create an Agent with `owner_user_id` set to *any* other tenant member,
including the OWNER, with zero role-based restriction. Since an
Agent's effective permissions are its owner's in full
(`infrastructure/database/models/agent.py`), this let a mere ADMIN
mint itself a credential that authenticates as the OWNER for every
Actor-gated route -- and, more immediately, silently misattributes
every resulting `ApprovalRequest`/`AuditEvent` to the OWNER
(`requested_by_email`/`user_email`), with no way for the OWNER (or
anyone) to see who *actually* created the agent from the dashboard --
`AgentResponse` never exposed a creator, only an owner. Exactly the
same privilege-escalation shape this project has already defended
against twice (`INVITABLE_ROLES` excluding OWNER from invites;
`reset_member_password`'s `target_is_owner`/`requester_is_owner`
check, both `apps/api/routers/tenant.py`) -- missed here because Agent
creation shipped in a different phase (B) than either of those.

Fixed with the identical pattern: `target_is_owner and not
requester_is_owner` -> 403. Also added `AgentResponse.created_by_email`
(previously tracked in the database but never surfaced anywhere) as a
defense-in-depth visibility fix -- an owner shown on the dashboard can
now see who actually created an agent "owned by" them, not just take
the label on faith; the dashboard's Agents page now shows "· Oluşturan:
..." whenever it differs from the owner. 4 new tests (a mere ADMIN
creating an agent for the OWNER is 403; an OWNER creating one for a
mere ADMIN is fine -- the exclusion is specifically about minting *as*
the OWNER, not cross-user creation in general; the creator is visible
and correct when it differs from the owner; existing self-owned-agent
test extended to check `created_by_email` too).

**Two lower-severity observations, named rather than fixed this
phase:**
- `AuditEvent.error_message` stores an exception's `str()` verbatim,
  visible to anyone with `approval.read`. This is the exact same
  already-documented gap Phase 7 named for
  `ApprovalRequest.call_context` ("stored and shown in plain text...
  would need redaction/classification before this is safe to widen"),
  just extended to a new field this session added. Not exploitable
  today (PyGithub/Google API exceptions observed in this session's own
  live testing don't leak secrets), but no redaction exists if a
  future connector's exceptions did.
- No rate limiting on Actor-authenticated routes (`execute`,
  `list_connectors`, `list_approvals`) or the agent-management
  endpoints. Agent keys are 256-bit random tokens -- brute force isn't
  the concern -- but a compromised agent key holder could spam real
  downstream API calls or flood the approval queue. Operational/DoS
  territory (`README.md`'s Tier 3 "Monitoring, alerting" already names
  this class of gap), not an auth bypass.

**Verified, not asserted:** the 4 new tests pass against the real
local Postgres; full suite -- 226 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean; frontend `tsc -b`/`oxlint`/`vite build` all
clean.

### Phase 23 -- Policy Simulator

`docs/PRODUCTIZATION_ROADMAP.md`'s Phase C sketch named this as a
signature feature and deliberately didn't build it there ("needs Phase
A's rules and Phase C's event log to both exist first... building the
simulation itself is real, separate work"). Both existed as of Phase
21; this closes it.

New `GET /tenant/policies/{connector_type}/{action}/simulate` and its
agent-scoped twin `GET /tenant/agents/{id}/policies/{connector_type}/{action}/simulate`
(sharing `_simulate` in `apps/api/routers/policies.py`) answer the real
question an admin has *before* clicking Save on a policy change: of
the real `AuditEvent` history for this exact `(connector_type, action)`
in the last N days (default 7), how many events would have landed
differently under each of the three possible decisions. Deliberately
scoped to what a `PolicyRule` actually affects -- a rule maps one
`(connector_type, action[, agent_id])` to exactly one decision, so "if
this rule had existed" is arithmetic over the real historical
breakdown (`total - count(that decision)`), not a full re-simulation of
the guardrail against every action type. New `/policies` and `/agents`
dashboard "Simüle et" buttons expand an inline preview -- shared
`PolicySimulationResult` component, since the tenant-wide and
agent-scoped views render the identical shape.

`tests/test_infra_api_policy_simulator.py` (new, 11 tests): the
arithmetic itself against a real mix of ALLOW/DENY history (the one
that actually matters -- proves `would_change_if` isn't just
`total_events` for every decision), tenant isolation, action-not-
bleeding-into-a-different-action's count, a real `days=` window test
that backdates a real `AuditEvent` directly (no time-travel endpoint
exists, so this is the only way to prove the filter for real), the
agent-scoped variant only counting that agent's own history, and RBAC.

**Verified, not asserted:** all 11 new tests pass against the real
local Postgres; full suite -- 237 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean; frontend `tsc -b`/`oxlint`/`vite build` all
clean. Then live against the running dashboard, against this session's
own genuinely real, pre-existing `AuditEvent` history (not seeded for
this test): `/policies`' `gmail.list_messages` row correctly reported
"Son 7 günde 3 gerçek aksiyon: 2 İzin ver, 1 Engelle" with correct
would-change counts for each candidate decision; the agent-scoped
simulate for "SDK Live Test Agent" on the same action correctly
reported a smaller, different subset (2 events, that agent's own
history only) -- proving the agent filter narrows the real data, not
just accepts the parameter.

### Phase 24 -- TypeScript SDK

`sdk/typescript/` -- a second, genuinely standalone SDK package
(`@agentguard/sdk`, own `package.json`, zero runtime dependencies --
native `fetch`, not axios/node-fetch) mirroring `sdk/python`'s shape
and `apps/mcp_server/server.py`'s `execute_connector_action` tool
exactly: `listConnectors()`, then `run(connectorId, action, params,
options)` which calls `/execute`, polls `/approvals/{id}` if the
result is `pending_approval`, and returns the real result or throws
one of three typed errors (`AgentGuardError` / `AgentGuardDenied` /
`AgentGuardTimeout`) -- so a Node/TypeScript agent framework (a
LangChain.js tool, a raw agent loop) gets the same guarded-call
contract the Python SDK, the MCP server, and the dashboard already
share.

Tests (`tests/client.test.ts`, vitest, 9 tests) inject a stubbed
`fetchImpl` rather than hitting a real server in-process -- Node has no
equivalent of Python's `httpx.ASGITransport` for mounting the real
FastAPI app without a socket, so a stubbed `fetch` is the honest
substitute; this package's own live run against the real platform
(below) covers what the stub can't. Covers: request shape and headers,
immediate-return, polling to APPROVED, `AgentGuardDenied`,
`AgentGuardTimeout` (both the EXPIRED-status case and the
max-wait-elapsed-while-still-PENDING case), `AgentGuardError` when
approved but execution failed, and a non-2xx initial response.

`.github/workflows/tests.yml` gained a dedicated `sdk-typescript` job
(checkout, Node 20, `npm install`, build, `oxlint`, `vitest run`) --
same reasoning as `sdk/python`'s own dedicated CI steps from Phase 20:
without a job that actually `cd`s into this directory, this package's
tests would silently never run in CI.

**Verified, not asserted:** `npx tsc -b` compiles clean; `npx oxlint
src tests examples` (scoped -- unscoped `oxlint` incorrectly walks into
`node_modules`) reports zero warnings; all 9 vitest tests pass. Then
live against the real running platform, not just the stub: started a
real `uvicorn` against the real local Postgres, minted a fresh Agent
via the real API, granted it the tenant's real GitHub connector via
Phase 21's grant endpoint, ran the compiled `dist/index.js` (not the
TypeScript source -- the same artifact a real `npm install`'d consumer
would get) as an actual separate Node process. `listConnectors()`
returned the real two connectors (GitHub, Gmail); `run("...", "github",
"list_repos")` reached the real GitHub connector and correctly threw
`AgentGuardError` wrapping a real `401 Bad credentials` from GitHub's
API (the tenant's stored OAuth token has since expired -- the same
known-stale credential Phase 21's own live check hit), proving the
grant gate and the real connector layer were both actually reached, not
mocked. Confirmed the resulting row on `GET /tenant/audit` matches
exactly: `agent_name: "TS SDK Live Test Agent"`, `connector_type:
"github"`, `action: "list_repos"`, `decision: "ALLOW"`, `error`
containing the same GitHub 401 message the SDK call raised.

Deferred, named rather than hidden: not published to npm yet (`npm
install` from `sdk/typescript/` is the only install path, same
disclaimer the Python SDK's own README carries); no framework-specific
wrapper (a LangChain.js `Tool`, a LangGraph.js node) -- `run()` is a
plain async function any framework's tool-calling convention can wrap
directly, and claiming framework support without a runnable example
proving it would be the same kind of overclaim this project has
avoided elsewhere.

### Phase 25 -- Slack connector (third connector, closes half of Phase E)

`connectors/slack/` -- the third real connector, registered in
`connectors/registry.py` alongside Gmail and GitHub (no new dispatch
mechanism needed; the registry's own docstring already named this as
the extension point). `docs/PRODUCTIZATION_ROADMAP.md`'s Phase E sketch
named Slack specifically because its risk shape is a genuine step up:
five actions across **three** policy tiers, not the two-tier LOW/HIGH
split Gmail and GitHub's first passes each used --

- `list_channels` (LOW, auto-allow) -- a safe read, same tier as
  GitHub's `list_repos`.
- `send_message` (LOW, auto-allow) -- a real mutation that's still
  auto-allowed, because it's reversible (the message itself can be
  deleted). This tier doesn't exist on Gmail or GitHub, where every
  write is HIGH/sensitive; Slack is the first connector where "this is
  a write" and "this needs a human" come apart.
- `create_channel` (MEDIUM, sensitive), `delete_message` (HIGH,
  sensitive), `invite_user` (HIGH, sensitive) -- each has a real,
  harder-to-undo consequence (persistent workspace structure, permanent
  data loss, or a real person's channel access) and is gated behind
  human approval, same mechanism as GitHub's `close_issue`.

OAuth v2 (`connectors/slack/oauth.py`) mirrors `connectors/github/oauth.py`'s
shape (no PKCE, confidential client) but normalizes a real difference:
Slack's token endpoint always returns HTTP 200, even on failure (`"ok":
false` + `"error"` instead of a non-2xx status), and nests the
workspace under `"team"` rather than returning a flat shape the way
GitHub/Google do.

`tests/test_infra_api_connectors_slack.py` (new, 8 tests): one per
policy tier (task-scope rejection, both auto-allow actions resolving
immediately rather than pending, all three sensitive actions creating
a real pending approval with the right `risk_level`), plus two proving
the Policy Simulator (Phase 23) picked up the new connector_type for
free, entirely from the registry, with zero Policy-Simulator-specific
code change.

**Deliberately simplified from Phase E's original two-part sketch:**
this closes only the "third connector" half; the "developer experience
pass" (quickstart docs, a 5-minute install path) named in the same
sketch is still open -- see Roadmap.

**Not exposed in the dashboard's test-action UI, named rather than
hidden:** `create_channel` and `invite_user` are fully implemented,
registered, policy-governed, and callable via the API/SDK/MCP server
exactly like every other action -- they just don't have a bespoke
`IntegrationsPage.tsx` test widget the way `list_channels`/
`send_message`/`delete_message` do, matching this project's existing
precedent of not building a UI widget for every single connector action
(Gmail's `list_messages` has never had one either).

**Verified, not asserted:** all 8 new tests pass against the real local
Postgres; full suite -- 245 tests, 0 skipped -- still green; `ruff
check`/`mypy` clean; frontend `tsc -b`/`oxlint`/`vite build` all clean.
Then live against the real running dashboard: inserted a real
(fake-secret) Slack `Credential` row for the session's own real tenant,
reloaded `/integrations`, and confirmed the new Slack card rendered
with its three action widgets. Clicked "Kanalları listele"
(`list_channels`) and "Gönder" (`send_message`) -- both resolved
immediately (no pending approval), and both requests reached the real
`https://slack.com/api/...` endpoints, correctly failing with a real
`{"ok": false, "error": "invalid_auth"}` from Slack itself (proving the
policy tier let the call through to a real network attempt, not that a
fake token can call real Slack). Clicked "Mesajı sil" (`delete_message`)
-- correctly returned `pending_approval` without touching Slack at all;
the resulting row appeared on the real `/approvals` dashboard with
`HIGH` risk and the right parameters, including in the connector-level
`ActionGraph` visualization (proving `ActionGraph.tsx`'s new
`slack: MessageSquare` icon mapping renders without error) -- then
denied it there to leave no dangling pending state. Cleaned up the
inserted test credential afterward.

### Phase 26 -- real framework examples for both SDKs (LangGraph, LangChain.js)

Closes the "framework-specific example" gap both SDK READMEs' own "What
this is not (yet)" sections and `docs/PRODUCTIZATION_ROADMAP.md`'s Phase
D writeup named explicitly -- this project's own stated standard is
that a framework isn't claimed as supported without a real, runnable
example proving it, so this phase builds exactly that for one framework
per SDK, and is honest that the rest (CrewAI, LangGraph.js) still don't
have one.

- `sdk/python/examples/langgraph_agent.py` (new) -- wraps
  `AgentGuardClient.run()` as a genuine LangChain `@tool`
  (`execute_via_agentguard`, real `args_schema` inferred from real type
  hints) and builds a real LangGraph `StateGraph` around it using
  LangGraph's own prebuilt `ToolNode` (the same node a real
  `create_react_agent` graph uses internally). New optional
  `sdk/python[langgraph]` extra (`langgraph`, `langchain-core`) --
  scoped to this example only; the core `agentguard-sdk` package stays
  dependency-free beyond `httpx`.
- `sdk/typescript/examples/langchain-agent.ts` (new) -- wraps `run()`
  as a genuine LangChain.js `StructuredTool` via `@langchain/core`'s
  `tool()`, with a real `zod` input schema. `@langchain/core`/`zod`
  added as devDependencies (example-only, same reasoning as the Python
  extra -- `@agentguard/sdk` itself keeps zero runtime dependencies).
  Pinned `@langchain/core` to `^1.2.0`, not the initially-tried
  `^0.3.0`: the older range resolved a `langsmith` transitive
  dependency with three known CVEs (one high-severity SSRF/prototype-
  pollution advisory), all absent from `1.2.0+` -- caught by `npm
  audit`, not by inspection.
- **Deliberately, explicitly does NOT call a real LLM or wire either
  example into a full agent loop.** Getting an LLM to actually pick a
  tool and choose its arguments needs a real model provider (OpenAI/
  Anthropic/...) and a real API key -- an external decision neither
  example makes on a reader's behalf. What's proven instead, and is
  the part that's actually this project's to prove: the tool/schema
  shape is real and correctly inferred, and invoking it for real
  (`tool.ainvoke(...)` / `tool.invoke(...)`, the same call a real
  LLM-driven tool-calling node makes) reaches the real platform API
  over a real HTTP call.

**Verified, not asserted:** both examples actually run, not just
compile/type-check. `ruff check`/`mypy` clean on the new Python file;
`tsc -b`/`oxlint` clean on the new TypeScript file (after fixing three
real `ruff` findings in the Python file first -- unsorted imports, an
unused `tools_condition` import, and a mutable-default-argument
warning, all caught by `ruff check`, none by inspection). Then live
against the real running platform, both times: minted a fresh Agent,
granted it the tenant's real GitHub connector, ran each example as a
real separate process. Both printed the tool's real inferred name/
schema, built (Python) or the pre-existing (TypeScript) real
tool object, then called `.ainvoke()`/`.invoke()` for real against
`github.list_repos` -- reaching the real GitHub connector and
correctly surfacing the same real `401 Bad credentials` this session's
other live checks have hit from the same since-expired stored OAuth
token, proving each tool wrapper actually round-trips through the real
platform rather than stopping at a mock. Confirmed both calls landed
on the real `/tenant/audit` log under their own agent names
("LangGraph Example Agent"). `sdk/python`'s own 5 SDK tests and
`sdk/typescript`'s own 9 vitest tests both re-run clean afterward,
proving the new dependencies didn't disturb either package's existing
test suite.

**A real gap found and fixed in passing, not part of this phase's own
goal:** `sdk/typescript/tsconfig.json`'s `include` is scoped to `src/`
(what actually ships) -- `npm run build` (`tsc -b`) has therefore never
type-checked anything under `examples/`, including
`examples/basic-agent-loop.ts`, unnoticed since Phase 24 shipped it.
Confirmed by running `tsc --noEmit` against both example files
directly outside the project config: `basic-agent-loop.ts` happened to
already be clean, but the gap itself was real -- CI would have let a
broken example file merge silently. Fixed with a new
`tsconfig.examples.json` (`rootDir: "."`, `include: ["examples"]`) and
a `typecheck:examples` script, wired into the `sdk-typescript` CI job
as its own step so this can't regress unnoticed again.

### Phase 27 -- the second framework example for each SDK (CrewAI, LangGraph.js)

Closes the rest of the "framework-specific example" gap Phase 26 left
named but unbuilt: CrewAI for the Python SDK, a real LangGraph.js graph
(not just the LangChain.js tool wrapper) for the TypeScript SDK.

- `sdk/python/examples/crewai_agent.py` (new) -- `ExecuteViaAgentGuardTool`,
  a real `crewai.tools.BaseTool` subclass with a real pydantic
  `args_schema`, overriding `_arun` (CrewAI's own supported async path)
  to call `AgentGuardClient.run()` directly -- no sync/async bridging
  hack needed, since both are natively async.
- `sdk/typescript/examples/langgraph-agent.ts` (new) -- takes the exact
  `StructuredTool` `langchain-agent.ts` already builds and drops it
  into a real `StateGraph` wired with LangGraph.js's own prebuilt
  `ToolNode` (the same node a real `createReactAgent` graph uses
  internally), then invokes it by routing a real `AIMessage` with a
  `tool_calls` entry through the compiled graph -- the same shape a
  real LLM's tool-call output would take, just hard-coded here instead
  of model-produced.
- Same honesty boundary as Phase 26, restated because it matters
  every time: neither example calls a real LLM or runs a full
  `Crew`/agent loop end to end. That needs a real model-provider API
  key this project won't choose on a reader's behalf. What's proven
  instead is the tool/schema shape and the real HTTP round-trip through
  `.arun()` / the compiled graph's `.invoke()`.

**A real, structural finding, not a code bug -- CrewAI's dependency
tree cannot share a Python environment with this platform:** installing
`crewai` into this repo's own `.venv` (the same one `apps/api`,
`apps/mcp_server`, and `sdk/python` itself already live in) downgraded
the `mcp` package from `2.0.0` to `1.28.1` as a transitive dependency,
which broke `apps/mcp_server/server.py`'s own import
(`ModuleNotFoundError: No module named 'mcp.server.mcpserver'`) --
found by actually importing `apps.mcp_server.server` after installing
`crewai`, not by reading `crewai`'s own dependency list. Uninstalling
`crewai` and reinstalling `mcp>=1.0` (which resolved back to `2.0.0`)
fixed it; the full 245-test suite was re-run clean afterward to
confirm nothing else had silently shifted. `crewai_agent.py`'s own
module docstring documents this, and the example is meant to be run
from a dedicated `sdk/python/.venv-crewai` (new, gitignored) rather
than the shared repo venv -- not a workaround, the correct shape: an
SDK example's own heavy, unrelated dependency tree has no reason to
share an environment with the platform it's calling.

**Verified, not asserted:** `ruff check`/`mypy` clean on both new
files (run from `.venv-crewai` for the Python one, so type-checking
sees the same `crewai`/pydantic versions the example actually runs
against); `tsc -b`/`typecheck:examples`/`oxlint` clean on the
TypeScript one. Then live against the real running platform, both
times: reused the same Agent (already granted the tenant's real GitHub
connector) Phase 26 minted, ran each example as a real separate
process (`crewai_agent.py` via `.venv-crewai`'s own Python). Both
printed the tool's real inferred name/schema, then called
`.arun()` / the compiled graph's `.invoke()` for real against
`github.list_repos` -- reaching the real GitHub connector and
correctly surfacing the same real `401 Bad credentials` this session's
other live checks have hit from the same since-expired stored OAuth
token. Confirmed both calls landed on the real `/tenant/audit` log.
`sdk/typescript`'s own 9 vitest tests re-run clean afterward.

### Phase 28 -- developer experience pass (closes the rest of Phase E)

Closes the last open item from `docs/PRODUCTIZATION_ROADMAP.md`'s
Phase E sketch: quickstart docs / a 5-minute install path. Added a
third quickstart to the main `README.md`, "Quickstart — call a guarded
action from your own agent code (SDK)" -- sitting right after the
existing platform quickstart, it takes a reader from "the platform is
running" to "a real Agent key called a real guarded action from real
Python/TypeScript code" in two steps, then points at both SDKs' own
READMEs for the framework-specific examples (Phases 26-27).

**A real bug found and fixed while writing this, not part of the
phase's own goal:** every framework example's own docstring and
`sdk/typescript/README.md` documented running it with `node
--experimental-strip-types examples/foo.ts` -- this command has never
actually worked, for any of the three TypeScript examples, since Phase
24. Node's native type stripping doesn't rewrite relative import
specifiers: `../src/index.js` (the NodeNext-compiled shape this
package's own source uses) resolves against a literal `src/index.js`
file, which only exists after `npm run build` writes it to `dist/`, not
`src/` -- so the documented command fails immediately with
`ERR_MODULE_NOT_FOUND`. Every prior live verification in this session
(Phases 24, 26, 27) worked around this unknowingly by hand-writing a
temporary `.mjs` runner that imported the already-built `dist/index.js`
directly, rather than running the example file as documented -- which
meant the documented command itself was never actually tried until
this phase, while writing the SDK quickstart section and actually
following its own instructions. Fixed by adding `tsx` as a
devDependency (resolves `.ts` sources' relative imports correctly, no
separate build step needed) and updating all three examples' own
docstrings plus `sdk/typescript/README.md` to `npx tsx examples/foo.ts`
instead.

**Verified, not asserted:** re-ran all three TypeScript examples with
the newly-corrected `npx tsx examples/foo.ts` command for the first
time (previously only ever run via the workaround `.mjs` runner) --
all three actually ran, resolved imports correctly, and reached the
real platform API, hitting the same real GitHub 401 as every prior live
check. Also ran `sdk/python/examples/basic_agent_loop.py` directly (the
Python half of the new SDK quickstart) against the real platform, same
result. `tsc -b`/`typecheck:examples`/`oxlint`/vitest (9 tests) all
re-run clean after adding `tsx`.

### Phase 29 -- an Agent's own, narrower-than-its-owner's RBAC permission set

Closes the gap `infrastructure/database/models/agent.py`'s own
docstring and `docs/PRODUCTIZATION_ROADMAP.md` have both named since
Phase B: "its effective permissions are its owner's... a true
independently-scoped, narrower grant per agent is real future work."

New `AgentPermissionGrant` (`infrastructure/database/models/agent_permission_grant.py`,
migration `1130f09ac23e`) -- deliberately NOT deny-by-default the way
`AgentCredentialGrant` is, and the model's own docstring explains why
at length: an Agent with zero grants keeps inheriting its owner's full
permission set unchanged (every Agent created before this phase keeps
working exactly as it did -- no breaking change, unlike
`AgentCredentialGrant`'s own deliberate tightening in Phase 21).
`agent.execute`/`connector.read`/`approval.read` are what an Agent *is*,
not an optional extra; defaulting that to empty the moment this model
shipped would have broken every existing Agent's basic ability to
function, a real regression, not a tightening. What DOES change: the
moment a tenant admin grants at least one permission, the agent's
effective set narrows to the INTERSECTION of its owner's currently-held
permissions and its own granted set -- re-evaluated on every request
against the owner's live roles (`apps/api/dependencies.py`'s
`require_actor_permission`), not cached at grant time, so an agent can
never end up holding a permission its owner doesn't currently have.

New `GET/PUT/DELETE /tenant/agents/{id}/permissions[/{code}]`
(`apps/api/routers/agents.py`, `RequireTenantAdmin`-only, mirroring
`AgentCredentialGrant`'s own connector endpoints exactly). Listing only
shows permission codes the agent's owner actually holds -- granting one
the owner doesn't have would be inert anyway (the intersection would
never include it), so the endpoint refuses it outright (400) rather
than silently accepting a grant that can never take effect. New
`/agents` dashboard panel ("RBAC izinleri", next to the existing
connector-access panel) with the same toggle-per-row shape, and a
message that switches between "inherits everything" and "narrowed to
only what's checked" depending on whether any grant exists yet.

`tests/test_infra_api_agent_permissions.py` (new, 10 tests): the two
claims that matter most -- zero grants really does mean "inherit
everything" (proven against a real Actor-gated route, `GET
/connectors`), and one grant really does narrow to the intersection
(proven by showing a DIFFERENT Actor-gated route, `GET /approvals`,
newly 403s for the same agent key that could reach it freely before,
while the granted permission itself still resolves through a real
`execute` call). Also: idempotent re-grant, 404 for an unknown
permission code or cross-tenant agent, RBAC on the management
endpoints themselves, and revoking the last grant restoring full
inheritance.

**Verified, not asserted:** all 10 new tests pass against the real
local Postgres; full suite -- 255 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean; frontend `tsc -b`/`oxlint`/`vite build` all
clean. Then live against the real running dashboard: expanded a real
pre-existing agent ("LangGraph Example Agent") on `/agents`, confirmed
the new panel listed all 11 real permission codes as ungranted with the
"inherits everything" message, granted `agent.execute` through the UI
and watched the message switch to "narrowed." Confirmed the real effect
via direct API calls with that agent's real key: `GET /connectors`
(needs `connector.read`, never granted) and `GET /approvals` (needs
`approval.read`, never granted) both correctly 403'd with `Missing
required permission: ...`, while a real `POST .../execute` call for
`github.list_repos` got past the permission gate and reached the real
GitHub connector layer (the same real 401 this session's other live
checks have hit from the same stale OAuth token) -- proving the
intersection includes what WAS granted, not just excludes what wasn't.
Revoked the grant through the UI and confirmed `GET /connectors`
immediately returned to 200, proving the "revoke the last grant ->
full inheritance restored" behavior for real, not just in the test
suite.

### Phase 30 -- license change (MIT -> AGPL-3.0-or-later), author attribution

Not an engineering phase -- a project-ownership decision. Replaced the
MIT license with the real, unmodified GNU Affero General Public
License v3.0 text (fetched verbatim from `gnu.org`, not reconstructed
from memory, given the legal stakes of getting a license document
wrong) in all three `LICENSE` copies (`/`, `agentguard/`, `sdk/python/`
-- `sdk/typescript` has no separate copy, only a `package.json`
`license` field). Copyright and `authors`/`author` fields across
`pyproject.toml` (root and `sdk/python`), `sdk/typescript/package.json`,
and `README.md` now attribute the project to Furkan Ulusoy rather than
the placeholder "AgentGuard contributors". `YOUR_ORG` placeholder URLs
in both `pyproject.toml` files corrected to the real
`github.com/furkanulusoy/agentguard`, matching the CI badge URL that
already used it.

AGPL-3.0's practical effect versus MIT: anyone who runs a modified
version of this project as a network service (not just anyone who
redistributes the code) must make their modified source available to
users of that service -- closes the "someone forks this, hosts it as
their own SaaS, and never contributes anything back" gap MIT leaves
open. Re-installed the package (`pip install -e ".[all]"`) after the
metadata change to confirm nothing broke.

### Phase 31 -- self-host with Docker (one `docker compose up`, no Python/Node install needed)

Closes a real gap: until now, running the actual platform (not the
older `server/app.py` reference deployment the repo-root `Dockerfile`
already packaged) required installing Python, Node, and manually
editing `.env` -- fine for a developer, not for the non-technical
users (business owners, students, people using AI tools without being
programmers) this project is meant to reach if it's ever going to be
a real product someone can hand to a company for a trial.

- `apps/api/Dockerfile` (new) -- production image for the real
  platform API, distinct from the repo-root `Dockerfile` (which still
  packages the older Flask reference deployment, untouched).
  `apps/api/docker-entrypoint.sh` applies Alembic migrations on every
  container start, then serves with `gunicorn` + `uvicorn` workers --
  not the `--reload` dev server `apps/api/main.py`'s own docstring
  documents for local development. `gunicorn` is scoped to this image
  only (installed in the Dockerfile, not added to `pyproject.toml`) --
  `sdk/python` and the Flask reference deployment have no use for it.
- `apps/web/Dockerfile` (new) -- multi-stage: real `npm ci && npm run
  build` in a `node:22-alpine` stage, then the built bundle served by
  plain `nginx:alpine` (no Node runtime in the final image).
  `apps/web/nginx.conf`'s `try_files` fallback is required, not
  decorative -- `react-router-dom`'s client-side routes (`/agents`,
  `/policies`, ...) need every deep link to load the app shell first,
  which a raw nginx 404 wouldn't do.
- `docker-compose.yml` extended from Postgres-only (its own comment
  had said "later phases add ... api/web services here" since Phase
  1 -- this is that later phase) to the full stack, with a real
  `depends_on: condition: service_healthy` chain and the API's own
  `/health` endpoint as its healthcheck.
- `scripts/setup.sh` / `scripts/setup.ps1` (new) -- writes a real
  `.env` with real, randomly-generated `JWT_SECRET_KEY`/
  `SECRET_ENCRYPTION_KEY`/`POSTGRES_PASSWORD`. Running with
  `.env.example`'s literal `changeme` placeholders in a real
  deployment is a real vulnerability, not a hypothetical one -- this
  project won't let that happen silently. Refuses to overwrite an
  existing `.env`.
- `.dockerignore` and `apps/web/.dockerignore` (new) -- without them,
  `docker compose build`'s context transfer included this repo's own
  1.1GB `.venv/` and `apps/web`'s 161MB `node_modules/`, found by
  actually watching the build log report a 156MB context transfer for
  a package whose real source is a few MB, not by inspection.
- `.gitattributes` (new) -- forces `*.sh` files to stay LF even on a
  Windows checkout, closing a real "CRLF shebang breaks inside a Linux
  container" failure mode before it could ever hit a real user.
- CI gained a `docker-self-host` job: builds the full stack, starts
  it, polls the real `/health` endpoint until it reports
  `"database":"connected"`, and confirms the dashboard actually serves
  real HTML -- so this packaging can't silently rot the way the
  repo-root `Dockerfile` had (`docs/ROADMAP_TO_PRODUCTION.md` had long
  listed it as "written to standard conventions but still
  unverified").

**Two real bugs found while actually testing this, not by inspection:**

1. **A predictable-secret bug in `scripts/setup.ps1`.** The first
   version generated random bytes with `[RandomNumberGenerator]::Fill`
   -- a .NET 6+ static method that doesn't exist on Windows
   PowerShell 5.1's older .NET Framework runtime. Calling it there
   doesn't throw a terminating error; it silently leaves the byte
   array at its default all-zero value, so the script would have
   generated a completely predictable `SECRET_ENCRYPTION_KEY` (and
   every other "random" secret) with zero indication anything had
   gone wrong -- found by actually inspecting the generated key's byte
   pattern (`AAAA...A=`) after a real run on Windows PowerShell 5.1,
   not by assuming the .NET API surface. Fixed with
   `RNGCryptoServiceProvider.GetBytes()`, an instance-method API both
   old and new .NET runtimes support. Separately, the Fernet key
   generation path was also fixed to keep its base64 `=` padding --
   `New-UrlSafeToken`'s generic helper strips it (fine for a plain
   token), but a Fernet key one byte short of the padded length fails
   to parse in the `cryptography` library. Verified the corrected key
   for real: generated one in PowerShell, decrypted a round-trip with
   it in Python's own `cryptography.fernet.Fernet`.
2. **A real CORS gap, found by actually registering a workspace
   through the Docker stack, not by reading the code.** The default
   `cors_allowed_origins` (`infrastructure/config.py`) only ever
   listed `http://localhost:5173` (`apps/web`'s Vite dev server) --
   never `http://localhost` (port 80, where `docker-compose.yml`'s
   `web` service actually serves the dashboard), so the very first
   real registration attempt through the Docker stack failed with a
   real CORS preflight rejection in the browser console. Fixed in both
   `infrastructure/config.py`'s own default and `.env.example`.
   Also found in the same pass: `docker compose restart` does NOT
   reread `env_file` values -- only `docker compose up -d <service>`
   (recreating the container) does; confirmed via `docker exec ...
   printenv`, which showed the old value was still what the running
   container actually had after a plain `restart`.

**Verified, not asserted:** built both images for real
(`docker compose build`), confirmed `.dockerignore` actually shrank
the build context (from >150MB to a few KB, visible in the build log).
Ran the full stack for real (`docker compose up -d`) against this
session's own real Postgres data. Confirmed via real `curl`: `/health`
returned `{"status":"ok","database":"connected"}`, `/docs` and the
dashboard root both 200'd. Then, live in a real browser against
`http://localhost` (port 80, the actual nginx-served build, not the
Vite dev server): registered a real workspace end to end -- real
tenant, real JWT, real dashboard render ("Hoş geldin, docker-test3",
correct OWNER role, correct team-of-one) -- through the exact Docker
stack a real self-hosting user would run, not a developer shortcut.
Cleaned up the test tenant from the database afterward. Full 255-test
suite and `ruff`/`mypy` re-run clean after the `infrastructure/config.py`
change.

### Phase 32 -- audit: the "sensitive action needs human approval" scenario, and closing the one real gap it found

An external audit request asked whether this codebase, as it actually
is today, supports a specific 10-step scenario: an agent wants to run
`refund_customer(amount=50000 TRY)`, AgentGuard catches it, a
policy/risk engine classifies it `CRITICAL`, it's held for human
approval, `APPROVE`/`DENY` from the dashboard, `DENY` never runs the
real action and records why, `APPROVE` really executes it, and the
approval record carries agent/action/parameters/risk level/reason/
requester/approver/timestamp/execution result. The instruction was
explicit: report what's real vs. mock, don't invent capability the
code doesn't have, and build the minimum real test possible without
ever risking a real irreversible action.

**What's real, verified against the actual code, not asserted:**
task-scope enforcement, the human-approval gate (`Policy.sensitive_actions`),
the guarded call never running until approved
(`apps/api/routers/approvals.py`'s `resolve_approval`), `APPROVE`/`DENY`
from the same API the dashboard calls, `DENY` never touching the real
connector, `APPROVE` really invoking it, and eight of the ten fields
the scenario named on `ApprovalRequest`/`AuditEvent` (agent, action,
parameters, risk level, requester, approver, timestamps, execution
result).

**What's fictional relative to this codebase, named rather than
worked around:** there is no `refund_customer` action or any
payments/financial connector anywhere (`connectors/registry.py` only
ever registers gmail/github/slack); risk levels are a fixed
LOW/MEDIUM/HIGH lookup per `(connector_type, action)`
(`connectors/*/policy.py`'s `RISK_LEVELS` dicts), not a `CRITICAL`
tier, and nothing inspects an action's actual parameter *values* (an
`amount`) to classify risk dynamically -- risk is entirely static,
decided by which action name was called, never by what it was called
with. An action name no connector defines (like `refund_customer`) is
rejected at the task-scope gate (`decision_source: "TASK_SCOPE"`)
before any policy/risk resolution runs at all -- there is no
"classify, then require approval" step for an undefined action to reach.
`ApprovalRequest` also has no free-text "reason" field -- only
`AuditEvent.decision_source`, a fixed enum of *which rule* decided
(`TASK_SCOPE`/`AGENT_POLICY`/`TENANT_POLICY`/`SYSTEM_DEFAULT`/
`AGENT_NOT_GRANTED`), not a human-readable sentence.

**The one real, previously-named gap this audit's own instructions led
to closing:** `tests/test_infra_api_connectors_approvals.py`'s own
module docstring already stated plainly that `resolve_approval`'s
`approved=True` branch -- the moment a real guarded call actually
executes -- "is NOT exercised here... verified manually... during this
project's UI work," not by an automated test. `tests/test_e2e_approval_lifecycle.py`
(new, 4 tests) closes that gap for real, end to end, against
`github.close_issue` (this codebase's closest real analog to the
scenario's HIGH-risk write -- not `refund_customer`, never invented):
the full DENY path (catch, classify HIGH, hold pending, deny, confirm
the real connector was never touched, confirm the outcome is visible
on `/tenant/audit` via its live `ApprovalRequest` join), the full
APPROVE path (confirm the real connector call actually ran, evidenced
by a real GitHub 401 -- not a mock -- landing in the approval's own
`error` field), a field-by-field check of the ten scenario fields
against real API responses, and a direct demonstration of what a
`refund_customer`-shaped call actually gets today (403, `TASK_SCOPE`,
before any risk classification). Same fake-credential-so-the-real-call-
fails-honestly discipline as its sibling test file -- a throwaway
GitHub token that can never succeed, so `APPROVE` still reaches the
real `api.github.com` and gets a real rejection, never a real
irreversible action, per this audit's own instruction.

**Verified, not asserted:** all 4 new tests pass against the real
local Postgres; full suite -- 259 tests, 0 skipped -- still green;
`ruff check`/`mypy` clean.

## 0.2.0

Engineering-maturity pass: turned the demo repo into an installable
library with real config, real audit logging, a real (tested, concurrent)
approval flow, and a real reference HTTP deployment.

### Added
- `pyproject.toml` -- `agentguard` is now `pip install`-able, with optional
  extras `[yaml]`, `[cli]`, `[server]`, `[dev]`.
- `Policy.from_file()` / `Policy.from_dict()` -- load policies from JSON
  (always) or YAML (`agentguard[yaml]`) instead of only constructing them
  in Python. Example files in `examples/policies/`.
- `agentguard.audit_log.JsonlAuditLog` -- structured JSON Lines audit
  trail, wired into `AgentGuard` via a new optional `audit_log` parameter.
- `agentguard.approval` -- `TerminalApprover` (interactive y/n prompt),
  `AutoDenyApprover` (fail-closed default), and `QueueApprover` (async,
  thread-safe, timeout-and-deny pending-approval queue) as real
  alternatives to a scripted `approval_callback`.
- `agentguard.cli` -- `agentguard policy-check`, `agentguard audit`,
  `agentguard demo` (`agentguard[cli]`).
- `server/app.py` -- reference Flask deployment exposing AgentGuard over
  HTTP, including a sensitive action blocking one request until a
  separate request resolves it (`agentguard[server]`).
- `Dockerfile` for the reference server.
- Scenario D: prompt injection, plus the honest caveat that this repo has
  no LLM in the loop and simulates the *outcome* of a successful
  injection deterministically.
- `agentguard/mcp_adapter.py` -- MCP-call-shaped adapter (`GuardedMCPServer`),
  dependency-free. The real-SDK wiring sketch at the bottom of the file was
  read-verified against the official `modelcontextprotocol/python-sdk`
  source (cloned from GitHub, which is reachable here even though PyPI
  isn't) -- and that check caught the sketch using the deprecated v1
  decorator API (`@server.list_tools()`); it's now corrected to the
  current v2 constructor-based `on_list_tools`/`on_call_tool` API. Still
  not executed end-to-end: `hatchling`/`httpx`/`anyio`/`starlette` aren't
  installable in this sandbox (no PyPI access).
- `docs/ROADMAP_TO_PRODUCTION.md` and `SECURITY.md`.
- Test suite grew from 6 to 48 stdlib `unittest` tests across the
  guardrail, MCP adapter, policy config loading, audit log, approval
  channels (including real multi-threaded `QueueApprover` behavior), CLI,
  and the reference server (including real concurrent HTTP behavior).

### Fixed
- `MockInbox._id_counter` was a class attribute shared across every
  `MockInbox` instance in the process, so message IDs kept climbing
  across unrelated inboxes instead of each fresh inbox starting at 1.
  Found while writing the new test suite; now per-instance.

### Known gaps (see `docs/ROADMAP_TO_PRODUCTION.md`)
- No independent security audit or pen test.
- MCP adapter wiring is read-verified against the real SDK source, not
  executed against a live client. Dockerfile is unverified against a real
  registry (no network access to either PyPI or Docker Hub here).
- Reference server is in-memory, unauthenticated, single-tenant, dev-server-only.
- No compliance, legal, or market-validation work has started -- none of
  that is a coding task.

## 0.1.0

Initial demo: three before/after scenarios (over-broad permissions,
unapproved autonomous action, malicious/fake connector), the core
`AgentGuard` guardrail, a stdlib `unittest` suite, and GitHub Actions CI.
