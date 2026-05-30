"""Invite flow — /api/auth/invites/*  (Phase H.1.6).

Endpoints:
  POST   /api/auth/invites          mint a new invite (auth + can('manage_invites'))
  GET    /api/auth/invites          list invites for current business
  DELETE /api/auth/invites/{id}     revoke an unaccepted invite
  GET    /api/auth/invites/lookup   public — preview invite by token (for claim page)
  POST   /api/auth/invites/claim    public — claim invite + mint session

The mint endpoint returns the raw token ONCE. After that, only prefix is
shown — same pattern as ChatbotIngestionKey / UserSession (token in cookie).

Claim flow:
  1. Owner mints invite, sends /invite?token=cbi_live_xxxxx_yyy URL to invitee
  2. Invitee opens URL → /invite page calls /api/auth/invites/lookup → shows
     "Join {publisher} as {role} for {email}" with password input
  3. Invitee submits → /api/auth/invites/claim creates User, BusinessUser,
     marks invite accepted, mints session cookie → redirect to /
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.deps import get_tenant_id
from ..auth.permissions import VALID_ROLES, can
from ..auth.sessions import (
    COOKIE_NAME,
    COOKIE_SECURE,
    SESSION_TTL,
    mint_session,
)
from ..db import get_db
from ..email import send_invite_email
from ..models import Business, BusinessUser, Invite, User
from ..pwhash import hash_password

router = APIRouter(prefix="/auth/invites")

INVITE_TTL = timedelta(days=14)
TOKEN_PREFIX_LEN = 12


def _mint_token() -> tuple[str, str, str]:
    """Return (raw_token, prefix, sha256_hash). Raw token is shown once."""
    body = secrets.token_urlsafe(24)
    raw = f"inv_live_{body}"
    return raw, raw[:TOKEN_PREFIX_LEN], hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lookup_invite(db: Session, raw_token: str) -> Optional[Invite]:
    """Resolve raw token -> live Invite row, or None if missing/expired/used/revoked."""
    h = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    row = db.query(Invite).filter(Invite.token_hash == h).first()
    if row is None:
        return None
    now = datetime.utcnow()
    if row.accepted_at is not None or row.revoked_at is not None or row.expires_at < now:
        return None
    return row


# ---------- schemas ----------
class MintRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="owner")


class ClaimRequest(BaseModel):
    token: str = Field(min_length=10, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)


# ---------- owner-side endpoints (auth + role-gated) ----------
@router.post("")
def mint_invite(
    body: MintRequest,
    request: Request,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "not_authenticated")
    role = getattr(request.state, "user_role", None)
    is_super = getattr(request.state, "is_superuser", False)
    if not is_super and not can(role or "", "manage_invites"):
        raise HTTPException(403, "manage_invites_required")

    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"invalid_role: {body.role}")

    email = body.email.lower().strip()
    # Don't allow inviting an email that's already a member.
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user is not None:
        is_member = (
            db.query(BusinessUser)
            .filter(
                BusinessUser.user_id == existing_user.id,
                BusinessUser.business_id == business_id,
            )
            .first()
        )
        if is_member is not None:
            raise HTTPException(409, "already_a_member")

    raw, prefix, hsh = _mint_token()
    row = Invite(
        business_id=business_id,
        email=email,
        role=body.role,
        token_prefix=prefix,
        token_hash=hsh,
        created_by_user_id=user_id,
        expires_at=datetime.utcnow() + INVITE_TTL,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Best-effort email delivery — failures don't roll back the invite mint.
    # If POSTMARK_API_KEY is unset (local dev) this is a no-op + returns
    # {"sent": False, "reason": "no_api_key"} so the owner can copy the URL.
    biz = db.get(Business, business_id)
    business_name = biz.name if biz else "your business"
    claim_url = f"/invite?token={raw}"
    delivery = send_invite_email(
        to_email=row.email,
        claim_url=claim_url,
        role=row.role,
        business_name=business_name,
    )

    return {
        "id": row.id,
        "email": row.email,
        "role": row.role,
        "tokenPrefix": row.token_prefix,
        "rawToken": raw,   # shown ONCE — owner copies the URL
        "claimUrl": claim_url,
        "expiresAt": row.expires_at.isoformat(),
        "emailDelivery": delivery,
    }


@router.get("")
def list_invites(
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict:
    rows = (
        db.query(Invite)
        .filter(Invite.business_id == business_id)
        .order_by(Invite.created_at.desc())
        .all()
    )
    now = datetime.utcnow()
    return {
        "invites": [
            {
                "id": r.id,
                "email": r.email,
                "role": r.role,
                "tokenPrefix": r.token_prefix,
                "createdAt": r.created_at.isoformat(),
                "expiresAt": r.expires_at.isoformat(),
                "acceptedAt": r.accepted_at.isoformat() if r.accepted_at else None,
                "revokedAt": r.revoked_at.isoformat() if r.revoked_at else None,
                "status": (
                    "accepted" if r.accepted_at else
                    "revoked" if r.revoked_at else
                    "expired" if r.expires_at < now else
                    "pending"
                ),
            }
            for r in rows
        ],
    }


@router.delete("/{invite_id}")
def revoke_invite(
    invite_id: int,
    request: Request,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict:
    role = getattr(request.state, "user_role", None)
    is_super = getattr(request.state, "is_superuser", False)
    if not is_super and not can(role or "", "manage_invites"):
        raise HTTPException(403, "manage_invites_required")

    row = (
        db.query(Invite)
        .filter(Invite.id == invite_id, Invite.business_id == business_id)
        .first()
    )
    if row is None:
        raise HTTPException(404, "invite_not_found")
    if row.accepted_at is not None:
        raise HTTPException(409, "already_accepted")
    if row.revoked_at is not None:
        return {"revoked": False, "reason": "already_revoked"}
    row.revoked_at = datetime.utcnow()
    db.commit()
    return {"revoked": True}


# ---------- public claim endpoints (no auth required) ----------
@router.get("/lookup")
def lookup_invite_public(
    token: str,
    db: Session = Depends(get_db),
) -> dict:
    inv = _lookup_invite(db, token)
    if inv is None:
        raise HTTPException(404, "invite_not_found_or_invalid")
    biz = db.get(Business, inv.business_id)
    if biz is None:
        raise HTTPException(404, "business_not_found")
    return {
        "businessName": biz.name,
        "publisher": biz.publisher,
        "email": inv.email,
        "role": inv.role,
        "expiresAt": inv.expires_at.isoformat(),
    }


@router.post("/claim")
def claim_invite_public(
    body: ClaimRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    inv = _lookup_invite(db, body.token)
    if inv is None:
        raise HTTPException(404, "invite_not_found_or_invalid")

    # Existing user with this email? Link rather than create. New user? Create.
    user = db.query(User).filter(User.email == inv.email).first()
    if user is None:
        user = User(
            email=inv.email,
            password_hash=hash_password(body.password),
            display_name=body.display_name,
        )
        db.add(user)
        db.flush()
    else:
        # Don't silently overwrite an existing user's password — that'd be a
        # takeover vector if an attacker guessed an existing email. They must
        # log in with their existing password instead.
        raise HTTPException(409, "email_already_registered_please_login")

    # Attach to business.
    bu = (
        db.query(BusinessUser)
        .filter(BusinessUser.user_id == user.id, BusinessUser.business_id == inv.business_id)
        .first()
    )
    if bu is None:
        bu = BusinessUser(
            user_id=user.id,
            business_id=inv.business_id,
            role=inv.role,
            invited_by_user_id=inv.created_by_user_id,
        )
        db.add(bu)

    # Mark invite accepted.
    inv.accepted_at = datetime.utcnow()
    inv.accepted_by_user_id = user.id

    # Mint session immediately so the user lands on the dashboard logged in.
    token, _ = mint_session(
        db,
        user,
        active_business_id=inv.business_id,
        user_agent=request.headers.get("user-agent"),
        ip_address=(request.client.host if request.client else None),
    )
    user.last_login_at = datetime.utcnow()
    db.commit()

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
        "active_business_id": inv.business_id,
        "role": inv.role,
    }
