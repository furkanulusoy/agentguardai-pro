"""
Full HTTP-level integration tests for apps/api -- register, login,
refresh rotation, logout, /me, and RBAC enforcement. Runs against the
REAL local PostgreSQL (docker compose up -d), through an in-process
ASGI transport (no real server process/socket needed) -- same idea as
Flask's test_client() in tests/test_server.py, for FastAPI. Skipped
(not failed) if the api extras aren't installed or Postgres isn't
reachable, since neither is guaranteed in every environment this suite
runs in yet.

Each test creates its own randomly-named tenant/users and deletes them
in tearDown -- this shares the real dev database rather than requiring
a separate test database, so cleanup discipline matters here.

This suite calls /auth/register and /auth/login far more often per
minute than any real client would, so rate limiting
(apps/api/rate_limit.py) is disabled here before apps.api.main is
imported -- must happen before that import, since the Limiter instance
is constructed at module load time. See test_rate_limit.py for the
suite that actually verifies the limiter blocks when enabled.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid

os.environ["RATE_LIMIT_ENABLED"] = "false"

try:
    import httpx
    from sqlalchemy import select

    from apps.api.main import app
    from infrastructure.auth.passwords import hash_password
    from infrastructure.database.models import Role, Tenant, User
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
    # The check above ran in its own (now-closed) event loop. asyncpg
    # connections can't be reused across event loops -- without disposing
    # the pool here, it can hand a test a connection bound to this dead
    # loop and crash deep inside asyncpg's socket layer. Same reasoning
    # applies per-test below (IsolatedAsyncioTestCase gives each test
    # method its own loop).
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestAuthFlow(unittest.IsolatedAsyncioTestCase):
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
        # IsolatedAsyncioTestCase gives every test method its own event
        # loop, and asyncpg connections aren't safe to reuse across loops
        # -- without disposing the pool here, the NEXT test can inherit a
        # connection bound to THIS (about to be closed) loop and crash
        # deep inside asyncpg's socket layer instead of failing cleanly.
        await engine.dispose()

    async def _register(self, **overrides) -> tuple[httpx.Response, dict]:
        body = {
            "tenant_name": "Test Co",
            "tenant_slug": f"test-{uuid.uuid4().hex[:10]}",
            "email": f"user+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
        body.update(overrides)
        resp = await self.client.post("/auth/register", json=body)
        if resp.status_code == 201:
            access = resp.json()["access_token"]
            me = await self.client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
            self._cleanup_tenant_ids.append(uuid.UUID(me.json()["tenant_id"]))
        return resp, body

    # --- registration ---

    async def test_register_returns_token_pair(self):
        resp, _ = await self._register()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertNotIn("refresh_token", data)
        self.assertIn("agentguard_refresh_token", self.client.cookies)

    async def test_duplicate_email_register_rejected(self):
        _, body = await self._register()
        resp2, _ = await self._register(
            tenant_slug=f"test-{uuid.uuid4().hex[:10]}", email=body["email"]
        )
        self.assertEqual(resp2.status_code, 409)

    async def test_duplicate_tenant_slug_rejected(self):
        _, body = await self._register()
        resp2, _ = await self._register(tenant_slug=body["tenant_slug"])
        self.assertEqual(resp2.status_code, 409)

    async def test_invalid_slug_rejected(self):
        resp, _ = await self._register(tenant_slug="Not A Valid Slug!")
        self.assertEqual(resp.status_code, 422)

    async def test_short_password_rejected(self):
        resp, _ = await self._register(password="short")
        self.assertEqual(resp.status_code, 422)

    # --- login ---

    async def test_login_wrong_password_rejected(self):
        _, body = await self._register()
        resp = await self.client.post(
            "/auth/login", json={"email": body["email"], "password": "wrong"}
        )
        self.assertEqual(resp.status_code, 401)

    async def test_login_nonexistent_email_rejected_same_as_wrong_password(self):
        resp = await self.client.post(
            "/auth/login", json={"email": "nobody-at-all@example.com", "password": "whatever"}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Invalid email or password")

    async def test_login_succeeds_with_correct_credentials(self):
        _, body = await self._register()
        resp = await self.client.post(
            "/auth/login", json={"email": body["email"], "password": body["password"]}
        )
        self.assertEqual(resp.status_code, 200)

    # --- /auth/me ---

    async def test_me_requires_a_token(self):
        resp = await self.client.get("/auth/me")
        self.assertEqual(resp.status_code, 401)

    async def test_me_rejects_garbage_token(self):
        resp = await self.client.get(
            "/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        self.assertEqual(resp.status_code, 401)

    async def test_me_returns_correct_user_and_owner_role(self):
        resp, body = await self._register()
        access = resp.json()["access_token"]
        me = await self.client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], body["email"])
        self.assertEqual(me.json()["roles"], ["OWNER"])

    # --- refresh / logout ---

    async def test_refresh_rotates_and_old_token_is_rejected(self):
        resp, _ = await self._register()
        old_refresh = self.client.cookies["agentguard_refresh_token"]
        refreshed = await self.client.post("/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(refreshed.status_code, 200)
        new_tokens = refreshed.json()
        self.assertNotEqual(new_tokens["access_token"], resp.json()["access_token"])
        self.assertNotEqual(self.client.cookies["agentguard_refresh_token"], old_refresh)

        replay = await self.client.post("/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(replay.status_code, 401)

    async def test_refresh_with_garbage_token_rejected(self):
        resp = await self.client.post("/auth/refresh", json={"refresh_token": "not-a-real-one"})
        self.assertEqual(resp.status_code, 401)

    async def test_logout_revokes_the_refresh_token(self):
        resp, _ = await self._register()
        old_refresh = self.client.cookies["agentguard_refresh_token"]
        logout = await self.client.post("/auth/logout", json={"refresh_token": old_refresh})
        self.assertEqual(logout.status_code, 204)
        reuse = await self.client.post("/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(reuse.status_code, 401)

    async def test_logout_with_unknown_token_still_returns_204(self):
        # Must not leak "did this token exist" via a different status code.
        resp = await self.client.post("/auth/logout", json={"refresh_token": "never-issued"})
        self.assertEqual(resp.status_code, 204)

    # --- RBAC enforcement (GET /tenant/members) ---

    async def test_owner_can_list_tenant_members(self):
        resp, _ = await self._register()
        access = resp.json()["access_token"]
        members = await self.client.get(
            "/tenant/members", headers={"Authorization": f"Bearer {access}"}
        )
        self.assertEqual(members.status_code, 200)
        self.assertEqual(len(members.json()), 1)

    async def test_tenant_members_requires_a_token(self):
        resp = await self.client.get("/tenant/members")
        self.assertEqual(resp.status_code, 401)

    async def test_viewer_cannot_list_tenant_members(self):
        resp, _ = await self._register()
        access = resp.json()["access_token"]
        me = await self.client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
        tenant_id = uuid.UUID(me.json()["tenant_id"])

        viewer_email = f"viewer+{uuid.uuid4().hex[:10]}@example.com"
        viewer_password = "another correct horse battery"
        async with SessionLocal() as session:
            viewer_role = (
                await session.execute(select(Role).where(Role.name == "VIEWER"))
            ).scalar_one()
            viewer = User(
                tenant_id=tenant_id,
                email=viewer_email,
                hashed_password=hash_password(viewer_password),
            )
            viewer.roles.append(viewer_role)
            session.add(viewer)
            await session.commit()

        login = await self.client.post(
            "/auth/login", json={"email": viewer_email, "password": viewer_password}
        )
        self.assertEqual(login.status_code, 200)
        viewer_access = login.json()["access_token"]

        members = await self.client.get(
            "/tenant/members", headers={"Authorization": f"Bearer {viewer_access}"}
        )
        self.assertEqual(members.status_code, 403)
        self.assertIn("tenant.admin", members.json()["detail"])


if __name__ == "__main__":
    unittest.main()
