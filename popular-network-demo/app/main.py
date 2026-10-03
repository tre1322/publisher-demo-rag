"""FastAPI app for the Popular Network marketing dashboard.

Layout:
- /api/*          → JSON endpoints (bootstrap + per-tab CRUD)
- everything else → static files (dashboard.html, future assets/)

Run with `python -m app.main` (or `uv run python -m app.main`). The original
`serve.py` is preserved as a thin shim that delegates here.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException

# Phase C: load ANTHROPIC_API_KEY before any router imports the SDK.
# find_dotenv walks up from CWD, so this picks up either popular-network-demo/.env
# or the parent publisher-demo-rag/.env if you only set the key once.
#
# override=True is deliberate: some shells export ANTHROPIC_API_KEY="" (empty
# string) at login, which dotenv's default override=False treats as "already
# set" and refuses to overwrite. The result: load_dotenv returns True but the
# value stays empty. Override=True ensures the .env value wins.
load_dotenv(find_dotenv(usecwd=True), override=True)

# E402 below is deliberate, not an oversight: every import from here down must
# run AFTER load_dotenv so the SDK clients pick up the keys. Silenced per-line
# (matching the convention in app/scripts/smoke_*.py) so that a genuinely
# misplaced import elsewhere in this file still gets flagged.
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

from .auth.middleware import RequireBusinessMiddleware  # noqa: E402
from .db import _add_col_if_missing, init_db  # noqa: E402
from .routers import (  # noqa: E402
    admin,
    ads,
    approvals,
    auth,
    billing,
    bootstrap,
    chat,
    chatbot,
    compose,
    integrations,
    inventory,
    invites,
    marketing_plan,
    onboarding,
    password_reset,
    performance,
    posts,
    reach,
    reviews,
    settings,
    widget,
)
from .seed import seed_if_empty  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("popular_network")

# The interactive API explorer (/docs, /redoc, /openapi.json) is dev-only:
# in production it would publish every endpoint to anyone.
_API_DOCS = os.getenv("ENVIRONMENT", "development").lower() != "production"
app = FastAPI(
    title="Popular Network — Marketing Dashboard",
    version="0.1.0",
    docs_url="/docs" if _API_DOCS else None,
    redoc_url="/redoc" if _API_DOCS else None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Forward-migrations for existing dev DBs that pre-date a column.
    # init_db() above creates all tables fresh; this catches the case where
    # the table exists but a newer column hasn't been added yet.
    _add_col_if_missing("settings", "notifications_json", "JSON")  # B.4
    _add_col_if_missing("businesses", "allowed_origins_json", "JSON")  # H.2.2 widget CORS
    # Phase I.1 — real LinkedIn OAuth lifecycle columns on the existing
    # ad_connections table. NULL for the three mock platforms; populated only
    # by the live LinkedIn 3-legged handshake. Idempotent on persistent DBs.
    _add_col_if_missing("ad_connections", "refresh_token", "TEXT")
    _add_col_if_missing("ad_connections", "token_expires_at", "DATETIME")
    _add_col_if_missing("ad_connections", "refresh_expires_at", "DATETIME")
    _add_col_if_missing("ad_connections", "scope", "TEXT")
    _add_col_if_missing("ad_connections", "oauth_state", "VARCHAR(64)")
    _add_col_if_missing("ad_connections", "account_urn", "VARCHAR(120)")
    _add_col_if_missing("ad_connections", "connected_user_name", "VARCHAR(120)")
    # Trust pass (v49): anchor timestamp for live Day-N rendering. Nullable;
    # _backfill_enrolled_at below fills pre-existing rows.
    _add_col_if_missing("businesses", "enrolled_at", "DATETIME")
    # Phase 0 (money path): owner-authorized spend ceiling, server-side
    # autonomy switch, and machine-readable proposal payloads. All nullable;
    # NULL means "owner hasn't authorized" / "autonomy off" / "legacy row".
    _add_col_if_missing("ad_platform_budgets", "owner_cap_cents", "INTEGER")
    _add_col_if_missing("settings", "ad_autonomy_enabled", "BOOLEAN")
    _add_col_if_missing("approvals", "payload_json", "JSON")
    # Phase 1: demo-vs-real flag, voice brief in the DB, client website.
    _add_col_if_missing("businesses", "is_demo", "BOOLEAN")
    _add_col_if_missing("businesses", "voice_brief_json", "JSON")
    _add_col_if_missing("businesses", "website", "VARCHAR(200)")
    _add_col_if_missing("businesses", "deletion_requested_at", "DATETIME")
    _add_col_if_missing("businesses", "deletion_due_at", "DATETIME")
    # Phase 2: onboarding wizard progress.
    _add_col_if_missing("businesses", "onboarding_json", "JSON")
    _add_col_if_missing("settings", "notify_state_json", "JSON")
    inserted = seed_if_empty()
    if inserted:
        log.info("Seeded Quadd.ai (business_id=1) — Day-1 customer w/ voice brief loaded")
    else:
        log.info("DB already seeded — skipping")
    # Phase 1: Quadd is the demo account (must run before anything that
    # branches on is_demo), and file-based voice briefs move into the DB.
    _backfill_demo_flag()
    _backfill_voice_briefs()
    # One-time backfill: settings rows that pre-date notifications_json have
    # NULL there. Populate with sensible defaults so the Notifications tab
    # renders something. Safe to re-run on every startup (no-op once filled).
    _backfill_notification_defaults()
    # Phase D.1: businesses that pre-date the reach_tiers table need their
    # tier ladder seeded. Idempotent: only inserts when the business has zero
    # rows. Same call as in seed.py's fresh-DB path.
    _backfill_reach_tiers()
    # Phase E.1: businesses that pre-date the ad_platform_budgets +
    # ad_connections tables need their disconnected-state seed. Same
    # idempotent pattern.
    _backfill_ad_platform_setup()
    # Phase F: tier bump (Quadd: 3 → 4) + usage-metric rows for any business
    # that pre-dates the F.2 billing tables. Idempotent.
    _backfill_phase_f()
    # Phase G: add `source` column to chatbot_conversations if missing, then
    # backfill existing rows to source='fixture' so they render with the
    # Demo chip. Idempotent — column is added only when absent; UPDATE only
    # touches NULL rows.
    _backfill_phase_g()
    # Trust pass (v49). Order matters: enrolled_at must exist before the
    # week-recap backfill derives event timestamps from it.
    _backfill_enrolled_at()
    _backfill_week_recap_timestamps()
    _backfill_attention_copy()
    # Phase 1: run any deletion whose 30-day clock has run out, now and then
    # every few hours while the server is up (there's no cron in the image).
    _purge_due_businesses()
    _start_purge_loop()
    # Phase 2: approval reminders and the Monday summary.
    _start_notify_loop()


def _purge_due_businesses() -> None:
    from .data_lifecycle import purge_due
    from .db import SessionLocal

    try:
        with SessionLocal() as db:
            purged = purge_due(db)
        if purged:
            log.info(f"Deleted businesses past their deletion date: {purged}")
    except Exception:  # never take the app down over housekeeping
        log.exception("Scheduled business deletion failed; will retry")


_PURGE_LOOP_STARTED = False


def _start_purge_loop() -> None:
    global _PURGE_LOOP_STARTED
    if _PURGE_LOOP_STARTED or os.getenv("POPULAR_PURGE_LOOP", "1") == "0":
        return
    _PURGE_LOOP_STARTED = True

    import threading
    import time

    def _loop() -> None:
        while True:
            time.sleep(6 * 3600)
            _purge_due_businesses()

    threading.Thread(target=_loop, name="purge-due-businesses", daemon=True).start()


_NOTIFY_LOOP_STARTED = False


def _start_notify_loop() -> None:
    """Every 15 minutes, send the reminder/summary emails that are due.

    On by default only in production, so a dev server with a Postmark key
    in .env can't email real people from a copy of the data.
    POPULAR_NOTIFY_LOOP=1/0 overrides either way.
    """
    global _NOTIFY_LOOP_STARTED
    default = "1" if os.getenv("ENVIRONMENT", "").lower() == "production" else "0"
    if _NOTIFY_LOOP_STARTED or os.getenv("POPULAR_NOTIFY_LOOP", default) == "0":
        return
    _NOTIFY_LOOP_STARTED = True

    import threading
    import time

    from .db import SessionLocal
    from .notifications import run_due

    def _loop() -> None:
        time.sleep(60)  # let startup finish first
        while True:
            try:
                with SessionLocal() as db:
                    run_due(db)
            except Exception:  # housekeeping never takes the app down
                log.exception("Notification run failed; will retry")
            time.sleep(15 * 60)

    threading.Thread(target=_loop, name="notifications", daemon=True).start()
    log.info("Notification loop started (every 15 minutes)")


def _backfill_demo_flag() -> None:
    """Quadd (slug quadd_ai) is the sales-demo account. Every other business
    stays NULL = real. Only fills NULL, so an admin's later choice sticks."""
    from .db import SessionLocal
    from .models import Business

    with SessionLocal() as db:
        biz = db.query(Business).filter(Business.slug == "quadd_ai", Business.is_demo.is_(None)).first()
        if biz is not None:
            biz.is_demo = True
            log.info(f"Marked business_id={biz.id} ({biz.slug}) as the demo account")
        db.commit()


