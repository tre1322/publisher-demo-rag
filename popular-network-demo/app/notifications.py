"""Reminder and weekly summary emails (Phase 2).

Two emails, both switched by the business's notification settings and sent
to every active owner and editor (the people who can approve posts):

  Posts waiting   "post_scheduled". When drafts sit in Approvals: at most
                  one a day, and only when something new arrived since the
                  last one (or three days have passed). A draft gets an hour
                  before it counts, so drafting while the owner is in the
                  dashboard doesn't email them about it.
  Weekly summary  "weekly_digest". Mondays from 13:00 UTC (8am Central in
                  summer, 7am in winter), once per week, caught up on Tuesday
                  if the server was down. Approved last week, coming up,
                  waiting, and one new post the agent drafts for the week.

Demo accounts and businesses scheduled for deletion get nothing. A loop in
main.py calls run_due() every 15 minutes in production. Every link opens
the right screen of the right business (/?tab=...&b=...).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from . import email as mailer
from .models import Approval, Business, BusinessUser, MarketingPlan, Post, SettingsRow, User
from .voice_brief import load_voice_brief

log = logging.getLogger("popular_network.notifications")

REMINDER_GRACE = timedelta(hours=1)
REMINDER_GAP = timedelta(hours=24)
REMINDER_REPEAT = timedelta(days=3)
WEEKLY_HOUR_UTC = 13
WEEKLY_DAYS = (0, 1)  # Monday, with Tuesday as catch-up
MAX_PENDING_FOR_SUGGESTION = 3


def base_url() -> str:
    return (os.getenv("APP_BASE_URL") or "https://dashboard.amplafai.com").rstrip("/")


def link(business_id: int, tab: str) -> str:
    return f"{base_url()}/?tab={tab}&b={business_id}"


def recipients(db: Session, business_id: int) -> list[str]:
    rows = (
        db.query(User.email)
        .join(BusinessUser, BusinessUser.user_id == User.id)
        .filter(BusinessUser.business_id == business_id, BusinessUser.role.in_(("owner", "editor")),
                User.is_active.is_(True))
        .execution_options(include_all_tenants=True)
        .all()
    )
    return sorted({r.email for r in rows if r.email})


def pref_on(settings: Optional[SettingsRow], key: str) -> bool:
    for n in (settings.notifications_json if settings else None) or []:
        if n.get("key") == key:
            return bool(n.get("on")) and not n.get("muted")
    return False


def _state(settings: SettingsRow) -> dict[str, Any]:
    return dict(settings.notify_state_json or {})


def _save_state(settings: SettingsRow, state: dict[str, Any]) -> None:
    settings.notify_state_json = dict(state)  # reassign: JSON change detection


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _day_label(iso: Optional[str]) -> Optional[str]:
    try:
        d = date.fromisoformat(iso or "")
    except ValueError:
        return None
    return f"{d.strftime('%a, %b')} {d.day}"


def _pending_posts(db: Session, business_id: int) -> list[Approval]:
    return (
        db.query(Approval)
        .filter(Approval.business_id == business_id, Approval.decision.is_(None), Approval.kind == "post")
        .execution_options(include_all_tenants=True)
        .order_by(Approval.created_at.asc())
        .all()
    )


def _eligible(biz: Business) -> bool:
    return not biz.is_demo and biz.deletion_due_at is None


# --------------------------------------------------------------------------- #
# Posts waiting
# --------------------------------------------------------------------------- #

def reminder_due(pending: list[Approval], state: dict[str, Any], now: datetime) -> bool:
    ripe = [a for a in pending if a.created_at and now - a.created_at >= REMINDER_GRACE]
    if not ripe:
        return False
    last = _parse(state.get("approvalsAt"))
    if last is None:
        return True
    if now - last < REMINDER_GAP:
        return False
    newest = max(a.created_at for a in ripe)
    return newest > last or now - last >= REMINDER_REPEAT


def send_approvals_reminder(db: Session, biz: Business, *, to: Optional[list[str]] = None) -> dict[str, Any]:
    """Send the reminder now (no timing checks). `to` overrides the recipients (admin preview)."""
    pending = _pending_posts(db, biz.id)
    items = [{"title": a.title, "planned": _day_label((a.payload_json or {}).get("plannedDate"))} for a in pending]
    addrs = to if to is not None else recipients(db, biz.id)
    if not items:
        return {"sent": 0, "reason": "nothing_pending"}
    results = [mailer.send_approvals_reminder(a, business_name=biz.name, items=items,
                                              review_url=link(biz.id, "approvals"),
                                              settings_url=link(biz.id, "settings")) for a in addrs]
    return {"sent": sum(1 for r in results if r.get("sent")), "to": addrs, "results": results}


# --------------------------------------------------------------------------- #
# Weekly summary
# --------------------------------------------------------------------------- #

def week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def weekly_due(state: dict[str, Any], now: datetime) -> bool:
    if now.weekday() not in WEEKLY_DAYS or (now.weekday() == 0 and now.hour < WEEKLY_HOUR_UTC):
        return False
    return state.get("weeklyWeek") != week_key(now)


def _channels(db: Session, biz: Business) -> list[str]:
    answers = (biz.onboarding_json or {}).get("answers") or {} if isinstance(biz.onboarding_json, dict) else {}
    chosen = [c for c in answers.get("channels") or [] if c in ("fb", "ig", "gbp", "web")]
    if chosen:
        return chosen
    mp = db.get(MarketingPlan, biz.id)
    planned = [c.get("platform") for c in (mp.channels_json if mp else None) or [] if isinstance(c, dict)]
    return [c for c in planned if c in ("fb", "ig", "gbp", "web")] or ["fb"]


SUGGEST_SYSTEM = """You are the AI marketing agent for a small local business on Amplafai. Write one social post for the coming week. It sounds like the VOICE in the brief, pushes an AMPLIFY item, leaves MUTE items out, and follows every constraint. If a seasonal pattern fits today's date, use it. Don't repeat the recent posts listed. Never invent facts: no prices, discounts, dates, events, numbers, awards or quotes that the brief doesn't contain.

