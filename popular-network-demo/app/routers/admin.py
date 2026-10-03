"""Admin console API — /api/admin/*  (Phase 1).

Amplafai's operators (User.is_superuser) set up and look after client
businesses here instead of hand-running seed scripts:

  GET  /api/admin/businesses                    every business + its people
  POST /api/admin/businesses                    create one (clean first day), optionally invite its owner
  PUT  /api/admin/businesses/{id}               edit profile, plan, website, demo flag
  PUT  /api/admin/businesses/{id}/voice-brief   store the synthesized brief (JSON)
  POST /api/admin/businesses/{id}/invites       invite someone to that business
  POST /api/admin/businesses/{id}/open          make it this session's active business
  GET  /api/admin/businesses/{id}/embed         chat-widget snippet for their website
  POST /api/admin/escalations/{id}/handled      close a "Talk to a human" request
  GET  /api/admin/businesses/{id}/export        download everything it owns (JSON)
  POST /api/admin/businesses/{id}/schedule-deletion   start the 30-day clock   {confirm_name}
  POST /api/admin/businesses/{id}/cancel-deletion     stop the clock
  POST /api/admin/businesses/{id}/delete-now          delete immediately     {confirm_name}

Everything here is superuser-only and reads across tenants on purpose
(superusers skip the ORM tenant filter), so each handler scopes by the
business id in the path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.permissions import VALID_ROLES
from ..auth.sessions import COOKIE_NAME, lookup_session
from ..data_lifecycle import cancel_deletion, export_business, purge_business, schedule_deletion
from ..db import get_db
from ..models import Business, BusinessUser, Escalation, Invite, User
from ..provisioning import create_business
from ..onboarding import get_state as get_onboarding_state
from ..onboarding import save_state as save_onboarding_state
from ..voice_brief import load_voice_brief, validate_brief
from .billing import TIER_LABELS, TIER_PRICES
from .invites import create_invite


def require_superuser(request: Request) -> int:
    if not getattr(request.state, "is_superuser", False):
        raise HTTPException(status_code=403, detail="Amplafai admins only.")
    return request.state.user_id


router = APIRouter(prefix="/admin", dependencies=[Depends(require_superuser)])


# ---------- helpers ----------

def _origin(url: str) -> str:
    """'example.com/about' → 'https://example.com'. Raises ValueError."""
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        raise ValueError(f"'{url}' doesn't look like a website address")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{port}"


def _widget_origins(origin: str) -> list[str]:
    """The site with and without www, so the widget works on both."""
    scheme, host = origin.split("://", 1)
    bare = host[4:] if host.startswith("www.") else host
    return [f"{scheme}://{bare}", f"{scheme}://www.{bare}"]


def _get_business(db: Session, business_id: int) -> Business:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")
    return biz


def _business_row(db: Session, biz: Business) -> dict[str, Any]:
    members = (
        db.query(BusinessUser, User)
        .join(User, User.id == BusinessUser.user_id)
        .filter(BusinessUser.business_id == biz.id)
        .order_by(User.email.asc())
        .all()
    )
    now = datetime.utcnow()
    pending = (
        db.query(Invite)
        .filter(
            Invite.business_id == biz.id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
        .order_by(Invite.created_at.desc())
        .all()
    )
    # "Talk to a human" requests nobody has handled yet. The dashboard tells
    # the owner Amplafai will reply, so they have to surface somewhere.
    open_requests = (
        db.query(Escalation)
        .filter(Escalation.business_id == biz.id, Escalation.handled_at.is_(None))
        .order_by(Escalation.created_at.desc())
        .limit(10)
        .all()
    )
    return {
        "id": biz.id,
        "slug": biz.slug,
        "name": biz.name,
        "owner": biz.owner,
        "location": biz.location,
        "publisher": biz.publisher,
        "phone": biz.phone,
        "tier": biz.tier,
        "tierLabel": biz.tier_label,
        "monthlyPrice": biz.monthly_price,
        "isDemo": bool(biz.is_demo),
        "website": biz.website,
        "deletionDueAt": biz.deletion_due_at.isoformat() if biz.deletion_due_at else None,
        "allowedOrigins": biz.allowed_origins_json or [],
        "enrolledAt": biz.enrolled_at.isoformat() if biz.enrolled_at else None,
        "hasVoiceBrief": load_voice_brief(biz) is not None,
        "voiceInterview": biz.voice_interview,
        "onboardingStatus": get_onboarding_state(biz)["status"],
        "members": [{"email": u.email, "role": bu.role, "active": u.is_active} for bu, u in members],
        "pendingInvites": [
            {"id": i.id, "email": i.email, "role": i.role, "expiresAt": i.expires_at.isoformat()}
            for i in pending
        ],
        "openRequests": [
            {"id": e.id, "message": e.message, "createdAt": e.created_at.isoformat()} for e in open_requests
        ],
    }


# ---------- schemas ----------

class CreateBusinessBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=120)
    location: str = Field(default="", max_length=160)
    publisher: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    tier: int = Field(default=2)
    website: Optional[str] = Field(default=None, max_length=200)
    owner_email: Optional[EmailStr] = None
    demo: bool = False


class UpdateBusinessBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    owner: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=160)
    publisher: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    tier: Optional[int] = None
    website: Optional[str] = Field(default=None, max_length=200)
    demo: Optional[bool] = None


class VoiceBriefBody(BaseModel):
    brief: Optional[dict[str, Any]] = None  # None clears it


class AdminInviteBody(BaseModel):
    email: EmailStr
    role: str = "owner"


class ConfirmNameBody(BaseModel):
    # Typed by the operator; must match the business name exactly, so a
    # deletion can't happen from a stray click on the wrong card.
    confirm_name: str


def _require_name(biz: Business, body: ConfirmNameBody) -> None:
    if body.confirm_name.strip() != biz.name:
        raise HTTPException(status_code=422, detail=f"Type the business name exactly ({biz.name}) to confirm.")


# ---------- routes ----------

@router.get("/businesses")
def list_businesses(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(Business).order_by(Business.id.asc()).all()
    return {"businesses": [_business_row(db, b) for b in rows]}


@router.post("/businesses")
def create(
    body: CreateBusinessBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if body.tier not in TIER_PRICES:
        raise HTTPException(status_code=422, detail=f"plan tier must be one of {sorted(TIER_PRICES)}")
    website = None
    if body.website and body.website.strip():
        try:
            website = _origin(body.website)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
    biz = create_business(
        db,
        name=body.name, owner=body.owner, location=body.location, publisher=body.publisher,
        phone=body.phone, tier=body.tier, website=website, demo=body.demo,
    )
    if website:
        biz.allowed_origins_json = _widget_origins(website)
    db.commit()
    invite = None
    if body.owner_email:
        invite = create_invite(db, business_id=biz.id, email=str(body.owner_email), role="owner",
                               created_by_user_id=user_id)
    return {"ok": True, "business": _business_row(db, biz), "invite": invite}


@router.put("/businesses/{business_id}")
def update(business_id: int, body: UpdateBusinessBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    for field in ("name", "owner", "location", "publisher", "phone"):
        value = getattr(body, field)
        if value is not None:
            setattr(biz, field, value.strip())
    if body.owner is not None:
        parts = body.owner.split()
        biz.owner_initials = ("".join(p[0] for p in parts[:2]) or "?").upper()
    if body.tier is not None:
        if body.tier not in TIER_PRICES:
            raise HTTPException(status_code=422, detail=f"plan tier must be one of {sorted(TIER_PRICES)}")
        biz.tier, biz.tier_label, biz.monthly_price = body.tier, TIER_LABELS[body.tier], TIER_PRICES[body.tier]
    if body.website is not None:
        if body.website.strip():
            try:
                biz.website = _origin(body.website)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from None
            biz.allowed_origins_json = _widget_origins(biz.website)
        else:
            biz.website = None
            biz.allowed_origins_json = None
    if body.demo is not None:
        biz.is_demo = body.demo
    db.commit()
    return {"ok": True, "business": _business_row(db, biz)}


@router.put("/businesses/{business_id}/voice-brief")
def set_voice_brief(business_id: int, body: VoiceBriefBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    if body.brief is None:
        biz.voice_brief_json = None
        biz.voice_interview = "pending"
    else:
        try:
            biz.voice_brief_json = validate_brief(body.brief)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        biz.voice_interview = "complete"
        # The recorded interview fills the same brief the setup wizard
        # would, so the owner isn't asked to do setup on top of it.
        state = get_onboarding_state(biz)
        if state["status"] not in ("done", "drafting", "planning"):
            state.update(status="done", error=None, source="interview")
            save_onboarding_state(biz, state)
    db.commit()
    return {"ok": True, "business": _business_row(db, biz)}


@router.post("/businesses/{business_id}/invites")
def invite(
    business_id: int,
    body: AdminInviteBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_business(db, business_id)
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {list(VALID_ROLES)}")
    return create_invite(db, business_id=business_id, email=str(body.email), role=body.role,
                         created_by_user_id=user_id)


@router.post("/businesses/{business_id}/open")
def open_business(business_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_business(db, business_id)
    token = request.cookies.get(COOKIE_NAME)
    session = lookup_session(db, token) if token else None
    if session is None:
        raise HTTPException(status_code=401, detail="session_expired")
    session.active_business_id = business_id
    db.commit()
    return {"ok": True, "activeBusinessId": business_id, "redirect": "/"}


@router.post("/escalations/{escalation_id}/handled")
def mark_handled(escalation_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    esc = db.get(Escalation, escalation_id)
    if esc is None:
        raise HTTPException(status_code=404, detail=f"request {escalation_id} not found")
    if esc.handled_at is None:
        esc.handled_at = datetime.utcnow()
        db.commit()
    return {"ok": True, "business": _business_row(db, _get_business(db, esc.business_id))}


@router.get("/businesses/{business_id}/export")
def export(business_id: int, db: Session = Depends(get_db)) -> JSONResponse:
    biz = _get_business(db, business_id)
    data = export_business(db, business_id)
    filename = f"{biz.slug}-export-{datetime.utcnow():%Y-%m-%d}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/businesses/{business_id}/schedule-deletion")
def schedule(business_id: int, body: ConfirmNameBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    _require_name(biz, body)
    schedule_deletion(db, biz)
    db.commit()
    return {"ok": True, "business": _business_row(db, biz)}


@router.post("/businesses/{business_id}/cancel-deletion")
def cancel(business_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    cancel_deletion(biz)
    db.commit()
    return {"ok": True, "business": _business_row(db, biz)}


@router.post("/businesses/{business_id}/delete-now")
def delete_now(business_id: int, body: ConfirmNameBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    _require_name(biz, body)
    counts = purge_business(db, business_id)
    return {"ok": True, "deleted": counts}


@router.get("/businesses/{business_id}/embed")
def embed_snippet(business_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = _get_business(db, business_id)
    base = str(request.base_url).rstrip("/")
    title = biz.name.replace("\\", "\\\\").replace("'", "\\'")
    snippet = (
        f'<script src="{base}/static/widget.js"></script>\n'
        "<script>\n"
        "  PopularNetworkWidget.init({\n"
        f"    businessId: {biz.id},\n"
        f"    apiBaseUrl: '{base}',\n"
        f"    title: 'Ask {title}',\n"
        "    greeting: 'Hi! How can we help today?'\n"
        "  });\n"
        "</script>"
    )
    return {"snippet": snippet, "allowedOrigins": biz.allowed_origins_json or []}