def _backfill_voice_briefs() -> None:
    """Copy a legacy voice-briefs/{slug}.json into the DB once."""
    from .db import SessionLocal
    from .models import Business
    from .voice_brief import brief_from_file

    with SessionLocal() as db:
        for biz in db.query(Business).filter(Business.voice_brief_json.is_(None)).all():
            brief = brief_from_file(biz.slug)
            if brief:
                biz.voice_brief_json = brief
                log.info(f"Moved voice brief for business_id={biz.id} ({biz.slug}) into the DB")
        db.commit()


def _backfill_reach_tiers() -> None:
    """Insert the Phase D reach-tier ladder for any business that doesn't have it."""
    from .db import SessionLocal
    from .models import Business, ReachTier
    from .seed import _seed_reach_tiers

    with SessionLocal() as db:
        for biz in db.query(Business).all():
            has_any = db.query(ReachTier).filter(ReachTier.business_id == biz.id).first()
            if has_any is None:
                _seed_reach_tiers(db, business_id=biz.id, publisher=biz.publisher or None,
                                  location=biz.location or None)
                log.info(f"Backfilled reach tier ladder for business_id={biz.id} ({biz.slug})")
        db.commit()


def _backfill_ad_platform_setup() -> None:
    """Insert Phase E disconnected ad_connections + zero-cap ad_platform_budgets
    rows for any business that doesn't have them. Idempotent — checks per
    business + per current month."""
    from .db import SessionLocal
    from .models import AdConnection, AdPlatformBudget, Business
    from .seed import _current_month_year, _seed_ad_connections, _seed_ad_platform_budgets

    with SessionLocal() as db:
        month = _current_month_year()
        for biz in db.query(Business).all():
            has_conn = db.query(AdConnection).filter(AdConnection.business_id == biz.id).first()
            if has_conn is None:
                _seed_ad_connections(db, business_id=biz.id)
                log.info(f"Backfilled ad connections for business_id={biz.id} ({biz.slug})")
            has_budget = (
                db.query(AdPlatformBudget)
                .filter(
                    AdPlatformBudget.business_id == biz.id,
                    AdPlatformBudget.month_year == month,
                )
                .first()
            )
            if has_budget is None:
                _seed_ad_platform_budgets(db, business_id=biz.id)
                log.info(f"Backfilled ad platform budgets ({month}) for business_id={biz.id}")
        db.commit()


