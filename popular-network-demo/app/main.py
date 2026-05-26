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

from .db import _add_col_if_missing, init_db
from .routers import approvals, bootstrap, chat, marketing_plan, performance, posts, reviews, settings
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


app.include_router(bootstrap.router, prefix="/api", tags=["bootstrap"])
app.include_router(posts.router, prefix="/api", tags=["posts"])
app.include_router(approvals.router, prefix="/api", tags=["approvals"])
app.include_router(reviews.router, prefix="/api", tags=["reviews"])
app.include_router(performance.router, prefix="/api", tags=["performance"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(marketing_plan.router, prefix="/api", tags=["marketing-plan"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


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
