"""Auth router — /api/auth/* endpoints.

Endpoints:
  POST /api/auth/register     create a User (superuser-only for now)
  POST /api/auth/login        exchange email+password for session cookie
  POST /api/auth/logout       revoke current session
  POST /api/auth/logout-all   revoke ALL sessions for current user
  GET  /api/auth/me           current user + active business + role
  GET  /api/auth/businesses   list businesses the current user belongs to
  POST /api/auth/switch       set active_business_id on current session

Login throttle: per-IP, 5 failed attempts per 15 min → 429. Stored in-process
(dict) for the demo; swap to Redis when there's a real attack surface.
Per-email throttling deliberately NOT done — an attacker can otherwise lock
out any account by trying its email repeatedly.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.permissions import VALID_ROLES, can
from ..auth.sessions import (
    COOKIE_NAME,
    COOKIE_SECURE,
    SESSION_TTL,
    mint_session,
    revoke_all_user_sessions,
    revoke_session,
)
from ..db import get_db
from ..models import Business, BusinessUser, User
from ..pwhash import hash_password, verify_password

router = APIRouter(prefix="/auth")


# ---------- in-process IP throttle ----------
_FAIL_WINDOW_SECS = 15 * 60
_FAIL_MAX = 5
_FAILS: dict[str, deque[float]] = {}


def _record_fail(ip: str) -> None:
    bucket = _FAILS.setdefault(ip, deque())
    now = time.time()
    bucket.append(now)
    cutoff = now - _FAIL_WINDOW_SECS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _is_throttled(ip: str) -> bool:
    bucket = _FAILS.get(ip)
    if not bucket:
        return False
    cutoff = time.time() - _FAIL_WINDOW_SECS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return len(bucket) >= _FAIL_MAX


def _clear_fails(ip: str) -> None:
    _FAILS.pop(ip, None)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else "0.0.0.0")


# ---------- schemas ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)
    business_id: Optional[int] = None
    role: str = Field(default="owner")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    active_business_id: Optional[int] = None


class SwitchRequest(BaseModel):
    business_id: int


# ---------- endpoints ----------
@router.post("/register")
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Create a User. Superuser-only for now — pilot signup is via invite (H.1.6).

    The bootstrap exception: if there are zero users in the DB, anyone can
    create the first superuser. After that, regular invites only.
    """
    is_bootstrap = db.query(User).count() == 0
    if not is_bootstrap and not getattr(request.state, "is_superuser", False):
        raise HTTPException(403, detail="superuser_required")

    if body.role not in VALID_ROLES:
        raise HTTPException(400, detail=f"invalid_role: {body.role}")

    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(409, detail="email_exists")

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        is_superuser=is_bootstrap,  # first user is superuser
    )
    db.add(user)
    db.flush()

    if body.business_id is not None:
        if db.get(Business, body.business_id) is None:
            raise HTTPException(404, detail="business_not_found")
        bu = BusinessUser(user_id=user.id, business_id=body.business_id, role=body.role)
        db.add(bu)

    db.commit()
    return {
        "id": user.id,
        "email": user.email,
        "is_superuser": user.is_superuser,
        "bootstrapped": is_bootstrap,
    }


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    ip = _client_ip(request)
    if _is_throttled(ip):
        raise HTTPException(429, detail="too_many_attempts")

    email = body.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        _record_fail(ip)
        # Generic message — don't leak whether email exists.
        raise HTTPException(401, detail="invalid_credentials")

    # Pick active business: explicit request → membership check; otherwise the
    # user's first (lowest id) BusinessUser. Superusers can have zero.
    active_business_id = body.active_business_id
    if active_business_id is not None:
        membership = (
            db.query(BusinessUser)
            .filter(
                BusinessUser.user_id == user.id,
                BusinessUser.business_id == active_business_id,
            )
            .first()
        )
        if membership is None and not user.is_superuser:
            raise HTTPException(403, detail="not_a_member_of_business")
    else:
        first = (
            db.query(BusinessUser)
            .filter(BusinessUser.user_id == user.id)
            .order_by(BusinessUser.business_id.asc())
            .first()
        )
        active_business_id = first.business_id if first is not None else None

    token, _row = mint_session(
        db,
        user,
        active_business_id=active_business_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
    )
    user.last_login_at = datetime.utcnow()
    db.commit()
    _clear_fails(ip)

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return {
        "user_id": user.id,
        "email": user.email,
        "is_superuser": user.is_superuser,
        "active_business_id": active_business_id,
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    deleted = False
    if token:
        deleted = revoke_session(db, token)
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"logged_out": deleted}


@router.post("/logout-all")
def logout_all(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, detail="not_authenticated")
    n = revoke_all_user_sessions(db, user_id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"sessions_revoked": n}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, detail="not_authenticated")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, detail="not_authenticated")
    role = getattr(request.state, "user_role", None)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_superuser": user.is_superuser,
        "active_business_id": getattr(request.state, "business_id", None),
        "role": role,
        "capabilities": {
            "manage_billing": can(role or "", "manage_billing"),
            "manage_invites": can(role or "", "manage_invites"),
            "manage_settings": can(role or "", "manage_settings"),
            "publish_post": can(role or "", "publish_post"),
            "manage_ads": can(role or "", "manage_ads"),
        },
    }


@router.get("/businesses")
def my_businesses(request: Request, db: Session = Depends(get_db)) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, detail="not_authenticated")
    rows = (
        db.query(BusinessUser, Business)
        .join(Business, Business.id == BusinessUser.business_id)
        .filter(BusinessUser.user_id == user_id)
        .order_by(Business.name.asc())
        .all()
    )
    return {
        "businesses": [
            {
                "id": biz.id,
                "name": biz.name,
                "slug": biz.slug,
                "role": bu.role,
                "tier": biz.tier,
            }
            for bu, biz in rows
        ],
        "active_business_id": getattr(request.state, "business_id", None),
    }


@router.post("/switch")
def switch_business(
    body: SwitchRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, detail="not_authenticated")
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(401, detail="not_authenticated")
    # Re-lookup the session for mutation (middleware closes its session).
    from ..auth.sessions import lookup_session
    s = lookup_session(db, token)
    if s is None:
        raise HTTPException(401, detail="session_expired")

    bu = (
        db.query(BusinessUser)
        .filter(
            BusinessUser.user_id == user_id,
            BusinessUser.business_id == body.business_id,
        )
        .first()
    )
    if bu is None:
        is_superuser = getattr(request.state, "is_superuser", False)
        if not is_superuser:
            raise HTTPException(403, detail="not_a_member_of_business")

    s.active_business_id = body.business_id
    db.commit()
    return {"active_business_id": body.business_id}