def _backfill_phase_f() -> None:
    """Phase F backfill: ensure Quadd is at tier 4 + usage_metrics rows exist.

    Tier bump: pre-Phase-F DBs have Quadd at tier 3. Phase F demos the
    Tier 4 Inventory surface, so we promote tier 3 → 4 (and price 150 → 799)
    one-time. Pre-existing higher tiers are left alone. Lower tiers (1/2)
    are left alone too — a real Tier 2 customer shouldn't get free-upgraded.

    Usage metrics: each business needs zero-valued rows for the current
    month so the Billing view has something to render.

    Dashboard notices fixup: existing notices rows from before the tier
    bump still say "concierge tier" / "$150/mo concierge tier". Repaint
    those for Tier 4 businesses so Home reads correctly.
    """
    from .db import SessionLocal
    from .models import Business, DashboardNotices, UsageMetric
    from .seed import _seed_billing_usage, _current_month_year

    with SessionLocal() as db:
        month = _current_month_year()
        for biz in db.query(Business).all():
            # Tier bump (Quadd-specific — gated on slug to be paranoid).
            if biz.slug == "quadd_ai" and biz.tier == 3 and biz.monthly_price == 150:
                biz.tier = 4
                biz.tier_label = "Tier 4 — Inventory"
                biz.monthly_price = 799
                log.info(f"Backfilled tier 3 → 4 for business_id={biz.id} ({biz.slug})")
            # Usage rows for this month.
            has_usage = (
                db.query(UsageMetric)
                .filter(
                    UsageMetric.business_id == biz.id,
                    UsageMetric.month_year == month,
                )
                .first()
            )
            if has_usage is None:
                _seed_billing_usage(db, business_id=biz.id, tier=biz.tier)
                log.info(f"Backfilled usage_metrics ({month}) for business_id={biz.id}")

            # Dashboard notices fixup — only for Tier 4 customers whose
            # notices still say "concierge". Tier 3 customers should keep
            # their concierge wording. Demo accounts only: this rewrites the
            # old Quadd seed copy and must never inject it into a real client.
            if biz.tier >= 4 and biz.is_demo:
                notices = db.get(DashboardNotices, biz.id)
                if notices is not None:
                    changed = False
                    # week_recap: replace "concierge tier" → "inventory tier"
                    if notices.week_recap_json:
                        new_recap = []
                        for item in notices.week_recap_json:
                            text = item.get("text", "")
                            if "concierge tier" in text:
                                new_recap.append({**item, "text": text.replace("concierge tier", "inventory tier")})
                                changed = True
                            else:
                                new_recap.append(item)
                        if changed:
                            notices.week_recap_json = new_recap
                    # stats_overrides.spend: $150/mo concierge → $799/mo inventory
                    if notices.stats_overrides_json:
                        sp = notices.stats_overrides_json.get("spend")
                        if sp and "concierge" in (sp.get("helper") or ""):
                            new_overrides = {**notices.stats_overrides_json}
                            new_overrides["spend"] = {
                                **sp,
                                "budget": biz.monthly_price,
                                "helper": f"${biz.monthly_price}/mo inventory tier",
                            }
                            notices.stats_overrides_json = new_overrides
                            changed = True
                    # attention_json: inject the "Connect your first inventory
                    # feed" item if missing. Insert before the existing spend/
                    # review entries so it sits at the top of the second row.
                    if notices.attention_json is not None:
                        has_inv = any(a.get("kind") == "inventory" for a in notices.attention_json)
                        if not has_inv:
                            inv_item = {
                                "kind": "inventory",
                                "title": "Connect your first inventory feed",
                                "detail": "Your plan includes live inventory sync — DealerCenter / vAuto / MLS / TractorHouse. Listings show up in your publisher's chatbot search within an hour.",
                                "cta": "Open Inventory", "icon": "box", "tone": "teal",
                                "target": "inventory",
                            }
                            # Insert at position 2 (after pending + voice brief)
                            # so the highest-priority items stay first.
                            new_attention = list(notices.attention_json)
                            new_attention.insert(2, inv_item)
                            notices.attention_json = new_attention
                            changed = True
                    if changed:
                        log.info(f"Backfilled dashboard_notices tier copy for business_id={biz.id}")
        db.commit()


