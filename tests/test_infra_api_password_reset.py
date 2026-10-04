"""
HTTP-level integration tests for the admin-initiated password reset
flow -- POST /tenant/members/{user_id}/reset-password
(apps/api/routers/tenant.py) and GET /auth/password-reset/{token} +
POST /auth/reset-password (apps/api/routers/auth.py). Same shape as
the invite flow, for a locked-out EXISTING teammate rather than a new
one -- see infrastructure/database/models/password_reset.py's
docstring for why this is admin-initiated, not self-service.

Same real-Postgres-or-skip discipline, and same rate-limit-disabled
reasoning, as test_infra_api.py.
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
    asyncio.run(engine.dispose())
    return reachable


_DB_OK = _db_reachable()


@unittest.skipUnless(API_IMPORTS_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestPasswordReset(unittest.IsolatedAsyncioTestCase):
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

    def _auth(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _register(self, **overrides) -> tuple[str, uuid.UUID]:
        body = {
            "tenant_name": "Reset Test Co",
            "tenant_slug": f"reset-{uuid.uuid4().hex[:10]}",
            "email": f"owner+{uuid.uuid4().hex[:10]}@example.com",
            "password": "correct horse battery staple",
        }
        body.update(overrides)
        resp = await self.client.post("/auth/register", json=body)
        assert resp.status_code == 201, resp.text
        access = resp.json()["access_token"]
        me = await self.client.get("/auth/me", headers=self._auth(access))
        tenant_id = uuid.UUID(me.json()["tenant_id"])
        self._cleanup_tenant_ids.append(tenant_id)
        return access, tenant_id

    async def _make_member(
        self,
        tenant_id: uuid.UUID,
        role_name: str = "OPERATOR",
        password: str = "a starting password",
    ) -> tuple[uuid.UUID, str, str]:
        """Returns (user_id, email, password) for a freshly created member."""
        email = f"member+{uuid.uuid4().hex[:10]}@example.com"
        async with SessionLocal() as session:
            role = (await session.execute(select(Role).where(Role.name == role_name))).scalar_one()
            member = User(tenant_id=tenant_id, email=email, hashed_password=hash_password(password))
            member.roles.append(role)
            session.add(member)
            await session.commit()
            await session.refresh(member)
            return member.id, email, password

    async def _trigger_reset(self, access: str, user_id: uuid.UUID):
        return await self.client.post(
            f"/tenant/members/{user_id}/reset-password", headers=self._auth(access)
        )

    # --- POST /tenant/members/{id}/reset-password ---

    async def test_owner_can_trigger_reset_for_a_member(self):
        access, tenant_id = await self._register()
        user_id, _email, _pw = await self._make_member(tenant_id)

        resp = await self._trigger_reset(access, user_id)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()["reset_token"])

    async def test_member_cannot_trigger_reset(self):
        access, tenant_id = await self._register()
        user_id, email, password = await self._make_member(tenant_id, role_name="VIEWER")
        login = await self.client.post("/auth/login", json={"email": email, "password": password})
        member_access = login.json()["access_token"]

        resp = await self._trigger_reset(member_access, user_id)
        self.assertEqual(resp.status_code, 403)

    async def test_cannot_trigger_reset_for_another_tenant(self):
        access_a, _tenant_a = await self._register()
        _access_b, tenant_b = await self._register()
        user_id_b, _email, _pw = await self._make_member(tenant_b)

        resp = await self._trigger_reset(access_a, user_id_b)
        self.assertEqual(resp.status_code, 404)

    async def test_admin_cannot_reset_the_owner(self):
        access, tenant_id = await self._register()
        me = await self.client.get("/auth/me", headers=self._auth(access))
        owner_id = uuid.UUID(me.json()["id"])

        admin_id, admin_email, admin_password = await self._make_member(
            tenant_id, role_name="ADMIN"
        )
        login = await self.client.post(
            "/auth/login", json={"email": admin_email, "password": admin_password}
        )
        admin_access = login.json()["access_token"]

        resp = await self._trigger_reset(admin_access, owner_id)
        self.assertEqual(resp.status_code, 403)

    # --- GET /auth/password-reset/{token} ---

    async def test_preview_reset_returns_the_target_email(self):
        access, tenant_id = await self._register()
        user_id, email, _pw = await self._make_member(tenant_id)
        created = await self._trigger_reset(access, user_id)
        token = created.json()["reset_token"]

        preview = await self.client.get(f"/auth/password-reset/{token}")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["email"], email)

    async def test_preview_garbage_token_is_404(self):
        resp = await self.client.get("/auth/password-reset/not-a-real-token")
        self.assertEqual(resp.status_code, 404)

    # --- POST /auth/reset-password ---

    async def test_reset_password_logs_in_with_the_new_password(self):
        access, tenant_id = await self._register()
        user_id, email, _old_password = await self._make_member(tenant_id)
        created = await self._trigger_reset(access, user_id)
        token = created.json()["reset_token"]

        resolved = await self.client.post(
            "/auth/reset-password",
            json={"reset_token": token, "new_password": "a brand new password"},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertIn("access_token", resolved.json())

        login = await self.client.post(
            "/auth/login", json={"email": email, "password": "a brand new password"}
        )
        self.assertEqual(login.status_code, 200)

    async def test_old_password_no_longer_works_after_reset(self):
        access, tenant_id = await self._register()
        user_id, email, old_password = await self._make_member(tenant_id)
        created = await self._trigger_reset(access, user_id)
        token = created.json()["reset_token"]

        await self.client.post(
            "/auth/reset-password",
            json={"reset_token": token, "new_password": "a brand new password"},
        )

        login = await self.client.post(
            "/auth/login", json={"email": email, "password": old_password}
        )
        self.assertEqual(login.status_code, 401)

    async def test_reset_token_cannot_be_reused(self):
        access, tenant_id = await self._register()
        user_id, _email, _pw = await self._make_member(tenant_id)
        created = await self._trigger_reset(access, user_id)
        token = created.json()["reset_token"]

        first = await self.client.post(
            "/auth/reset-password",
            json={"reset_token": token, "new_password": "first new password"},
        )
        self.assertEqual(first.status_code, 200)

        second = await self.client.post(
            "/auth/reset-password",
            json={"reset_token": token, "new_password": "second new password"},
        )
        self.assertEqual(second.status_code, 400)

    async def test_reset_revokes_existing_refresh_tokens(self):
        access, tenant_id = await self._register()
        user_id, email, password = await self._make_member(tenant_id)
        login = await self.client.post("/auth/login", json={"email": email, "password": password})
        self.assertEqual(login.status_code, 200)
        old_refresh_token = self.client.cookies["agentguard_refresh_token"]

        created = await self._trigger_reset(access, user_id)
        token = created.json()["reset_token"]
        await self.client.post(
            "/auth/reset-password",
            json={"reset_token": token, "new_password": "a brand new password"},
        )

        replay = await self.client.post("/auth/refresh", json={"refresh_token": old_refresh_token})
        self.assertEqual(replay.status_code, 401)

    async def test_reset_garbage_token_is_400(self):
        resp = await self.client.post(
            "/auth/reset-password",
            json={"reset_token": "not-a-real-token", "new_password": "whatever password"},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
