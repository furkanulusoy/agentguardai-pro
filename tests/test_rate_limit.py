"""
Proves the rate limiter apps/api/rate_limit.py wires onto /auth/*
actually blocks, rather than just asserting the decorator is present.
Every other apps/api test suite disables rate limiting (RATE_LIMIT_ENABLED,
set before apps.api.main is first imported -- see test_infra_api.py's
module docstring) because they legitimately call /auth/* far more often
per minute than any real client would. This file is the one place that
turns it back on, on the real app.state.limiter instance.

test_a_successful_request_through_a_rate_limited_route_does_not_crash
exists because of a real bug this suite originally missed: an earlier
version of apps/api/rate_limit.py had headers_enabled=True, which
crashes with a 500 on every SUCCESSFUL response from a route declared
with response_model=... (slowapi's header-injection step needs a raw
Response object; FastAPI hasn't produced one yet at the point slowapi's
wrapper runs for a typed Pydantic return). Every other test here only
ever drove /auth/login into its REJECTED path (401 wrong-password, or
429 rate-limited) -- both raise before slowapi tries to inject
anything, so nothing caught it until a real successful /auth/register
call was tried by hand. headers_enabled is now False; this test is the
regression guard so that specific mistake can't silently come back.

Same real-Postgres-or-skip discipline as test_infra_api.py: /auth/login
still does a real DB query for each of the first 5 (under-limit) calls.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid

try:
    import httpx

    from apps.api.main import app
    from infrastructure.database.models import Tenant
    from infrastructure.database.session import SessionLocal, check_database_connection, engine

    API_IMPORTS_AVAILABLE = True
except ImportError:
    API_IMPORTS_AVAILABLE = False


def _db_reachable() -> bool:
    if not API_IMPORTS_AVAILABLE:
        return False
    try:
        reachable = asyncio.run(check_database_connection())
    except Exception:
        return False
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestRateLimiting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        self._cleanup_tenant_ids: list[uuid.UUID] = []
        # Every other suite runs with this off (see module docstring) --
        # flip it on just for this test class, on the real limiter
        # instance apps/api/main.py attached to app.state, and clear any
        # counts a previous test left behind in its shared in-memory
        # storage.
        app.state.limiter.enabled = True
        app.state.limiter.reset()

    async def asyncTearDown(self):
        app.state.limiter.enabled = False
        if self._cleanup_tenant_ids:
            async with SessionLocal() as session:
                for tenant_id in self._cleanup_tenant_ids:
                    obj = await session.get(Tenant, tenant_id)
                    if obj is not None:
                        await session.delete(obj)
                await session.commit()
        await self.client.aclose()
        await engine.dispose()

    async def test_sixth_login_attempt_within_a_minute_is_rate_limited(self):
        body = {"email": "nobody-at-all@example.com", "password": "wrong"}

        for i in range(5):
            resp = await self.client.post("/auth/login", json=body)
            self.assertEqual(resp.status_code, 401, f"attempt {i + 1} should be a normal 401")

        blocked = await self.client.post("/auth/login", json=body)
        self.assertEqual(blocked.status_code, 429)

    async def test_rate_limit_is_per_endpoint_not_global(self):
        # Exhaust /auth/login's 5/minute limit...
        body = {"email": "nobody-at-all@example.com", "password": "wrong"}
        for _ in range(5):
            await self.client.post("/auth/login", json=body)
        blocked = await self.client.post("/auth/login", json=body)
        self.assertEqual(blocked.status_code, 429)

        # ...but /auth/refresh (30/minute, a separate limit) is unaffected.
        refresh = await self.client.post("/auth/refresh", json={"refresh_token": "not-a-real-one"})
        self.assertEqual(refresh.status_code, 401)

    async def test_a_successful_request_through_a_rate_limited_route_does_not_crash(self):
        # See the module docstring -- this is the regression test for a
        # real bug that reached a running server (though never a real
        # deployment): headers_enabled=True crashed every SUCCESSFUL
        # response from a rate-limited, response_model=... route with a
        # 500. Under the limit AND a genuine 2xx, not an error path.
        body = {
            "tenant_name": "Rate Limit Regression Co",
            "tenant_slug": f"rl-regress-{uuid.uuid4().hex[:10]}",
            "email": f"rl-regress+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
        resp = await self.client.post("/auth/register", json=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertIn("access_token", data)

        me = await self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        self._cleanup_tenant_ids.append(uuid.UUID(me.json()["tenant_id"]))


if __name__ == "__main__":
    unittest.main()