def _backfill_enrolled_at() -> None:
    """Populate Business.enrolled_at for rows that pre-date the column.

    Source: earliest seeded Approval/Post created_at for the business (the
    closest surviving record of when the row was actually created), else
    utcnow(). Idempotent — rows that already have enrolled_at are skipped.
    """
    from datetime import datetime

    from sqlalchemy import func

    from .db import SessionLocal
    from .models import Approval, Business, Post

    with SessionLocal() as db:
        for biz in db.query(Business).all():
            if biz.enrolled_at is not None:
                continue
            a = db.query(func.min(Approval.created_at)).filter(Approval.business_id == biz.id).scalar()
            p = db.query(func.min(Post.created_at)).filter(Post.business_id == biz.id).scalar()
            cands = [d for d in (a, p) if d is not None]
            biz.enrolled_at = min(cands) if cands else datetime.utcnow()
            log.info(f"Backfilled enrolled_at={biz.enrolled_at} for business_id={biz.id} ({biz.slug})")
        db.commit()


def _backfill_week_recap_timestamps() -> None:
    """Convert legacy literal week-recap `when` strings ("Today, 9:33am") into
    when_iso timestamps derived from Business.enrolled_at, so bootstrap can
    render honest labels ("Jun 2") instead of a frozen "Today".

    Idempotent — items that already carry when_iso are skipped; strings that
    don't match the legacy shape are left verbatim (bootstrap passes them
    through unchanged).
    """
    import re
    from datetime import datetime

    from .db import SessionLocal
    from .models import Business, DashboardNotices

    time_re = re.compile(r"^Today,\s*(\d{1,2}):(\d{2})\s*(am|pm)$", re.IGNORECASE)

    with SessionLocal() as db:
        for biz in db.query(Business).all():
            notices = db.get(DashboardNotices, biz.id)
            if notices is None or not notices.week_recap_json:
                continue
            base = biz.enrolled_at or datetime.utcnow()
            changed, new_items = False, []
            for item in notices.week_recap_json:
                if item.get("when_iso"):
                    new_items.append(item)
                    continue
                when = (item.get("when") or "").strip()
                m = time_re.match(when)
                if m:
                    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
                    iso = base.replace(hour=hour, minute=int(m.group(2)), second=0, microsecond=0).isoformat()
                elif when.lower().startswith("today"):
                    # "Today, ongoing" and friends — anchor to enrollment.
                    iso = base.replace(second=0, microsecond=0).isoformat()
                else:
                    new_items.append(item)
                    continue
                new_items.append({**item, "when_iso": iso})
                changed = True
            if changed:
                # Reassign, never mutate in place — SQLAlchemy JSON change
                # detection only fires on attribute assignment.
                notices.week_recap_json = new_items
                log.info(f"Backfilled week_recap when_iso for business_id={biz.id}")
        db.commit()


