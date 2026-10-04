"""
Pure unit tests for infrastructure/auth -- no database, no network.
Always runnable; skipped only if the `api` extras aren't installed
(pip install -e ".[api]").
"""
from __future__ import annotations

import unittest
import uuid

try:
    from infrastructure.auth.jwt import (
        create_access_token,
        decode_access_token,
        generate_refresh_token,
        hash_refresh_token,
    )
    from infrastructure.auth.passwords import hash_password, verify_password

    INFRA_AUTH_AVAILABLE = True
except ImportError:
    INFRA_AUTH_AVAILABLE = False


@unittest.skipUnless(INFRA_AUTH_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
class TestPasswordHashing(unittest.TestCase):
    def test_hash_then_verify_succeeds(self):
        h = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", h))

    def test_wrong_password_fails(self):
        h = hash_password("correct horse battery staple")
        self.assertFalse(verify_password("wrong", h))

    def test_hash_is_not_the_plaintext(self):
        h = hash_password("secret")
        self.assertNotEqual(h, "secret")

    def test_same_password_hashes_differently_each_time(self):
        # Argon2 includes a random salt -- two hashes of the same password
        # must not be identical (this is what makes rainbow tables useless).
        h1 = hash_password("secret")
        h2 = hash_password("secret")
        self.assertNotEqual(h1, h2)
        self.assertTrue(verify_password("secret", h1))
        self.assertTrue(verify_password("secret", h2))


@unittest.skipUnless(INFRA_AUTH_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
class TestAccessTokens(unittest.TestCase):
    def test_roundtrip(self):
        uid, tid = uuid.uuid4(), uuid.uuid4()
        token = create_access_token(user_id=uid, tenant_id=tid)
        claims = decode_access_token(token)
        self.assertIsNotNone(claims)
        self.assertEqual(claims.user_id, uid)
        self.assertEqual(claims.tenant_id, tid)

    def test_garbage_token_returns_none(self):
        self.assertIsNone(decode_access_token("not-a-real-token"))

    def test_tampered_token_returns_none(self):
        token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        tampered = token[:-4] + "abcd"
        self.assertIsNone(decode_access_token(tampered))

    def test_two_tokens_for_same_user_are_distinct(self):
        # jti makes tokens unique even when issued in the same instant --
        # see infrastructure/auth/jwt.py for why that matters.
        uid, tid = uuid.uuid4(), uuid.uuid4()
        t1 = create_access_token(user_id=uid, tenant_id=tid)
        t2 = create_access_token(user_id=uid, tenant_id=tid)
        self.assertNotEqual(t1, t2)


@unittest.skipUnless(INFRA_AUTH_AVAILABLE, "api extras not installed (pip install -e '.[api]')")
class TestRefreshTokenGeneration(unittest.TestCase):
    def test_generate_returns_raw_and_matching_hash(self):
        raw, h = generate_refresh_token()
        self.assertNotEqual(raw, h)
        self.assertEqual(hash_refresh_token(raw), h)

    def test_two_generated_tokens_differ(self):
        raw1, _ = generate_refresh_token()
        raw2, _ = generate_refresh_token()
        self.assertNotEqual(raw1, raw2)


if __name__ == "__main__":
    unittest.main()
