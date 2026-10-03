"""Phase 5b — approved posts publish themselves (Tier 2 and up).

Decision 4 (Trevor, 2026-10-03): use a posting service (Ayrshare) rather than
building Amplafai's own platform apps. Decision (same day): automatic posting
comes with Tier 2 (Marketing Agent) and up; Tier 1 keeps copy and paste.

Flow:
  1. The owner links Facebook, Instagram and Google Business Profile once
     (Settings → Connections → "Link accounts", Ayrshare's hosted page).
  2. Approving a post queues it for its planned day (POST_HOUR_UTC), or for
     right away if that day is today.
  3. The notification loop (every 15 minutes, production) publishes what's
     due. Each post records what happened per platform: posted with a link,
     or why not (with "Try again" for failures).

Never posts:
  * the website ("web") — there's no posting API; it stays copy and paste;
  * Instagram without a photo — Instagram only takes posts with an image;
  * for a business below Tier 2, the demo, or a platform the owner hasn't
    linked. Those are marked "manual" with the reason, so nothing silently
    disappears.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import posting_service as ps
from .models import Business, Post
from .provisioning import is_demo

log = logging.getLogger("popular_network.auto_posting")

MIN_TIER = 2
POST_HOUR_UTC = 15          # ≈ 10am Central / 9am Mountain on the planned day
RATE_LIMIT_RETRY = timedelta(minutes=30)
PLAN_MESSAGE = ("Automatic posting comes with the Marketing Agent plan (Tier 2) and up. "
                "On your plan, copy each approved post to your accounts.")
# Dashboard channel → posting-service platforms.
TARGETS = {"fb": ["facebook"], "ig": ["instagram"], "gbp": ["gmb"], "meta": ["facebook", "instagram"],
           "google": ["gmb"], "web": []}


def plan_allows(biz: Optional[Business]) -> bool:
    return biz is not None and not is_demo(biz) and (biz.tier or 0) >= MIN_TIER


def available(biz: Optional[Business]) -> bool:
    """Switched on for the app AND included in this business's plan."""
    return ps.is_configured() and plan_allows(biz)


def _client() -> ps.AyrshareClient:
    return ps.AyrshareClient()


# --------------------------------------------------------------------------- #
# Linking accounts
# --------------------------------------------------------------------------- #

def ensure_profile(db: Session, biz: Business, client: ps.AyrshareClient) -> str:
    """The business's posting-service profile key, created on first use.
    Commits right away: the service shows the key only once."""
    if biz.posting_profile_key:
        return biz.posting_profile_key
    prof = client.create_profile(f"{biz.name} (Amplafai #{biz.id})")
    biz.posting_profile_key, biz.posting_profile_ref = prof["profileKey"], prof.get("refId") or None
    db.commit()
    return biz.posting_profile_key


def link_url(db: Session, biz: Business, *, redirect: Optional[str]) -> dict[str, Any]:
    client = _client()
    try:
        key = ensure_profile(db, biz, client)
        return client.link_session(key, redirect=redirect)
    finally:
        client.close()


def linked_accounts(biz: Business) -> list[dict[str, Any]]:
    if not biz.posting_profile_key:
        return []
    client = _client()
    try:
        return client.linked_accounts(biz.posting_profile_key)
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Queueing and publishing
# --------------------------------------------------------------------------- #