def _backfill_attention_copy() -> None:
    """Repaint roadmap jargon in stored attention_json rows ("Tier 4 unlocks…"
    → plan-benefit language). The frontend sweep can't reach DB-stored copy.
    Idempotent — matches on the exact legacy phrase only.
    """
    from .db import SessionLocal
    from .models import Business, DashboardNotices

    _REPAINTS = [
        ("Tier 4 unlocks live inventory sync", "Your plan includes live inventory sync"),
        ("Tier 3 unlocks the consumer chatbot preview", "Your plan includes the consumer chatbot preview"),
        ("Tier 4 unlocks the consumer chatbot preview", "Your plan includes the consumer chatbot preview"),
    ]

    with SessionLocal() as db:
        for biz in db.query(Business).all():
            notices = db.get(DashboardNotices, biz.id)
            if notices is None or not notices.attention_json:
                continue
            changed, new_items = False, []
            for item in notices.attention_json:
                detail = item.get("detail", "")
                new_detail = detail
                for old, new in _REPAINTS:
                    if old in new_detail:
                        new_detail = new_detail.replace(old, new)
                if new_detail != detail:
                    new_items.append({**item, "detail": new_detail})
                    changed = True
                else:
                    new_items.append(item)
            if changed:
                notices.attention_json = new_items  # reassign — JSON change detection
                log.info(f"Repainted attention copy for business_id={biz.id}")
        db.commit()


def _backfill_phase_g() -> None:
    """Phase G: add `source` column to chatbot_conversations if missing, and
    backfill any NULL values to 'fixture'. New rows from the seeder + the
    ingest endpoint set the column explicitly, so this only matters for DBs
    that pre-date Phase G."""
    from sqlalchemy import text as _text

    from .db import SessionLocal, engine

    _add_col_if_missing("chatbot_conversations", "source", "VARCHAR(16)")
    with engine.begin() as conn:
        conn.execute(
            _text("UPDATE chatbot_conversations SET source = 'fixture' WHERE source IS NULL")
        )


