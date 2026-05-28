"""FastAPI app for the Popular Network marketing dashboard.

Layout:
- /api/*          → JSON endpoints (bootstrap + per-tab CRUD)
- everything else → static files (dashboard.html, future assets/)

Run with `python -m app.main` (or `uv run python -m app.main`). The original
`serve.py` is preserved as a thin shim that delegates here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI

# Phase C: load ANTHROPIC_API_KEY before any router imports the SDK.
# find_dotenv walks up from CWD, so this picks up either popular-network-demo/.env
# or the parent publisher-demo-rag/.env if you only set the key once.
#
# override=True is deliberate: some shells export ANTHROPIC_API_KEY="" (empty
# string) at login, which dotenv's default override=False treats as "already
# set" and refuses to overwrite. The result: load_dotenv returns True but the
# value stays empty. Override=True ensures the .env value wins.
load_dotenv(find_dotenv(usecwd=True), override=True)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from .auth.middleware import RequireBusinessMiddleware
from .db import _add_col_if_missing, init_db
from .routers import (
    ads,
    approvals,
    auth,
    billing,
    bootstrap,
    chat,
    chatbot,
    compose,
    inventory,
    marketing_plan,
    performance,
    posts,
    reach,
    reviews,
    settings,
)
from .seed import seed_if_empty

ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("popular_network")

app = FastAPI(title="Popular Network — Marketing Dashboard", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Forward-migrations for existing dev DBs that pre-date a column.
    # init_db() above creates all tables fresh; this catches the case where
    # the table exists but a newer column hasn't been added yet.
    _add_col_if_missing("settings", "notifications_json", "JSON")  # B.4
    inserted = seed_if_empty()
    if inserted:
        log.info("Seeded Quadd.ai (business_id=1) — Day-1 customer w/ voice brief loaded")
    else:
        log.info("DB already seeded — skipping")
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


def _backfill_reach_tiers() -> None:
    """Insert the Phase D reach-tier ladder for any business that doesn't have it."""
    from .db import SessionLocal
    from .models import Business, ReachTier
    from .seed import _seed_reach_tiers

    with SessionLocal() as db:
        for biz in db.query(Business).all():
            has_any = db.query(ReachTier).filter(ReachTier.business_id == biz.id).first()
            if has_any is None:
                _seed_reach_tiers(db, business_id=biz.id)
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
            # their concierge wording.
            if biz.tier >= 4:
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
                                "detail": "Tier 4 unlocks live inventory sync — DealerCenter / vAuto / MLS / TractorHouse. Listings show up in your publisher's chatbot search within an hour.",
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
    from .models import DashboardNotices, SettingsRow

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
        for notices in db.query(DashboardNotices).all():
            if not notices.attention_json:
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
app.include_router(inventory.router, prefix="/api", tags=["inventory"])
app.include_router(billing.router, prefix="/api", tags=["billing"])
app.include_router(chatbot.router, prefix="/api", tags=["chatbot"])
app.include_router(compose.router, prefix="/api", tags=["compose"])


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(ROOT / "dashboard.html")


# Static files: serve dashboard.html and (future) assets/ at the root.
# This MUST be last so /api/* routes win.
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")


def _main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
