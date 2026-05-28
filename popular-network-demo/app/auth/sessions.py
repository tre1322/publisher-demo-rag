"""Session token mint, hash, cookie helpers.

The cookie holds an opaque token (`secrets.token_urlsafe`). The DB stores its
sha256 hash — a DB leak doesn't hand attackers valid session cookies.

Cookie defaults (constants below are the knobs):
  - Name: popular_session
  - TTL: 14 days (long-lived; force-logout = DELETE the UserSession row)
  - HttpOnly: True (no JS access — XSS doesn't steal sessions)
  - SameSite: Lax (Strict would break invite-link login-on-arrival flow in H.1.6)
  - Secure: env-controlled (POPULAR_COOKIE_SECURE=1 in prod)
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session as DbSession

from ..models import User, UserSession

COOKIE_NAME = "popular_session"
SESSION_TTL = timedelta(days=14)
COOKIE_SECURE = os.environ.get("POPULAR_COOKIE_SECURE", "0") == "1"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_session(
    db: DbSession,
    user: User,
    *,
    active_business_id: Optional[int] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[str, UserSession]:
    """Create a new UserSession row + return (opaque_token, row).

    The opaque token is what goes in the cookie; only its hash is persisted.
    Caller is responsible for committing the DB and setting the cookie on the
    response.
    """
    token = secrets.token_urlsafe(32)
    row = UserSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        active_business_id=active_business_id,
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:64] or None,
        expires_at=datetime.utcnow() + SESSION_TTL,
    )
    db.add(row)
    db.flush()
    return token, row


def lookup_session(db: DbSession, token: Optional[str]) -> Optional[UserSession]:
    """Return the live UserSession matching `token`, or None.

    Expired sessions return None (and are NOT auto-deleted here — cleanup is a
    separate concern; see H.1.7 smoke or a future cron).
    """
    if not token:
        return None
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == _hash_token(token))
        .first()
    )
    if row is None:
        return None
    if row.expires_at < datetime.utcnow():
        return None
    return row


def touch_session(db: DbSession, row: UserSession) -> None:
    """Bump last_seen_at on each authed request. Cheap; lets us show active sessions later."""
    row.last_seen_at = datetime.utcnow()


def revoke_session(db: DbSession, token: str) -> bool:
    """Delete the session row for `token`. Returns True if a row was deleted."""
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == _hash_token(token))
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    return True


def revoke_all_user_sessions(db: DbSession, user_id: int) -> int:
    """Force-logout all sessions for a user. Returns deleted row count."""
    rows = db.query(UserSession).filter(UserSession.user_id == user_id).all()
    n = len(rows)
    for r in rows:
        db.delete(r)
    return n