Facebook 40-90 words. Instagram: a caption, then 3-6 hashtags on the last line. Google Business Profile: 60-120 words ending in a call to action. Website: a title line, then 3-5 short paragraphs. title: a short internal title. why: one sentence for the owner on why this post, this week. Plain English."""


def suggest_next_post(db: Session, biz: Business, now: datetime) -> Optional[dict[str, Any]]:
    """Have Claude draft one post for the week and queue it in Approvals.

    Best effort: returns None (and the summary goes out without it) when the
    business has no brief, already has plenty waiting, or the call fails.
    """
    from .onboarding import OnboardingError, _call_structured

    brief = load_voice_brief(biz)
    if not brief or len(_pending_posts(db, biz.id)) >= MAX_PENDING_FOR_SUGGESTION:
        return None
    channels = _channels(db, biz)
    recent = (
        db.query(Post.title).filter(Post.business_id == biz.id)
        .execution_options(include_all_tenants=True).order_by(Post.id.desc()).limit(10).all()
    )
    public_brief = {k: v for k, v in brief.items() if not str(k).startswith("_")}
    user = (
        f"Business: {biz.name}" + (f" in {biz.location}" if biz.location else "") + "\n"
        f"Today: {now.strftime('%A, %B')} {now.day}, {now.year}\n"
        f"Platforms: {', '.join(channels)}\n"
        f"Recent posts: {'; '.join(r.title for r in recent) or 'none yet'}\n\n"
        f"Voice brief:\n{json.dumps(public_brief, indent=2, ensure_ascii=False)}"
    )
    schema = {
        "type": "object",
        "properties": {
            "platform": {"type": "string", "enum": channels},
            "title": {"type": "string"},
            "draft": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["platform", "title", "draft", "why"],
        "additionalProperties": False,
    }
    try:
        out = _call_structured(purpose="weekly_suggestion", system=SUGGEST_SYSTEM, user=user, schema=schema, effort="low")
    except OnboardingError as e:
        log.warning("weekly suggestion skipped for business %s: %s", biz.id, e)
        return None
    if out.get("platform") not in channels or not (out.get("draft") or "").strip():
        return None
    planned = (now.date() + timedelta(days=2)).isoformat()
    why = (out.get("why") or "").strip()
    db.add(Approval(
        business_id=biz.id, kind="post", platform=out["platform"],
        title=(out.get("title") or "This week's post").strip()[:280], draft=out["draft"].strip(),
        note=f"The agent's idea for this week. {why}".strip(),
        payload_json={"source": "weekly_suggestion", "plannedDate": planned},
    ))
    db.flush()
    return {"title": (out.get("title") or "This week's post").strip(), "why": why}


def send_weekly_summary(db: Session, biz: Business, now: datetime, *, to: Optional[list[str]] = None,
                        suggest: bool = True) -> dict[str, Any]:
    """Send the summary now (no timing checks). `to` overrides the recipients (admin preview)."""
    from .onboarding import needs_onboarding

    since = now - timedelta(days=7)
    posts = db.query(Post).filter(Post.business_id == biz.id).execution_options(include_all_tenants=True).all()
    approved = sorted(
        (p for p in posts if p.status in ("approved", "published") and p.decided_at and p.decided_at >= since),
        key=lambda p: p.decided_at,
    )
    today, week_out = now.date().isoformat(), (now.date() + timedelta(days=7)).isoformat()
    upcoming = sorted((p for p in posts if p.status == "approved" and today <= (p.date or "") <= week_out),
                      key=lambda p: p.date)
    suggestion = suggest_next_post(db, biz, now) if suggest else None
    pending = len(_pending_posts(db, biz.id))
    addrs = to if to is not None else recipients(db, biz.id)
    links = {tab: link(biz.id, tab) for tab in ("approvals", "calendar", "chat", "onboarding", "settings")}
    results = [mailer.send_weekly_summary(
        a, business_name=biz.name,
        approved=[{"title": p.title, "when": _day_label(p.date) or p.date} for p in approved],
        upcoming=[{"title": p.title, "when": _day_label(p.date) or p.date} for p in upcoming],
        pending=pending, suggestion=suggestion, setup_unfinished=needs_onboarding(biz), links=links,
    ) for a in addrs]
    return {"sent": sum(1 for r in results if r.get("sent")), "to": addrs, "suggestion": suggestion,
            "results": results}


# --------------------------------------------------------------------------- #
# The loop's entry point
# --------------------------------------------------------------------------- #

def run_due(db: Session, now: Optional[datetime] = None) -> dict[str, list[int]]:
    """Send every reminder and summary that's due. Returns business ids per kind."""
    now = now or datetime.utcnow()
    done: dict[str, list[int]] = {"approvals": [], "weekly": []}
    if not os.getenv("POSTMARK_API_KEY"):
        return done  # nothing can be sent, so don't draft suggestions nobody will hear about
    businesses = db.query(Business).execution_options(include_all_tenants=True).all()
    for biz in businesses:
        if not _eligible(biz):
            continue
        settings = db.get(SettingsRow, biz.id)
        if settings is None or not recipients(db, biz.id):
            continue
        try:
            state = _state(settings)
            if pref_on(settings, "post_scheduled") and reminder_due(_pending_posts(db, biz.id), state, now):
                if send_approvals_reminder(db, biz)["sent"]:
                    state["approvalsAt"] = now.isoformat()
                    done["approvals"].append(biz.id)
                    _save_state(settings, state)
                    db.commit()
            if pref_on(settings, "weekly_digest") and weekly_due(state, now):
                week = week_key(now)
                # Draft the week's suggestion once, even if the email has to
                # be retried on a later tick.
                res = send_weekly_summary(db, biz, now, suggest=state.get("suggestedWeek") != week)
                if res["suggestion"]:
                    state["suggestedWeek"] = week
                if res["sent"]:
                    state["weeklyWeek"] = week
                    done["weekly"].append(biz.id)
                _save_state(settings, state)
                db.commit()
        except Exception:  # one business's failure never stops the others
            db.rollback()
            log.exception("notifications failed for business %s", biz.id)
    if done["approvals"] or done["weekly"]:
        log.info("notifications sent: %s", done)
    return done
