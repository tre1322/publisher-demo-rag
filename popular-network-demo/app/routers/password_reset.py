"""Forgot-password flow — /api/auth/password-reset/*  (Phase 1).

  POST /api/auth/password-reset/request   {email}            always 200; emails a link if the account exists
  GET  /api/auth/password-reset/lookup    ?token=...         is this link still good? (for the reset page)
  POST /api/auth/password-reset/confirm   {token, password}  set the new password, sign out everywhere

Rules:
  - The request answer never says whether an email has an account
    (no account-enumeration oracle). Inactive accounts get no email.
  - Links are single-use, expire after RESET_TTL, and only the sha256 of
    the token is stored. A new request retires older unused links.
  - Using a link deletes every session for that user, so a stolen session
    dies with the old password. The user then signs in normally.
  - Requests are throttled per IP and per email (in-process, like login).
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..email import send_password_reset_email
from ..models import PasswordReset, User, UserSession
from ..pwhash import hash_password

router = APIRouter(prefix="/auth/password-reset")

RESET_TTL = timedelta(minutes=60)
_WINDOW_SECS = 60 * 60
MAX_PER_IP = 10      # per hour
MAX_PER_EMAIL = 3    # per hour
_HITS: dict[str, deque[float]] = {}

GENERIC_REPLY = (
    "If that email has an account, a reset link is on its way. "
    "It works once and expires in 60 minutes."
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _over_limit(key: str, limit: int) -> bool:
    """Record a hit for `key`; True if it is now over `limit` per window."""
    now = time.time()
    bucket = _HITS.setdefault(key, deque())
    while bucket and bucket[0] < now - _WINDOW_SECS:
        bucket.popleft()
    bucket.append(now)
    return len(bucket) > limit


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _live_reset(db: Session, token: str) -> Optional[PasswordReset]:
    row = db.query(PasswordReset).filter(PasswordReset.token_hash == _hash(token)).first()
    if row is None or row.used_at is not None or row.expires_at < datetime.utcnow():
        return None
    return row


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'•' * max(1, len(name) - 1)}@{domain}"


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=120)
    password: str = Field(min_length=8, max_length=128)


@router.post("/request")
def request_reset(body: ResetRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower().strip()
    ip = _client_ip(request)
    if _over_limit(f"ip:{ip}", MAX_PER_IP) or _over_limit(f"email:{email}", MAX_PER_EMAIL):
        raise HTTPException(status_code=429, detail="Too many reset requests. Try again in an hour.")

    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active:
        return {"ok": True, "message": GENERIC_REPLY}

    now = datetime.utcnow()
    # Only the newest link works.
    db.query(PasswordReset).filter(
        PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
    ).update({PasswordReset.used_at: now})
    raw = f"pwr_{secrets.token_urlsafe(32)}"
    db.add(PasswordReset(
        user_id=user.id,
        token_hash=_hash(raw),
        created_at=now,
        expires_at=now + RESET_TTL,
        requested_ip=ip[:64],
    ))
    db.commit()
    send_password_reset_email(user.email, f"/reset-password?token={raw}",
                              minutes_valid=int(RESET_TTL.total_seconds() // 60))
    return {"ok": True, "message": GENERIC_REPLY}


@router.get("/lookup")
def lookup(token: str = Query(min_length=10, max_length=120), db: Session = Depends(get_db)) -> dict:
    row = _live_reset(db, token)
    if row is None:
        return {"valid": False}
    user = db.get(User, row.user_id)
    return {"valid": True, "email": _mask(user.email) if user else None}


@router.post("/confirm")
def confirm(body: ResetConfirm, db: Session = Depends(get_db)) -> dict:
    row = _live_reset(db, body.token)
    if row is None:
        raise HTTPException(
            status_code=400,
            detail="This reset link has expired or was already used. Ask for a new one.",
        )
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="This account can't be reset. Contact Amplafai.")
    user.password_hash = hash_password(body.password)
    row.used_at = datetime.utcnow()
    signed_out = db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.commit()
    return {"ok": True, "signedOutSessions": signed_out}
