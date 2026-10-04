"""
Configuration from OS environment or AGENTGUARD_SECRET_FILE. Environment overrides file
values. Dotenv files are never read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    deployment_mode: str = "local"
    database_url: str
    jwt_secret_key: str
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30
    secret_encryption_key: str

    # Optional, unlike the above: this app must always have a database and
    # its own secrets to run at all, but a given deployment may not have
    # every connector configured. Gmail routes check for these themselves
    # and fail clearly (not at import time) if they're missing.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None

    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    github_oauth_redirect_uri: str | None = None

    slack_oauth_client_id: str | None = None
    slack_oauth_client_secret: str | None = None
    slack_oauth_redirect_uri: str | None = None

    # Comma-separated origins allowed to call the API from a browser.
    # Not a security boundary on its own -- CORS only constrains
    # browsers, not curl/server-to-server calls -- but still shouldn't
    # be hard-coded into main.py. Covers both ways this platform is
    # actually run: apps/web's own Vite dev server (5173) and the
    # Docker self-host stack's nginx-served build (docker-compose.yml's
    # `web` service, port 80) -- found missing the second one while
    # actually registering a workspace through the Docker stack for the
    # first time, not by inspection: a real CORS preflight rejection,
    # not a hypothetical gap.
    cors_allowed_origins: str = "http://localhost:5173,http://localhost"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # Per-IP throttling on /auth/* (apps/api/rate_limit.py) -- true in every
    # real deployment. Only ever false in tests/CI, which legitimately call
    # /auth/register and /auth/login far more often per minute than any real
    # client would (see tests/test_infra_api.py).
    rate_limit_enabled: bool = True

    # The refresh-token cookie's Secure attribute (apps/api/routers/auth.py)
    # -- browsers refuse to set a Secure cookie over plain http://, which
    # this app's own local dev server (http://localhost:8000) is. False
    # here is a deliberate dev-only default; any real deployment (HTTPS)
    # must set this true, or the cookie never gets set at all.
    cookie_secure: bool = False


_secret_path = os.environ.get("AGENTGUARD_SECRET_FILE")
_config_values = json.loads(Path(_secret_path).read_text()) if _secret_path else {}
_config_values = {k: v for k, v in _config_values.items() if k.upper() not in os.environ}
settings = Settings(**_config_values)
if settings.deployment_mode == "production" and (
    not settings.cookie_secure or len(settings.jwt_secret_key) < 32
):
    raise RuntimeError("Production requires secure cookies and a strong signing key")
