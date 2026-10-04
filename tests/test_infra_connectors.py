"""
Proves two things permanently, not just by inspection:

1. connectors.base.BaseConnector really plugs into the EXISTING, tested
   agentguard.guardrail.AgentGuard/Policy engine -- a connector instance
   is just the `connector` object AgentGuard already wraps. This is the
   core architectural claim of the Connector Framework; if it stopped
   being true, everything built on top of it would be wrong.
2. infrastructure/database/models/credential.py really round-trips
   through the real database ENCRYPTED -- inserted via the ORM, read
   back with a separate query, and only readable as plaintext through
   infrastructure/secrets.decrypt(). A raw column read must not be the
   original plaintext.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid
from typing import Any

from agentguard import AgentGuard, ApprovalDenied, PermissionDenied, Policy
from connectors.base import BaseConnector

try:
    from sqlalchemy import text

    from infrastructure.database.models import Credential, Tenant
    from infrastructure.database.session import SessionLocal, check_database_connection, engine
    from infrastructure.secrets import secret_store

    INFRA_DB_AVAILABLE = True
except ImportError:
    INFRA_DB_AVAILABLE = False


def _db_reachable() -> bool:
    if not INFRA_DB_AVAILABLE:
        return False
    try:
        reachable = asyncio.run(check_database_connection())
    except Exception:
        return False
    asyncio.run(engine.dispose())  # see tests/test_infra_api.py for why
    return reachable


_DB_OK = _db_reachable()


class ExampleConnector(BaseConnector):
    """A minimal, test-only connector -- not one of the real ones
    (Gmail/GitHub/Slack/Outlook/SMS). Exists to exercise the base class
    against the real AgentGuard engine without needing a real external
    service or real credentials."""

    connector_type = "example"
    supported_actions = frozenset({"ping"})

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._connected = False

    def authenticate(self, credential: dict[str, Any]) -> None:
        self._api_key = credential["api_key"]

    def connect(self) -> None:
        if self._api_key is None:
            raise RuntimeError("authenticate() must be called before connect()")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def validate(self) -> bool:
        return self._connected and self._api_key is not None

    def ping(self) -> str:
        if not self._connected:
            raise RuntimeError("not connected")
        return "pong"

    def delete_everything(self) -> str:
        """Deliberately NOT in supported_actions/policy scope -- exists
        only so a test can prove AgentGuard blocks it anyway."""
        return "should never run"


class TestBaseConnectorLifecycle(unittest.TestCase):
    def test_lifecycle_without_agentguard(self):
        conn = ExampleConnector()
        self.assertFalse(conn.validate())
        conn.authenticate({"api_key": "test-key"})
        conn.connect()
        self.assertTrue(conn.validate())
        self.assertEqual(conn.ping(), "pong")
        conn.disconnect()
        self.assertFalse(conn.validate())

    def test_connect_before_authenticate_raises(self):
        conn = ExampleConnector()
        with self.assertRaises(RuntimeError):
            conn.connect()

    def test_disconnect_without_connect_is_safe(self):
        ExampleConnector().disconnect()  # must not raise


class TestConnectorThroughRealAgentGuard(unittest.TestCase):
    """The real proof: ExampleConnector wrapped by the real, unmodified
    AgentGuard/Policy from agentguard/ -- the exact same engine
    server/app.py and the demo scenarios use."""

    def setUp(self):
        self.connector = ExampleConnector()
        self.connector.authenticate({"api_key": "test-key"})
        self.connector.connect()

    def test_in_scope_action_succeeds_through_agentguard(self):
        policy = Policy(connector_name="example", task_scope={"ping"})
        guard = AgentGuard(connector=self.connector, policy=policy)
        self.assertEqual(guard.call("ping"), "pong")

    def test_action_outside_task_scope_is_blocked(self):
        # delete_everything() exists on the connector but is deliberately
        # not granted -- AgentGuard must refuse it even though the method
        # is technically callable.
        policy = Policy(connector_name="example", task_scope={"ping"})
        guard = AgentGuard(connector=self.connector, policy=policy)
        with self.assertRaises(PermissionDenied):
            guard.call("delete_everything")

    def test_sensitive_action_requires_approval(self):
        policy = Policy(
            connector_name="example", task_scope={"ping"}, sensitive_actions={"ping"}
        )
        guard = AgentGuard(
            connector=self.connector, policy=policy, approval_callback=lambda a, c: False
        )
        with self.assertRaises(ApprovalDenied):
            guard.call("ping")


@unittest.skipUnless(INFRA_DB_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
@unittest.skipUnless(_DB_OK, "PostgreSQL not reachable -- run 'docker compose up -d' first")
class TestCredentialEncryptionRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await engine.dispose()  # see tests/test_infra_api.py for why

    async def test_credential_is_stored_encrypted_and_decrypts_correctly(self):
        plaintext_token = f"real-oauth-token-{uuid.uuid4().hex}"
        async with SessionLocal() as session:
            tenant = Tenant(name="Cred Test Co", slug=f"cred-test-{uuid.uuid4().hex[:10]}")
            session.add(tenant)
            await session.flush()

            credential = Credential(
                tenant_id=tenant.id,
                connector_type="example",
                label="test credential",
                encrypted_secret=secret_store.encrypt(plaintext_token),
            )
            session.add(credential)
            await session.commit()
            credential_id = credential.id
            tenant_id = tenant.id

        try:
            # Fresh query, separate from the object above -- proves this
            # round-trips through the real database, not just in-memory.
            async with SessionLocal() as session:
                fetched = await session.get(Credential, credential_id)
                self.assertIsNotNone(fetched)
                self.assertNotEqual(fetched.encrypted_secret, plaintext_token)
                self.assertEqual(secret_store.decrypt(fetched.encrypted_secret), plaintext_token)

                # The raw column value in the database must not be the
                # plaintext either -- read it via a raw SQL query, bypassing
                # the ORM entirely, to be sure nothing is decrypting it
                # implicitly somewhere.
                raw = await session.execute(
                    text("SELECT encrypted_secret FROM credentials WHERE id = :id"),
                    {"id": credential_id},
                )
                raw_value = raw.scalar_one()
                self.assertNotIn(plaintext_token, raw_value)
        finally:
            async with SessionLocal() as session:
                tenant_obj = await session.get(Tenant, tenant_id)
                if tenant_obj is not None:
                    await session.delete(tenant_obj)
                cred_obj = await session.get(Credential, credential_id)
                if cred_obj is not None:
                    await session.delete(cred_obj)
                await session.commit()

    async def test_wrong_key_cannot_decrypt(self):
        from cryptography.fernet import Fernet

        from infrastructure.secrets.store import FernetSecretStore

        real_ciphertext = secret_store.encrypt("some secret")
        wrong_store = FernetSecretStore(key=Fernet.generate_key().decode())
        with self.assertRaises(ValueError):
            wrong_store.decrypt(real_ciphertext)


if __name__ == "__main__":
    unittest.main()
