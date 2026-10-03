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

import os
from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth.permissions import VALID_ROLES
from ..auth.sessions import COOKIE_NAME, lookup_session
from ..data_lifecycle import cancel_deletion, export_business, purge_business, schedule_deletion
from ..db import current_tenant_id, get_db
from ..models import Business, BusinessUser, Escalation, Invite, User
from ..provisioning import create_business
from .. import managed_ads, subscriptions
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
        "billing": _billing_row(db, biz),
        "members": [{"email": u.email, "role": bu.role, "active": u.is_active} for bu, u in members],
        "pendingInvites": [
            {"id": i.id, "email": i.email, "role": i.role, "expiresAt": i.expires_at.isoformat()}
            for i in pending
        ],
        "openRequests": [
            {"id": e.id, "message": e.message, "createdAt": e.created_at.isoformat()} for e in open_requests
        ],
    }


def _billing_row(db: Session, biz: Business) -> dict[str, Any]:
    state = subscriptions.billing_state(db, biz)
    sub = subscriptions.get_subscription(db, biz.id)
    key = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_base = "https://dashboard.stripe.com/test" if key.startswith("sk_test") else "https://dashboard.stripe.com"
    return {
        "mode": state["mode"],
        "state": state["state"],
        "status": state["status"],
        "configured": state["configured"],
        "currentPeriodEnd": state["currentPeriodEnd"],
        "cancelAtPeriodEnd": state["cancelAtPeriodEnd"],
        "graceEndsAt": state["graceEndsAt"],
        "stripeCustomerUrl": f"{stripe_base}/customers/{sub.stripe_customer_id}" if sub and sub.stripe_customer_id else None,
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
    billing_mode: Literal["stripe", "outside"] = "stripe"


class UpdateBusinessBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    owner: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=160)
    publisher: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    tier: Optional[int] = None
    website: Optional[str] = Field(default=None, max_length=200)
    demo: Optional[bool] = None
    billing_mode: Optional[Literal["stripe", "outside"]] = None


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
        billing_mode=body.billing_mode,
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
    if body.billing_mode is not None:
        biz.billing_mode = body.billing_mode
    if body.tier is not None:
        if body.tier not in TIER_PRICES:
            raise HTTPException(status_code=422, detail=f"plan tier must be one of {sorted(TIER_PRICES)}")
        if body.tier != biz.tier and subscriptions.get_subscription(db, biz.id) is not None \
                and (subscriptions.get_subscription(db, biz.id).status or "") not in ("", "canceled", "incomplete_expired"):
            # A paying client's plan follows Stripe; changing it here would
            # put the dashboard out of step with what they're charged.
            raise HTTPException(status_code=409, detail="This client pays by card, so change the plan in Stripe "
                                                        "(or have them use Manage billing).")
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


class PreviewBody(BaseModel):
    kind: str


@router.post("/businesses/{business_id}/notifications/preview")
def preview_notification(
    business_id: int,
    body: PreviewBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Email the signed-in admin (not the client) what this business's
    reminder or weekly summary looks like right now. No timing checks, no
    state change, and no new agent suggestion is drafted into their queue."""
    from datetime import datetime as _dt

    from .. import notifications

    biz = _get_business(db, business_id)
    me = db.get(User, user_id)
    if body.kind == "approvals":
        res = notifications.send_approvals_reminder(db, biz, to=[me.email])
        if res.get("reason") == "nothing_pending":
            raise HTTPException(status_code=409, detail="Nothing is waiting for approval, so there's no reminder to send.")
    elif body.kind == "weekly":
        res = notifications.send_weekly_summary(db, biz, _dt.utcnow(), to=[me.email], suggest=False)
        db.rollback()
    else:
        raise HTTPException(status_code=422, detail="kind must be 'approvals' or 'weekly'")
    first = (res.get("results") or [{}])[0]
    return {"ok": bool(res.get("sent")), "to": me.email, "reason": first.get("reason")}


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


# --------------------------------------------------------------------------- #
# Phase 4: managed ad campaigns. Amplafai launches, pauses, restarts and
# cancels real clients' campaigns by hand in Ads Manager, then confirms here.
# --------------------------------------------------------------------------- #

class AdRequestDoneBody(BaseModel):
    external_campaign_id: Optional[str] = Field(default=None, max_length=80)
    note: Optional[str] = Field(default=None, max_length=500)


class AdRequestDropBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


def _ads_queue(db: Session) -> dict[str, Any]:
    return {"requests": managed_ads.admin_queue(db, "open"),
            "recent": managed_ads.admin_queue(db, "closed", limit=20),
            "promise": managed_ads.PROMISE,
            "emailsTo": managed_ads.ops_recipients()}


@router.get("/ads/requests")
def ad_requests(db: Session = Depends(get_db)) -> dict[str, Any]:
    if managed_ads.complete_finished(db):
        db.commit()
    return _ads_queue(db)


@router.post("/ads/requests/{request_id}/done")
def ad_request_done(
    request_id: int,
    body: AdRequestDoneBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    me = db.get(User, user_id)
    try:
        managed_ads.confirm(db, request_id, by=me.email, external_campaign_id=body.external_campaign_id,
                            note=body.note)
    except managed_ads.ManagedAdsError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    db.commit()
    return _ads_queue(db)


@router.post("/ads/requests/{request_id}/drop")
def ad_request_drop(
    request_id: int,
    body: AdRequestDropBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    me = db.get(User, user_id)
    try:
        managed_ads.drop(db, request_id, by=me.email, note=body.note)
    except managed_ads.ManagedAdsError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    db.commit()
    return _ads_queue(db)


class AdImportBody(BaseModel):
    platform: str = Field(max_length=16)
    filename: str = Field(default="export.csv", max_length=200)
    content_b64: str = Field(max_length=5_000_000)
    commit: bool = False


@router.post("/businesses/{business_id}/ads/import")
def ad_import(
    business_id: int,
    body: AdImportBody,
    user_id: int = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Phase 4b: preview (commit=false) or apply a platform's results export."""
    import base64
    import binascii

    from .. import ad_imports

    biz = _get_business(db, business_id)
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The file didn't upload cleanly. Try choosing it again.")
    me = db.get(User, user_id)
    # Scope every query in the import to the client's business (the admin's
    # own session points at whichever business they last opened).
    token = current_tenant_id.set(business_id)
    try:
        summary = ad_imports.run_import(db, biz, body.platform, data, filename=body.filename,
                                        by=me.email, commit=body.commit)
    except ad_imports.ImportProblem as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        current_tenant_id.reset(token)
    if summary["committed"]:
        db.commit()
    else:
        db.rollback()
    return summary


@router.get("/businesses/{business_id}/ads/imports")
def ad_import_history(business_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from sqlalchemy import select

    from ..models import AdImport

    _get_business(db, business_id)
    rows = db.execute(
        select(AdImport).where(AdImport.business_id == business_id)
        .order_by(AdImport.imported_at.desc()).limit(20)
        .execution_options(include_all_tenants=True)
    ).scalars()
    return {"imports": [{
        "id": r.id, "platform": r.platform, "filename": r.filename, "by": r.imported_by,
        "at": r.imported_at.isoformat(), "dateFrom": r.date_from, "dateTo": r.date_to,
        "rows": r.rows, "totalSpendCents": r.total_spend_cents, "unmatched": len(r.unmatched_json or []),
    } for r in rows]}


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