def _backfill_notification_defaults() -> None:
    from .db import SessionLocal
    from .models import Business, DashboardNotices, SettingsRow

    defaults = [
        {"key": "neg_review",        "label": "New negative review (2★ or below)", "on": True,  "via": "Email + push"},
        {"key": "post_scheduled",    "label": "Posts approaching scheduled time",  "on": True,  "via": "Email digest"},
        {"key": "ad_pacing",         "label": "Ad spend pacing alerts",            "on": True,  "via": "Email"},
        {"key": "weekly_digest",     "label": "Weekly performance digest",         "on": True,  "via": "Email · Mondays 8am"},
        {"key": "knowledge_gap",     "label": "Knowledge-gap detector findings",   "on": False, "via": "—"},
        {"key": "competitive_intel", "label": "Competitive intel digest (Tier 3)", "on": False, "via": "Tier 3 only", "muted": True},
    ]
    with SessionLocal() as db:
        for row in db.query(SettingsRow).filter(SettingsRow.notifications_json.is_(None)).all():
            row.notifications_json = defaults
            log.info(f"Backfilled notifications_json for business_id={row.business_id}")

        # B.7: pre-existing attention rows lack `targetId` for the scroll-to-item
        # nav. Patch them in place — only mutate items that don't already carry it,
        # so a future edited attention feed isn't clobbered.
        target_id_map = {"approvals": "a1", "reviews": "r3"}
        # Legacy seed ids ("a1", "r3") only exist in the demo seed; never
        # point a real client's attention items at them.
        demo_ids = {b.id for b in db.query(Business).filter(Business.is_demo.is_(True)).all()}
        for notices in db.query(DashboardNotices).all():
            if not notices.attention_json or notices.business_id not in demo_ids:
                continue
            changed = False
            patched = []
            for item in notices.attention_json:
                if "targetId" not in item and item.get("target") in target_id_map:
                    patched.append({**item, "targetId": target_id_map[item["target"]]})
                    changed = True
                else:
                    patched.append(item)
            if changed:
                notices.attention_json = patched
                log.info(f"Backfilled attention.targetId for business_id={notices.business_id}")

        db.commit()


@app.middleware("http")
async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Preserve the no-cache semantics of the original serve.py.

    Browsers and preview panels otherwise hold stale dashboard.html and silently
    confuse Trevor about which version they're seeing.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


app.add_middleware(RequireBusinessMiddleware)

