"""Password hashing — thin wrapper around `bcrypt`.

Why a wrapper:
- Contains the bytes/str dance in one place so call sites stay readable.
- Single knob (`BCRYPT_COST`) for tuning cost factor across the codebase.
- Lets us swap to argon2 / scrypt later without touching every router.

Cost factor rationale:
- 12 rounds is the modern OWASP baseline; ~250 ms per hash on a 2026 laptop.
- Bumping to 14 quadruples cost (~1 s) — worth it on real prod hardware but
  noticeably slows login + the auth smoke test suite. Sticking with 12 until a
  pilot tells us otherwise.
"""
from __future__ import annotations

import bcrypt

BCRYPT_COST = 12


def hash_password(plain: str) -> str:
    """Return a bcrypt hash (utf-8 string) for the given plain-text password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_COST)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check of `plain` against a stored bcrypt hash. Returns False on any malformed input."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
