from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_scanner():
    script = Path(__file__).parents[1] / "scripts" / "check-secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSecretScanner(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = _load_scanner()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_detects_credentials_without_returning_values(self) -> None:
        token = "ag" + "k_" + "A" * 40
        (self.root / "unsafe.py").write_text(f'KEY = "{token}"\n', encoding="utf-8")

        findings = self.scanner.scan(self.root)

        self.assertEqual(findings, [(Path("unsafe.py"), 1, "AgentGuard agent key")])
        self.assertNotIn(token, repr(findings))

    def test_allows_documentation_placeholders(self) -> None:
        (self.root / "README.md").write_text(
            "BURAYA AGENTGUARD AGENT ANAHTARINI KOPYALAYIN\n"
            "BURAYA KULLANDIĞINIZ YAPAY ZEKA APİSİ\n",
            encoding="utf-8",
        )

        self.assertEqual(self.scanner.scan(self.root), [])

    def test_ignores_virtual_environments(self) -> None:
        token = "ag" + "k_" + "A" * 40
        dependency = self.root / ".venv-tool" / "dependency.py"
        dependency.parent.mkdir()
        dependency.write_text(token, encoding="utf-8")

        self.assertEqual(self.scanner.scan(self.root), [])


if __name__ == "__main__":
    unittest.main()
