"""Fail CI when source files contain credentials that look real.

Only file paths and line numbers are reported. Secret values are never printed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

# Split provider prefixes so this scanner does not flag its own source.
PATTERNS = {
    "AgentGuard agent key": re.compile("ag" + r"k_[A-Za-z0-9_-]{30,}"),
    "NVIDIA API key": re.compile("nv" + r"api-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile("sk" + r"-[A-Za-z0-9_-]{30,}"),
    "GitHub token": re.compile("gh" + r"[pousr]_[A-Za-z0-9]{30,}"),
    "Slack token": re.compile("xo" + r"[xbaprs]-[A-Za-z0-9-]{20,}"),
    "private key": re.compile("BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY"),
}


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in root.rglob("*"):
        excluded = any(
            part in EXCLUDED_DIRS or part.startswith(".venv-") for part in path.parts
        )
        if not path.is_file() or excluded:
            continue
        if path.name.startswith(".env") or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path.relative_to(root), line_number, label))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan(root)
    for path, line_number, label in findings:
        print(f"{path}:{line_number}: possible {label}")
    if findings:
        print("Secret scan failed. Revoke exposed credentials and remove them from source.")
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
