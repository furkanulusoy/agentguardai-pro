"""Generate local-only infrastructure secrets without dotenv or console disclosure."""

import base64
import json
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directory = root / ".local"
directory.mkdir(exist_ok=True)
config = directory / "platform-config.json"
password_file = directory / "postgres-password"
if config.exists() != password_file.exists():
    raise SystemExit(
        "Incomplete local configuration; restore both files. Keys were not regenerated."
    )
if not config.exists():
    password = secrets.token_urlsafe(32)
    values = {
        "database_url": f"postgresql://agentguard:{password}@postgres:5432/agentguard",
        "jwt_secret_key": secrets.token_urlsafe(48),
        "secret_encryption_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    }
    password_file.write_text(password, encoding="utf-8")
    config.write_text(json.dumps(values), encoding="utf-8")
    password_file.chmod(0o600)
    config.chmod(0o600)
print("Local configuration ready. Secret values were not printed.")
