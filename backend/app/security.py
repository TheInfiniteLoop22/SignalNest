"""Password hashing and session-token helpers (ADR-008)."""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's current defaults

# Verified against when the email is unknown, so a login attempt costs the same either way.
_DUMMY_HASH = _hasher.hash("signalnest-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    """32 random bytes, URL-safe. Only the SHA-256 of this is stored."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    # A fast hash is fine here: the input is 256 bits of entropy, not a human password.
    return hashlib.sha256(token.encode()).digest()
