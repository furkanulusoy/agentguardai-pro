"""
HTTP-level tests for the refresh-token httpOnly cookie
(apps/api/routers/auth.py) -- the mechanism apps/web's frontend now
relies on entirely (apps/web/src/api/client.ts no longer stores or
sends the refresh token itself), while apps/mcp_server/server.py (a
non-browser client with no cookie jar of its own) keeps working via the
JSON body's refresh_token field unmodified. Both are exercised here.

Same real-Postgres-or-skip discipline as test_infra_api.py.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid

os.environ["RATE_LIMIT_ENABLED"] = "false"

try:
    import httpx

    from apps.api.main import app
    from apps.api.routers.auth import COOKIE_NAME
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
class TestRefreshCookie(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        self._cleanup_tenant_ids: list[uuid.UUID] = []

    async def asyncTearDown(self):
        await self.client.aclose()
        if self._cleanup_tenant_ids:
            async with SessionLocal() as session:
                for tenant_id in self._cleanup_tenant_ids:
                    obj = await session.get(Tenant, tenant_id)
                    if obj is not None:
                        await session.delete(obj)
                await session.commit()
        await engine.dispose()

    async def _register(self, **overrides) -> tuple[httpx.Response, dict]:
        body = {
            "tenant_name": "Cookie Test Co",
            "tenant_slug": f"cookie-{uuid.uuid4().hex[:10]}",
            "email": f"cookie+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
        body.update(overrides)
        resp = await self.client.post("/auth/register", json=body)
        assert resp.status_code == 201, resp.text
        access = resp.json()["access_token"]
        me = await self.client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        self._cleanup_tenant_ids.append(uuid.UUID(me.json()["tenant_id"]))
        return resp, body

    async def test_register_sets_httponly_refresh_cookie(self):
        resp, _ = await self._register()
        set_cookie = resp.headers.get_list("set-cookie")
        self.assertEqual(len(set_cookie), 1)
        cookie_header = set_cookie[0]
        self.assertIn(f"{COOKIE_NAME}=", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("Path=/auth", cookie_header)
        self.assertIn("SameSite=lax", cookie_header)
        # The JSON body still carries the real value too -- see the
        # module docstring for why (apps/mcp_server has no cookie jar).
        self.assertNotIn("refresh_token", resp.json())
        self.assertIn(self.client.cookies[COOKIE_NAME], cookie_header)

    async def test_login_sets_a_fresh_cookie(self):
        _, body = await self._register()
        resp = await self.client.post(
            "/auth/login", json={"email": body["email"], "password": body["password"]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(COOKIE_NAME, self.client.cookies)

    async def test_refresh_works_from_the_cookie_alone_no_body_token(self):
        await self._register()
        # Empty body -- exactly what apps/web's frontend now sends
        # (apps/web/src/api/client.ts's refreshAccessToken). The cookie
        # set by _register() above is already sitting in self.client's
        # jar and gets sent automatically.
        resp = await self.client.post("/auth/refresh", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access_token", resp.json())
        # Rotation set a fresh cookie too, same as the original register.
        self.assertIn(COOKIE_NAME, self.client.cookies)

    async def test_logout_clears_the_cookie(self):
        await self._register()
        resp = await self.client.post("/auth/logout", json={})
        self.assertEqual(resp.status_code, 204)
        set_cookie = resp.headers.get_list("set-cookie")
        self.assertEqual(len(set_cookie), 1)
        # FastAPI's delete_cookie expires it immediately.
        self.assertIn("Max-Age=0", set_cookie[0])

    async def test_body_token_still_works_with_no_cookie_present(self):
        # Simulates apps/mcp_server/server.py: its own httpx.AsyncClient
        # never receives a browser's cookies, but still has the real
        # refresh_token value from the JSON response.
        resp, _ = await self._register()
        raw_refresh = self.client.cookies[COOKIE_NAME]

        headless_transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=headless_transport, base_url="http://test"
        ) as headless:
            refreshed = await headless.post("/auth/refresh", json={"refresh_token": raw_refresh})
            self.assertEqual(refreshed.status_code, 200)

    async def test_explicit_body_token_takes_priority_over_a_stale_cookie(self):
        # Regression guard for the priority bug this session found while
        # writing this exact test: rotating via the cookie also rotates
        # the cookie itself, so a later call that deliberately replays
        # the ORIGINAL (now-revoked) token in the body must still be
        # rejected -- not silently succeed because a fresher cookie
        # happens to be sitting in the same client's jar.
        resp, _ = await self._register()
        original_refresh = self.client.cookies[COOKIE_NAME]

        rotated = await self.client.post("/auth/refresh", json={})
        self.assertEqual(rotated.status_code, 200)
        self.assertIn(COOKIE_NAME, self.client.cookies)
        self.assertNotEqual(self.client.cookies[COOKIE_NAME], original_refresh)

        replay = await self.client.post("/auth/refresh", json={"refresh_token": original_refresh})
        self.assertEqual(replay.status_code, 401)


if __name__ == "__main__":
    unittest.main()
