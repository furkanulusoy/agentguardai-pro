"""
Secret encryption at rest -- connector credentials (OAuth tokens, API
keys) are never stored as plaintext in the database
(infrastructure/database/models/credential.py).

FernetSecretStore is symmetric encryption (`cryptography`'s Fernet:
AES-128-CBC + HMAC-SHA256, authenticated -- decryption fails loudly if
the ciphertext was tampered with) keyed from SECRET_ENCRYPTION_KEY in
.env. This is a real, working implementation for a single-instance
deployment, not a placeholder -- but it is explicitly NOT the end
state: a production, multi-instance deployment should swap this for a
cloud KMS/Vault-backed implementation behind the same SecretStore
Protocol (see docs/ROADMAP_TO_PRODUCTION.md's SecretStore hierarchy).
Nothing above this layer (Credential model, connectors) needs to change
to make that swap -- that's the point of the Protocol boundary.
"""
from __future__ import annotations

from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from infrastructure.config import settings


class SecretStore(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class FernetSecretStore:
    def __init__(self, key: str | None = None):
        self._fernet = Fernet((key or settings.secret_encryption_key).encode("utf-8"))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Could not decrypt secret -- wrong SECRET_ENCRYPTION_KEY, or the "
                "stored value was corrupted/tampered with"
            ) from exc


secret_store = FernetSecretStore()