app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(admin.router, prefix="/api", tags=["admin"])
app.include_router(invites.router, prefix="/api", tags=["invites"])
app.include_router(password_reset.router, prefix="/api", tags=["password-reset"])
app.include_router(widget.router, prefix="/api", tags=["widget"])
app.include_router(bootstrap.router, prefix="/api", tags=["bootstrap"])
app.include_router(posts.router, prefix="/api", tags=["posts"])
app.include_router(approvals.router, prefix="/api", tags=["approvals"])
app.include_router(reviews.router, prefix="/api", tags=["reviews"])
app.include_router(performance.router, prefix="/api", tags=["performance"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(marketing_plan.router, prefix="/api", tags=["marketing-plan"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(reach.router, prefix="/api", tags=["reach"])
app.include_router(ads.router, prefix="/api", tags=["ads"])
app.include_router(integrations.router, prefix="/api", tags=["integrations"])
app.include_router(inventory.router, prefix="/api", tags=["inventory"])
app.include_router(billing.router, prefix="/api", tags=["billing"])
app.include_router(chatbot.router, prefix="/api", tags=["chatbot"])
app.include_router(compose.router, prefix="/api", tags=["compose"])
app.include_router(onboarding.router, prefix="/api", tags=["onboarding"])


# Phase H.1.5 — auth-gate the dashboard HTML.
#
# RequireBusinessMiddleware already resolves the session cookie and sets
# request.state.user_id for HTML paths too (not just /api/*). So these route
# handlers just check state — no duplicate DB lookup.
#
# Why server-side gate instead of client-side: avoids the "flash of unstyled
# dashboard before redirect" UX problem. Unauthed users see /login directly,
# never the dashboard chrome.


def _is_authed(request: Request) -> bool:
    return getattr(request.state, "user_id", None) is not None


def _login_redirect(request: Request) -> RedirectResponse:
    # Keep where they were going (an email's ?tab=approvals&b=7 link) so
    # signing in lands them there instead of on Home.
    from urllib.parse import quote

    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    url = "/login" if target == "/" else f"/login?next={quote(target, safe='')}"
    return RedirectResponse(url=url, status_code=302)


def _safe_next(value: str | None) -> str:
    """Only same-site paths: '/?tab=x' yes; '//evil.com', 'https://...' no."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


@app.get("/", include_in_schema=False)
def root(request: Request):
    if not _is_authed(request):
        return _login_redirect(request)
    return FileResponse(ROOT / "dashboard.html")


@app.get("/dashboard.html", include_in_schema=False)
def dashboard_html(request: Request):
    # Same gate as /. Covers direct URL hits + the StaticFiles fallback.
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(ROOT / "dashboard.html")


@app.get("/login", include_in_schema=False)
def login_page(request: Request, next: str | None = None):
    # If already logged in, skip the form and go straight to where they meant to go.
    if _is_authed(request):
        return RedirectResponse(url=_safe_next(next), status_code=302)
    return FileResponse(ROOT / "login.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    # For the external uptime monitor: 200 only if the app can reach its DB.
    from sqlalchemy import text as _text

    from .db import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(_text("SELECT 1"))
    except Exception:
        log.exception("healthz: database check failed")
        return JSONResponse({"ok": False, "db": "error"}, status_code=503)
    return {"ok": True, "db": "ok"}


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    # Log it, email ALERT_EMAIL in the background (rate-limited, see
    # alerts.py), and give the browser a plain 500 instead of a traceback.
    import threading

    from .alerts import notify_error

    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    threading.Thread(
        target=notify_error, args=(exc,), kwargs={"method": request.method, "path": request.url.path},
        daemon=True,
    ).start()
    return JSONResponse({"detail": "Something went wrong on our side. Try again in a minute."}, status_code=500)


@app.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    # Amplafai operators only. Everyone else is sent to their dashboard; the
    # /api/admin/* endpoints enforce the same rule on their own.
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=302)
    if not getattr(request.state, "is_superuser", False):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(ROOT / "admin.html")


@app.get("/forgot-password", include_in_schema=False)
def forgot_password_page(request: Request):
    # Public. Signed-in users don't need it, so send them to the dashboard.
    if _is_authed(request):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(ROOT / "forgot-password.html")


@app.get("/reset-password", include_in_schema=False)
def reset_password_page(request: Request):
    # Public — opened from the emailed link. Works signed in or out (a
    # signed-in user resetting gets signed out everywhere on success).
    return FileResponse(ROOT / "reset-password.html")


@app.get("/invite", include_in_schema=False)
def invite_page(request: Request):
    # Public — invitee opens this URL with ?token=...; client-side JS in
    # invite.html calls /api/auth/invites/lookup to confirm + render details.
    return FileResponse(ROOT / "invite.html")


# Dev-only routes. ENVIRONMENT=production hides them with a 404 so a public
# pilot URL doesn't leak our verification surfaces. Listed explicitly rather
# than pattern-matched so production behavior is auditable from this file.
_DEV_ONLY_PATHS = ("/widget-test.html",)
_IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"


@app.get("/widget-test.html", include_in_schema=False)
def widget_test(request: Request):
    if _IS_PRODUCTION:
        raise HTTPException(status_code=404)
    return FileResponse(ROOT / "widget-test.html")


@app.get("/voice-briefs/{_path:path}", include_in_schema=False)
def _voice_briefs_blocked(_path: str):
    # Voice briefs are baked into the image for the API/agent to read
    # (bootstrap.voiceBrief, agent system prompt). They are business-strategy
    # content and must never be served raw through the root StaticFiles mount
    # below — this route wins because routes match before the mount.
    raise HTTPException(status_code=404)


# Public assets: ONLY the static/ directory (widget.js). Pages are served by
# the explicit routes above. This used to mount the whole project root at "/",
# which published the live SQLite DB (/data/popular_network.db), its backups,
# and the app source to anyone. Never mount ROOT again; give each new public
# file a route or put it under static/. Guarded by smoke_static_exposure.
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _main() -> None:
    import uvicorn

    # Host/port env-overridable so the droplet can bind 0.0.0.0 behind Caddy
    # while local dev stays on 127.0.0.1. proxy_headers + forwarded_allow_ips
    # let the app see real client IPs through the reverse proxy.
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    _main()
