"""
HTTP-level integration tests for the invite flow -- POST/GET/DELETE
/tenant/invites (apps/api/routers/tenant.py) and GET /auth/invites/{token}
+ POST /auth/accept-invite (apps/api/routers/auth.py). This is the
feature that lets a tenant grow past its first user: before it,
/auth/register always created a brand new tenant, with no way to add a
second person to an existing one (see CHANGELOG.md).

Same real-Postgres-or-skip discipline, and same rate-limit-disabled
reasoning, as test_infra_api.py -- see that file's module docstring.
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
class TestInvitations(unittest.IsolatedAsyncioTestCase):
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
        """Registers a fresh tenant + OWNER user. Returns (access_token, tenant_id)."""
        body = {
            "tenant_name": "Test Co",
            "tenant_slug": f"test-{uuid.uuid4().hex[:10]}",
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

    async def _make_viewer(self, tenant_id: uuid.UUID) -> str:
        email = f"viewer+{uuid.uuid4().hex[:10]}@example.com"
        password = "another correct horse battery"
        async with SessionLocal() as session:
            viewer_role = (
                await session.execute(select(Role).where(Role.name == "VIEWER"))
            ).scalar_one()
            viewer = User(tenant_id=tenant_id, email=email, hashed_password=hash_password(password))
            viewer.roles.append(viewer_role)
            session.add(viewer)
            await session.commit()
        login = await self.client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]

    async def _invite(self, access: str, email: str, role: str = "OPERATOR"):
        return await self.client.post(
            "/tenant/invites",
            json={"email": email, "role": role},
            headers=self._auth(access),
        )

    # --- POST /tenant/invites ---

    async def test_owner_can_create_invite(self):
        access, _tenant_id = await self._register()
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"

        resp = await self._invite(access, email)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["email"], email)
        self.assertEqual(data["role"], "OPERATOR")
        self.assertIsNotNone(data["invite_token"])

    async def test_viewer_cannot_create_invite(self):
        access, tenant_id = await self._register()
        viewer_access = await self._make_viewer(tenant_id)
        resp = await self._invite(viewer_access, "someone@example.com")
        self.assertEqual(resp.status_code, 403)

    async def test_cannot_invite_as_owner(self):
        access, _tenant_id = await self._register()
        resp = await self._invite(access, "wannabe-owner@example.com", role="OWNER")
        self.assertEqual(resp.status_code, 400)

    async def test_cannot_invite_an_already_registered_email(self):
        access, _tenant_id = await self._register()
        me = await self.client.get("/auth/me", headers=self._auth(access))
        resp = await self._invite(access, me.json()["email"])
        self.assertEqual(resp.status_code, 409)

    async def test_cannot_double_invite_the_same_pending_email(self):
        access, _tenant_id = await self._register()
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"
        first = await self._invite(access, email)
        self.assertEqual(first.status_code, 201)
        second = await self._invite(access, email)
        self.assertEqual(second.status_code, 409)

    # --- GET /tenant/invites ---

    async def test_list_invites_shows_pending_and_is_tenant_scoped(self):
        access_a, _tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        await self._invite(access_a, f"invitee+{uuid.uuid4().hex[:10]}@example.com")

        list_a = await self.client.get("/tenant/invites", headers=self._auth(access_a))
        list_b = await self.client.get("/tenant/invites", headers=self._auth(access_b))
        self.assertEqual(len(list_a.json()), 1)
        self.assertEqual(len(list_b.json()), 0)
        # The listing never leaks the raw token back out.
        self.assertIsNone(list_a.json()[0]["invite_token"])

    # --- DELETE /tenant/invites/{id} ---

    async def test_revoke_invite_removes_it_from_the_list(self):
        access, _tenant_id = await self._register()
        created = await self._invite(access, f"invitee+{uuid.uuid4().hex[:10]}@example.com")
        invite_id = created.json()["id"]

        revoke = await self.client.delete(
            f"/tenant/invites/{invite_id}", headers=self._auth(access)
        )
        self.assertEqual(revoke.status_code, 204)

        listed = await self.client.get("/tenant/invites", headers=self._auth(access))
        self.assertEqual(len(listed.json()), 0)

    async def test_revoke_invite_from_another_tenant_is_404(self):
        access_a, _tenant_a = await self._register()
        access_b, _tenant_b = await self._register()
        created = await self._invite(access_a, f"invitee+{uuid.uuid4().hex[:10]}@example.com")
        invite_id = created.json()["id"]

        resp = await self.client.delete(
            f"/tenant/invites/{invite_id}", headers=self._auth(access_b)
        )
        self.assertEqual(resp.status_code, 404)

    # --- GET /auth/invites/{token} ---

    async def test_preview_invite_returns_tenant_and_role(self):
        access, _tenant_id = await self._register(tenant_name="Acme Inc")
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"
        created = await self._invite(access, email, role="APPROVER")
        token = created.json()["invite_token"]

        preview = await self.client.get(f"/auth/invites/{token}")
        self.assertEqual(preview.status_code, 200)
        body = preview.json()
        self.assertEqual(body["email"], email)
        self.assertEqual(body["role"], "APPROVER")
        self.assertEqual(body["tenant_name"], "Acme Inc")

    async def test_preview_garbage_token_is_404(self):
        resp = await self.client.get("/auth/invites/not-a-real-token")
        self.assertEqual(resp.status_code, 404)

    # --- POST /auth/accept-invite ---

    async def test_accept_invite_creates_user_in_same_tenant_with_invited_role(self):
        access, tenant_id = await self._register()
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"
        created = await self._invite(access, email, role="OPERATOR")
        token = created.json()["invite_token"]

        accept = await self.client.post(
            "/auth/accept-invite",
            json={"invite_token": token, "password": "a brand new password"},
        )
        self.assertEqual(accept.status_code, 201)
        tokens = accept.json()
        self.assertIn("access_token", tokens)

        me = await self.client.get("/auth/me", headers=self._auth(tokens["access_token"]))
        self.assertEqual(me.json()["email"], email)
        self.assertEqual(me.json()["tenant_id"], str(tenant_id))
        self.assertEqual(me.json()["roles"], ["OPERATOR"])

        # The tenant now really has two members.
        members = await self.client.get("/tenant/members", headers=self._auth(access))
        self.assertEqual(len(members.json()), 2)

    async def test_accepted_invite_cannot_be_reused(self):
        access, _tenant_id = await self._register()
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"
        created = await self._invite(access, email)
        token = created.json()["invite_token"]

        first = await self.client.post(
            "/auth/accept-invite", json={"invite_token": token, "password": "first password ok"}
        )
        self.assertEqual(first.status_code, 201)

        second = await self.client.post(
            "/auth/accept-invite", json={"invite_token": token, "password": "second password ok"}
        )
        self.assertEqual(second.status_code, 400)

    async def test_accept_garbage_token_is_400(self):
        resp = await self.client.post(
            "/auth/accept-invite",
            json={"invite_token": "not-a-real-token", "password": "whatever password"},
        )
        self.assertEqual(resp.status_code, 400)

    async def test_new_member_can_log_in_after_accepting(self):
        access, _tenant_id = await self._register()
        email = f"invitee+{uuid.uuid4().hex[:10]}@example.com"
        created = await self._invite(access, email)
        token = created.json()["invite_token"]
        await self.client.post(
            "/auth/accept-invite", json={"invite_token": token, "password": "a login password"}
        )

        login = await self.client.post(
            "/auth/login", json={"email": email, "password": "a login password"}
        )
        self.assertEqual(login.status_code, 200)


if __name__ == "__main__":
    unittest.main()
