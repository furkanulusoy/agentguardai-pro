"""Static contract checks that need neither configuration nor credentials."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class IdentityContract(unittest.TestCase):
    def test_refresh_not_in_response_schema(self):
        source = (ROOT / "apps/api/routers/auth.py").read_text(encoding="utf-8")
        schema = source.split("class TokenPairResponse")[1].split("class UserResponse")[0]
        self.assertNotIn("refresh_token:", schema)

    def test_no_empty_grant_inheritance(self):
        source = (ROOT / "apps/api/dependencies.py").read_text(encoding="utf-8")
        self.assertNotIn("if agent_scoped:", source)

    def test_browser_has_no_persistent_access_token(self):
        source = (ROOT / "apps/web/src/api/client.ts").read_text(encoding="utf-8")
        self.assertNotIn("localStorage.setItem", source)