def _publish_time(post: Post, now: datetime) -> datetime:
    try:
        day = datetime.strptime(post.date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return now
    at = day.replace(hour=POST_HOUR_UTC)
    return at if at > now else now


def queue(db: Session, biz: Business, post: Post, *, now: Optional[datetime] = None) -> None:
    """Called when a post is approved. Never commits; never calls the service."""
    now = now or datetime.utcnow()
    if not available(biz):
        return
    if not TARGETS.get(post.platform):
        post.publish_state = "manual"
        post.publish_result_json = {"reason": "The website has no posting connection; copy it there by hand."}
        return
    post.publish_state, post.publish_at = "queued", _publish_time(post, now)
    post.publish_result_json = None


def publish(db: Session, biz: Business, post: Post, *, client: Optional[ps.AyrshareClient] = None,
            now: Optional[datetime] = None) -> dict[str, Any]:
    """Publish one post now and record the result on it. Never commits."""
    now = now or datetime.utcnow()
    if not available(biz):
        post.publish_state = "manual"
        post.publish_result_json = {"reason": PLAN_MESSAGE if ps.is_configured() else
                                    "Automatic posting isn't switched on yet; copy it by hand."}
        return post.publish_result_json
    # A retry only goes to platforms that haven't posted yet, so "Try again"
    # after a partial post never posts the same thing twice.
    earlier = list((post.publish_result_json or {}).get("posts") or [])
    done = {x.get("platform") for x in earlier}
    targets = [t for t in TARGETS.get(post.platform) or [] if t not in done]
    skipped: list[dict[str, Any]] = []
    image = post.image_url if (post.image_url or "").startswith("https://") else None
    if "instagram" in targets and not image:
        targets.remove("instagram")
        skipped.append({"platform": "instagram", "retry": False,
                        "message": "Instagram only takes posts with a photo; post it there by hand."})
    own = client is None
    client = client or _client()
    try:
        if not biz.posting_profile_key:
            linked = []
        else:
            linked = [a["platform"] for a in client.linked_accounts(biz.posting_profile_key)]
        for t in [t for t in targets if t not in linked]:
            targets.remove(t)
            skipped.append({"platform": t, "message": f"{ps.LABELS.get(t, t)} isn't linked. Link it in Settings → Connections, then Try again."})
        if not targets:
            post.publish_state = "partial" if earlier else "manual"
            post.publish_result_json = {"posts": earlier, "errors": skipped, "at": now.isoformat()}
            return post.publish_result_json
        text = post.draft if post.draft else post.title
        res = client.publish(biz.posting_profile_key, text=text, platforms=targets,
                             media_urls=[image] if image else None)
    except ps.PostingError as e:
        if e.code == 429:
            post.publish_at = now + RATE_LIMIT_RETRY       # stays queued; never hammer the service
            post.publish_result_json = {"note": str(e), "at": now.isoformat()}
            return post.publish_result_json
        post.publish_state = "partial" if earlier else "failed"
        post.publish_result_json = {"posts": earlier, "errors": [{"platform": None, "message": str(e)}] + skipped,
                                    "at": now.isoformat()}
        return post.publish_result_json
    finally:
        if own:
            client.close()
    errors = res["errors"] + skipped
    posts = earlier + res["posts"]
    post.publish_result_json = {"posts": posts, "errors": errors, "id": res.get("id"), "at": now.isoformat()}
    if posts:
        post.publish_state = "posted" if not errors else "partial"
        if post.status != "published":
            post.status, post.published_at = "published", now
    else:
        post.publish_state = "failed"
    return post.publish_result_json


def publish_due(db: Session, *, now: Optional[datetime] = None, limit: int = 50) -> int:
    """Publish every queued post whose time has come (all businesses).
    Commits after each post so one failure never loses another's result."""
    from .db import current_tenant_id

    now = now or datetime.utcnow()
    if not ps.is_configured():
        return 0
    due = db.execute(select(Post).where(Post.publish_state == "queued", Post.publish_at <= now)
                     .order_by(Post.publish_at).limit(limit)
                     .execution_options(include_all_tenants=True)).scalars().all()
    if not due:
        return 0
    client = _client()
    done = 0
    try:
        for post in due:
            biz = db.execute(select(Business).where(Business.id == post.business_id)
                             .execution_options(include_all_tenants=True)).scalar_one_or_none()
            if biz is None:
                continue
            token = current_tenant_id.set(biz.id)
            try:
                publish(db, biz, post, client=client, now=now)
                db.commit()
                done += 1
            except Exception:
                db.rollback()
                log.exception("Publishing post %s failed unexpectedly", post.id)
            finally:
                current_tenant_id.reset(token)
    finally:
        client.close()
    return done


def payload(post: Post) -> Optional[dict[str, Any]]:
    """What the dashboard shows about automatic posting for one post."""
    if not post.publish_state:
        return None
    r = post.publish_result_json or {}
    return {
        "state": post.publish_state,
        "at": post.publish_at.isoformat() if post.publish_at else None,
        "posts": r.get("posts") or [],
        "errors": r.get("errors") or [],
        "reason": r.get("reason"),
        "note": r.get("note"),
    }
