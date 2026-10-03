"""Phase 4 — managed ad budgets.

Until each ad platform's API is approved (Phase 5), Amplafai runs a real
client's paid campaigns by hand, inside the client's own ad account. This
module is the one place that knows the difference between:

  * the demo account: campaigns are simulated, so every change applies here
    at once (mock platform IDs, "Simulate today" spend); and
  * a managed client: nothing here can reach Meta or Google, so launching,
    pausing, restarting and cancelling something that runs on the platform
    become REQUESTS. Amplafai does the work in Ads Manager, then marks the
    request done in the admin console.

Rules:
  1. A managed campaign is on the platform only once Amplafai enters its
     real campaign ID. Until then the owner sees "Waiting for launch".
  2. Changes to something on the platform show as "Pause requested" (etc.)
     until Amplafai confirms. The dashboard never claims a change the
     platform hasn't made.
  3. Before launch, pause and cancel apply here at once (nothing to stop).
  4. Managed campaigns come with Tier 3 (Marketing Agent + Concierge) and up.
  5. Each new request emails Amplafai (ADS_OPS_EMAIL, else ALERT_EMAIL) once
     the change is committed; the admin console queue is the source of truth.
"""
from __future__ import annotations

import html
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import AdCampaign, AdOpsRequest, Business, Post
from .provisioning import is_demo

log = logging.getLogger("popular_network.managed_ads")

MIN_TIER = 3
# What Amplafai promises owners about hand-run changes. Shown in the
# dashboard and in Amplafai's request emails; change it here only.
PROMISE = "within one business day"
PLAN_MESSAGE = (
    "Paid ad campaigns that Amplafai runs for you come with the Marketing Agent + Concierge "
    "plan (Tier 3) and up. Upgrade in Plan & billing to start one."
)
KINDS = ("launch", "pause", "resume", "cancel")
PLATFORM_LABELS = {
    "fb_ig": "Meta (Facebook + Instagram)",
    "google_ads": "Google Ads",
    "tiktok": "TikTok",
    "linkedin": "LinkedIn",
}
ADS_MANAGER = {
    "fb_ig": "Meta Ads Manager",
    "google_ads": "Google Ads",
    "tiktok": "TikTok Ads Manager",
    "linkedin": "LinkedIn Campaign Manager",
}
SOURCE_LABELS = {"owner": "the owner", "agent": "the AI agent", "cap": "the monthly cap"}


