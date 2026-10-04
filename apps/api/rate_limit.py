"""
Per-IP rate limiting for the auth endpoints -- closes a real gap: before
this, /auth/login, /auth/register, and /auth/refresh had no brute-force
or spam mitigation at all (found in this project's own product/security
audit, see CHANGELOG.md).

In-memory storage (slowapi's default, backed by the `limits` package) --
correct for today's single-instance deployment
(docs/ROADMAP_TO_PRODUCTION.md's horizontal-scaling gap: a second
replica wouldn't share these counters). A multi-replica deployment would
swap in a shared backend via `storage_uri=` (e.g. Redis) without
changing any call site here -- same swap-the-backend-not-the-callers
shape as infrastructure/secrets/store.py's SecretStore protocol.

Per-IP, not per-account: the identity a rate limit needs to key on has
to exist *before* auth succeeds (login/register have no user yet), and
IP is the standard first layer (OWASP's own guidance). A second,
per-account layer could be added later without replacing this one.

headers_enabled is deliberately False, not just left at the default:
turning it on breaks every SUCCESSFUL (non-error) response from a
rate-limited route on this FastAPI/slowapi version pairing -- slowapi's
header-injection step needs the raw Response object, but a route
declared with response_model=... returns a plain Pydantic model, not a
Response, until well after slowapi's wrapper has already run. Found the
hard way: /auth/register worked in every test here because they all
checked the REJECTED path (401/429, which raise before slowapi tries to
inject headers) -- a real, successful registration crashed with a 500
that no test caught. tests/test_rate_limit.py now has a regression test
for exactly this (a genuine 2xx through a rate-limited route), and it's
`enabled=True` there specifically so it exercises the real code path,
not just documents the bug in a comment.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from infrastructure.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    headers_enabled=False,
    enabled=settings.rate_limit_enabled,
)
