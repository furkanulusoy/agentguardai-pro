"""
Password hashing -- Argon2 (argon2id, OWASP's current recommendation),
via argon2-cffi. Not passlib: passlib's last release predates modern
Argon2 parameter guidance and the project is in low-maintenance mode;
argon2-cffi is maintained directly by the Argon2 reference implementers.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return _hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False
