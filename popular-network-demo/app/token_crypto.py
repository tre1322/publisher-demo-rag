"""Phase 5a: ad-platform sign-in tokens are encrypted in the database.

A Meta or LinkedIn access token can spend a client's money, so it never sits
in the database (or in a backup of it) as plain text. Tokens are encrypted
with Fernet (AES + HMAC) using TOKEN_ENCRYPTION_KEY from the server's .env,
which is never in the database or its backups.

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rules:
  * With the key set, every token is stored as "enc:v1:<fernet token>".
  * Without it, only the demo's simulated tokens ("mock_...") may be stored;
    a real token raises TokenKeyMissing, so the connection fails loudly
    instead of quietly writing a spendable secret in plain text.
  * Rows written before encryption existed still read back (no prefix), and
    `encrypt_existing` rewrites them once the key is set.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

log = logging.getLogger("popular_network.token_crypto")

PREFIX = "enc:v1:"
ENV_KEY = "TOKEN_ENCRYPTION_KEY"


class TokenKeyMissing(RuntimeError):
    """A real token was about to be stored without TOKEN_ENCRYPTION_KEY."""


class TokenUnreadable(RuntimeError):
    """A stored token can't be decrypted with the current key."""


def _fernet():
    key = os.getenv(ENV_KEY, "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def is_configured() -> bool:
    return bool(os.getenv(ENV_KEY, "").strip())


def encrypt(value: Optional[str]) -> Optional[str]:
    if value is None or value == "" or value.startswith(PREFIX):
        return value
    f = _fernet()
    if f is None:
        if value.startswith("mock_"):
            return value
        raise TokenKeyMissing(
            f"{ENV_KEY} isn't set on the server, so ad-account tokens can't be stored safely. "
            "Set it before connecting a real ad account.")
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: Optional[str]) -> Optional[str]:
    if value is None or not value.startswith(PREFIX):
        return value
    f = _fernet()
    if f is None:
        raise TokenUnreadable(f"{ENV_KEY} is missing, so stored ad-account tokens can't be read.")
    from cryptography.fernet import InvalidToken

    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise TokenUnreadable("A stored ad-account token doesn't match TOKEN_ENCRYPTION_KEY. "
                              "Reconnect the account.") from e


class EncryptedText(TypeDecorator):
    """A Text column that encrypts on write and decrypts on read."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        return encrypt(value)

    def process_result_value(self, value: Any, dialect: Any) -> Optional[str]:
        # An unreadable token reads as "no token": that connection stops
        # working (owner reconnects), but pages that merely list connections
        # keep loading.
        try:
            return decrypt(value)
        except TokenUnreadable as e:
            log.error("%s", e)
            return None


def encrypt_existing(engine: Any) -> int:
    """Encrypt plain-text tokens left from before Phase 5a (needs the key).
    Raw SQL on purpose: the ORM would hand back already-decrypted values."""
    if not is_configured():
        return 0
    from sqlalchemy import text

    done = 0
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, oauth_token, refresh_token FROM ad_connections")).all()
        for row_id, access, refresh in rows:
            new_access, new_refresh = encrypt(access), encrypt(refresh)
            if (new_access, new_refresh) != (access, refresh):
                conn.execute(text("UPDATE ad_connections SET oauth_token = :a, refresh_token = :r WHERE id = :i"),
                             {"a": new_access, "r": new_refresh, "i": row_id})
                done += 1
    if done:
        log.info("Encrypted %d stored ad-account token row(s)", done)
    return done