class ManagedAdsError(Exception):
    """A change that isn't allowed right now, with a message fit to show."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #

def is_managed(biz: Optional[Business]) -> bool:
    """A real client: Amplafai runs its campaigns by hand."""
    return biz is not None and not is_demo(biz)


def plan_allows(biz: Optional[Business]) -> bool:
    return is_demo(biz) or (biz is not None and (biz.tier or 0) >= MIN_TIER)


def on_platform(c: AdCampaign) -> bool:
    """The campaign exists on the ad platform (a real ID, not a demo mock)."""
    ext = c.external_campaign_id or ""
    return bool(ext) and not ext.startswith("mock_")


def _now() -> datetime:
    return datetime.utcnow()


def _get(db: Session, model: Any, row_id: int) -> Any:
    """Load by id across tenants (admin paths act on every business)."""
    return db.execute(
        select(model).where(model.id == row_id).execution_options(include_all_tenants=True)
    ).scalar_one_or_none()


def open_requests(db: Session, business_id: int) -> dict[int, AdOpsRequest]:
    """The open request per campaign (there is at most one)."""
    rows = (
        db.query(AdOpsRequest)
        .filter(AdOpsRequest.business_id == business_id, AdOpsRequest.status == "open")
        .order_by(AdOpsRequest.requested_at)
        .all()
    )
    return {r.campaign_id: r for r in rows}


def _open_for(db: Session, c: AdCampaign) -> list[AdOpsRequest]:
    return (
        db.query(AdOpsRequest)
        .filter(AdOpsRequest.campaign_id == c.id, AdOpsRequest.status == "open")
        .execution_options(include_all_tenants=True)
        .all()
    )


def stage(c: AdCampaign, req: Optional[AdOpsRequest], managed: bool) -> str:
    """One word for what the owner should see on a campaign row."""
    if c.status == "pending_approval":
        return "needs_approval"
    if req is not None:
        return {"launch": "waiting_for_launch", "pause": "pause_requested",
                "resume": "resume_requested", "cancel": "cancel_requested"}[req.kind]
    if c.status == "scheduled" and managed and not on_platform(c):
        return "waiting_for_launch"
    if c.status == "paused" and managed and not on_platform(c):
        return "held"
    return {"active": "live"}.get(c.status, c.status)


def ends_at(c: AdCampaign) -> Optional[datetime]:
    return c.launched_at + timedelta(days=c.duration_days) if c.launched_at else None


def add_business_days(start: datetime, days: int) -> datetime:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #

def _close(db: Session, req: AdOpsRequest, status: str, *, by: str, note: str) -> None:
    req.status = status
    req.done_at = _now()
    req.done_by = by
    req.done_note = note


def _open(db: Session, biz: Business, c: AdCampaign, kind: str, *, by: str, source: str,
          note: Optional[str] = None) -> AdOpsRequest:
    """Open `kind` for the campaign, replacing any other open request."""
    for other in _open_for(db, c):
        if other.kind == kind:
            return other
        _close(db, other, "dropped", by=by, note=f"Replaced by a {kind} request.")
    req = AdOpsRequest(business_id=c.business_id, campaign_id=c.id, kind=kind, source=source,
                       requested_by=by, note=note, requested_at=_now())
    db.add(req)
    db.flush()
    _queue_email(db, "new", req, c, biz)
    return req


def request_launch(db: Session, biz: Business, c: AdCampaign, *, by: str, source: str) -> Optional[AdOpsRequest]:
    """A managed campaign was just approved: ask Amplafai to launch it."""
    if not is_managed(biz) or on_platform(c) or c.status != "scheduled":
        return None
    return _open(db, biz, c, "launch", by=by, source=source)


def _withdraw(db: Session, biz: Business, c: AdCampaign, req: AdOpsRequest, *, by: str) -> None:
    _close(db, req, "dropped", by=by, note="Withdrawn before Amplafai got to it.")
    _queue_email(db, "withdrawn", req, c, biz)


def change(db: Session, biz: Business, c: AdCampaign, action: str, *, by: str,
           source: str = "owner") -> dict[str, Any]:
    """Pause, restart or cancel a campaign. Never commits.

    Demo: applies at once. Managed and not launched yet: applies here (there's
    nothing on the platform). Managed and on the platform: opens a request.
    Returns {"outcome": applied|requested|withdrawn|unchanged, "message"}.
    """
    if action not in ("pause", "resume", "cancel"):
        raise ManagedAdsError(f"Unknown action {action}.", 400)
    label = PLATFORM_LABELS.get(c.platform, c.platform)
    manager = ADS_MANAGER.get(c.platform, label)

    if not is_managed(biz):
        if action == "pause":
            if c.status not in ("scheduled", "active"):
                raise ManagedAdsError(f"This campaign is {c.status}, so it can't be paused.")
            c.status = "paused"
        elif action == "resume":
            if c.status != "paused":
                raise ManagedAdsError(f"This campaign is {c.status}, so there's nothing to restart.")
            c.status = "active"
        else:
            if c.status in ("cancelled", "completed"):
                raise ManagedAdsError(f"This campaign is already {c.status}.")
            c.status, c.ended_at = "cancelled", _now()
        return {"outcome": "applied", "message": "Done."}

    pending = _open_for(db, c)
    current = pending[0] if pending else None

    if not on_platform(c):
        # Nothing runs on the platform yet, so the change is real right here.
        if action == "pause":
            if c.status != "scheduled":
                raise ManagedAdsError(f"This campaign is {c.status.replace('_', ' ')}, so it can't be paused.")
            c.status = "paused"
            if current is not None:
                _withdraw(db, biz, c, current, by=by)
            return {"outcome": "applied",
                    "message": "On hold. Amplafai won't launch it until you restart it."}
        if action == "resume":
            if c.status != "paused":
                raise ManagedAdsError(f"This campaign is {c.status.replace('_', ' ')}, so there's nothing to restart.")
            if not plan_allows(biz):
                raise ManagedAdsError(PLAN_MESSAGE, 403)
            c.status = "scheduled"
            request_launch(db, biz, c, by=by, source=source)
            return {"outcome": "requested",
                    "message": f"Sent to Amplafai to launch on {label}, {PROMISE}."}
        if c.status in ("cancelled", "completed"):
            raise ManagedAdsError(f"This campaign is already {c.status}.")
        c.status, c.ended_at = "cancelled", _now()
        if current is not None:
            _withdraw(db, biz, c, current, by=by)
        return {"outcome": "applied", "message": "Cancelled. It was never launched, so nothing was spent."}

    if c.status in ("cancelled", "completed"):
        raise ManagedAdsError(f"This campaign is already {c.status}.")

    if action == "pause":
        if current is not None and current.kind == "resume":
            _withdraw(db, biz, c, current, by=by)
            return {"outcome": "withdrawn", "message": "Restart request withdrawn. It stays paused."}
        if current is not None and current.kind in ("pause", "cancel"):
            return {"outcome": "unchanged", "message": f"Already asked: {current.kind} requested."}
        if c.status != "active":
            raise ManagedAdsError(f"This campaign is {c.status}, so it can't be paused.")
        _open(db, biz, c, "pause", by=by, source=source)
        return {"outcome": "requested",
                "message": f"Pause requested. Amplafai will pause it in {manager} {PROMISE}. "
                           "Until then it keeps running inside its budget and end date."}

    if action == "resume":
        if current is not None and current.kind == "pause":
            _withdraw(db, biz, c, current, by=by)
            return {"outcome": "withdrawn", "message": "Pause request withdrawn. It keeps running."}
        if current is not None and current.kind in ("resume", "cancel"):
            return {"outcome": "unchanged", "message": f"Already asked: {current.kind} requested."}
        if c.status != "paused":
            raise ManagedAdsError(f"This campaign is {c.status}, so there's nothing to restart.")
        if not plan_allows(biz):
            raise ManagedAdsError(PLAN_MESSAGE, 403)
        _open(db, biz, c, "resume", by=by, source=source)
        return {"outcome": "requested",
                "message": f"Restart requested. Amplafai will switch it back on in {manager} {PROMISE}."}

    if current is not None and current.kind == "cancel":
        return {"outcome": "unchanged", "message": "Already asked: cancel requested."}
    _open(db, biz, c, "cancel", by=by, source=source)
    return {"outcome": "requested",
            "message": f"Cancel requested. Amplafai will stop it in {manager} {PROMISE}."}


def confirm(db: Session, req_id: int, *, by: str, external_campaign_id: Optional[str] = None,
            note: Optional[str] = None) -> AdOpsRequest:
    """Amplafai did the work in Ads Manager. Never commits."""
    req = _get(db, AdOpsRequest, req_id)
    if req is None:
        raise ManagedAdsError("Request not found.", 404)
    if req.status != "open":
        raise ManagedAdsError(f"This request was already {req.status}.")
    c = _get(db, AdCampaign, req.campaign_id)
    note = (note or "").strip() or None
    if req.kind == "launch":
        ext = (external_campaign_id or "").strip()
        if not (3 <= len(ext) <= 80) or any(ch.isspace() for ch in ext) or ext.startswith("mock_"):
            raise ManagedAdsError("Enter the campaign ID exactly as the ad platform shows it (no spaces).", 422)
        dup = db.execute(
            select(AdCampaign.id).where(
                AdCampaign.business_id == c.business_id, AdCampaign.platform == c.platform,
                AdCampaign.external_campaign_id == ext, AdCampaign.id != c.id,
            ).execution_options(include_all_tenants=True)
        ).first()
        if dup:
            raise ManagedAdsError(f"Campaign #{dup[0]} already has that ID. Check you copied the right campaign.")
        c.external_campaign_id = ext
        c.status = "active"
        c.launched_at = _now()
        c.scheduled_for = c.scheduled_for or c.launched_at
    elif req.kind == "pause":
        c.status = "paused"
    elif req.kind == "resume":
        c.status = "active"
    else:
        c.status, c.ended_at = "cancelled", _now()
    c.ops_note = note
    _close(db, req, "done", by=by, note=note or "")
    return req


def drop(db: Session, req_id: int, *, by: str, note: str) -> AdOpsRequest:
    """Amplafai couldn't do it. A launch that can't happen cancels the
    campaign (nothing was spent); the note is shown to the owner."""
    note = (note or "").strip()
    if not note:
        raise ManagedAdsError("Say why, so the owner knows what happened.", 422)
    req = _get(db, AdOpsRequest, req_id)
    if req is None:
        raise ManagedAdsError("Request not found.", 404)
    if req.status != "open":
        raise ManagedAdsError(f"This request was already {req.status}.")
    c = _get(db, AdCampaign, req.campaign_id)
    if req.kind == "launch":
        c.status, c.ended_at = "cancelled", _now()
        c.ops_note = f"Amplafai couldn't launch this campaign: {note}"
    else:
        c.ops_note = note
    _close(db, req, "dropped", by=by, note=note)
    return req


def complete_finished(db: Session, business_id: Optional[int] = None, now: Optional[datetime] = None) -> int:
    """Managed campaigns past their end date are over on the platform too."""
    now = now or _now()
    q = select(AdCampaign).where(
        AdCampaign.status.in_(("active", "paused")), AdCampaign.launched_at.is_not(None),
    ).execution_options(include_all_tenants=True)
    if business_id is not None:
        q = q.where(AdCampaign.business_id == business_id)
    done = 0
    for c in db.execute(q).scalars():
        end = ends_at(c)
        if end is not None and end <= now:
            c.status, c.ended_at = "completed", end
            for r in _open_for(db, c):
                _close(db, r, "dropped", by="system", note="The campaign reached its end date.")
            done += 1
    return done


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #

def request_brief(req: Optional[AdOpsRequest]) -> Optional[dict[str, Any]]:
    if req is None:
        return None
    return {"id": req.id, "kind": req.kind, "source": req.source,
            "requestedAt": req.requested_at.isoformat() if req.requested_at else None}


def admin_queue(db: Session, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    q = select(AdOpsRequest).execution_options(include_all_tenants=True)
    if status == "open":
        q = q.where(AdOpsRequest.status == "open").order_by(AdOpsRequest.requested_at)
    else:
        q = q.where(AdOpsRequest.status != "open").order_by(AdOpsRequest.done_at.desc()).limit(limit)
    reqs = list(db.execute(q).scalars())
    now = _now()
    out = []
    for r in reqs:
        c = _get(db, AdCampaign, r.campaign_id)
        biz = _get(db, Business, r.business_id)
        if c is None or biz is None:
            continue
        post = _get(db, Post, c.post_id) if c.post_id else None
        due = add_business_days(r.requested_at, 1)
        out.append({
            "id": r.id, "kind": r.kind, "status": r.status, "source": r.source,
            "sourceLabel": SOURCE_LABELS.get(r.source, r.source),
            "requestedBy": r.requested_by, "requestedAt": r.requested_at.isoformat(),
            "dueAt": due.isoformat(), "overdue": r.status == "open" and now > due,
            "note": r.note, "doneAt": r.done_at.isoformat() if r.done_at else None,
            "doneBy": r.done_by, "doneNote": r.done_note,
            "business": {"id": biz.id, "name": biz.name, "owner": biz.owner, "location": biz.location},
            "campaign": {
                "id": c.id, "name": c.name, "platform": c.platform,
                "platformLabel": PLATFORM_LABELS.get(c.platform, c.platform),
                "adsManager": ADS_MANAGER.get(c.platform, c.platform),
                "dailyBudgetCents": c.daily_budget_cents, "durationDays": c.duration_days,
                "plannedTotalCents": c.planned_total_cents,
                "audience": (c.target_audience_json or {}).get("hint"),
                "postTitle": post.title if post else None,
                "externalCampaignId": c.external_campaign_id, "status": c.status,
                "launchedAt": c.launched_at.isoformat() if c.launched_at else None,
                "endsAt": ends_at(c).isoformat() if ends_at(c) else None,
            },
        })
    return out


# --------------------------------------------------------------------------- #
# Emails to Amplafai — sent only after the change commits
# --------------------------------------------------------------------------- #

_OUTBOX = "managed_ads_outbox"


def ops_recipients() -> list[str]:
    raw = os.getenv("ADS_OPS_EMAIL") or os.getenv("ALERT_EMAIL") or ""
    return [a.strip() for a in raw.split(",") if a.strip()]


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}".replace(".00", "")


def _queue_email(db: Session, what: str, req: AdOpsRequest, c: AdCampaign, biz: Business) -> None:
    db.info.setdefault(_OUTBOX, []).append({
        "what": what, "kind": req.kind, "source": req.source, "by": req.requested_by,
        "business": biz.name, "owner": biz.owner, "location": biz.location,
        "campaign": c.name, "campaignId": c.id, "platform": c.platform,
        "external": c.external_campaign_id, "daily": c.daily_budget_cents, "days": c.duration_days,
        "total": c.planned_total_cents, "audience": (c.target_audience_json or {}).get("hint"),
        "note": req.note,
    })


def build_email(item: dict[str, Any], base: str) -> tuple[str, str]:
    """(subject, text) for one outbox item."""
    label = PLATFORM_LABELS.get(item["platform"], item["platform"])
    manager = ADS_MANAGER.get(item["platform"], label)
    verb = {"launch": "Launch", "pause": "Pause", "resume": "Restart", "cancel": "Cancel"}[item["kind"]]
    money = f"{_money(item['daily'])}/day × {item['days']} days on {label}"
    admin = f"{base}/admin#ads"
    if item["what"] == "withdrawn":
        subject = f"[Amplafai ads] Withdrawn: {verb.lower()} for {item['business']} · {item['campaign']}"
        text = (f"{item['business']} withdrew the request to {verb.lower()} \"{item['campaign']}\" "
                f"({money}). Nothing to do in {manager}.\n\nQueue: {admin}\n")
        return subject, text
    who = f"{item['by'] or 'someone'} ({SOURCE_LABELS.get(item['source'], item['source'])})"
    lines = [f"{item['business']} ({item['owner']}, {item['location']}) needs this done {PROMISE}.", "",
             f"Campaign: {item['campaign']} (#{item['campaignId']})", f"Budget: {money} ({_money(item['total'])} total)"]
    if item["kind"] == "launch":
        lines += [
            f"Audience: {item['audience'] or 'not given; ask the owner or use their usual area'}",
            "",
            f"In {manager}, inside the client's own ad account: set a LIFETIME budget of "
            f"{_money(item['total'])} and an end date {item['days']} days after launch.",
            "When it's live, enter the platform's campaign ID in the admin console.",
        ]
    else:
        lines += [f"Platform campaign ID: {item['external']}", "",
                  f"Do it in {manager}, then mark it done in the admin console."]
    if item.get("note"):
        lines += ["", f"Note: {item['note']}"]
    lines += ["", f"Requested by {who}.", f"Queue: {admin}"]
    return f"[Amplafai ads] {verb} needed: {item['business']} · {money}", "\n".join(lines) + "\n"


def _deliver(items: list[dict[str, Any]]) -> None:
    from .email import _send
    from .notifications import base_url

    to = ops_recipients()
    if not to:
        log.warning("Ad request emails skipped: set ADS_OPS_EMAIL or ALERT_EMAIL (%d waiting in the queue)", len(items))
        return
    base = base_url()
    for item in items:
        subject, text = build_email(item, base)
        body = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.strip().split("\n\n"))
        for addr in to:
            _send(addr, subject, body, text, kind="ads-ops")


def _dispatch(items: list[dict[str, Any]]) -> None:
    threading.Thread(target=_deliver, args=(items,), name="ads-ops-email", daemon=True).start()


# Smokes swap this to capture emails synchronously.
dispatch: Callable[[list[dict[str, Any]]], None] = _dispatch


@event.listens_for(SessionLocal, "after_commit")
def _after_commit(session: Session) -> None:
    items = session.info.pop(_OUTBOX, None)
    if items:
        try:
            dispatch(items)
        except Exception:  # an email problem must never break the request
            log.exception("Couldn't send ad request emails")


@event.listens_for(SessionLocal, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_OUTBOX, None)
