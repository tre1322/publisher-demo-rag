"""Phase 5a — read real spend from the ad platforms' APIs on a schedule.

For every campaign running through a connected platform API, pull the last
week of daily results and store them exactly like an export upload (Phase 4b,
app/ad_imports.store_days): per-day rows that replace earlier numbers for the
same day, campaign and monthly totals summed from them, then the cap check.
At 100% of a monthly cap, campaigns still running are paused through the
platform's API (or, if that fails, Amplafai is asked to pause them by hand).

Runs every few hours in production (POPULAR_AD_SYNC_LOOP=1/0 overrides).
Campaigns Amplafai runs by hand keep using export uploads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ad_imports, ad_platforms, managed_ads
from .db import current_tenant_id
from .models import AdCampaign, Business

log = logging.getLogger("popular_network.ad_sync")

LOOKBACK_DAYS = 7          # platforms revise recent days; re-read a week each time
SYNC_EVERY_HOURS = 3


def _campaigns(db: Session, business_id: int, platform: str, now: datetime) -> list[AdCampaign]:
    """Launched campaigns on this platform that could have spent in the window."""
    since = now - timedelta(days=LOOKBACK_DAYS + 1)
    rows = db.query(AdCampaign).filter(AdCampaign.business_id == business_id, AdCampaign.platform == platform,
                                       AdCampaign.launched_at.is_not(None)).all()
    return [c for c in rows if managed_ads.on_platform(c)
            and (c.status in ("active", "paused") or (c.ended_at or now) >= since)]


def sync_business(db: Session, biz: Business, *, now: Optional[datetime] = None, by: str = "scheduled sync") -> dict[str, Any]:
    """Pull and store results for one business. Never commits."""
    now = now or datetime.utcnow()
    out: dict[str, Any] = {"platforms": {}, "alerts": []}
    for platform in ad_imports.PLATFORMS:
        campaigns = _campaigns(db, biz.id, platform, now)
        if not campaigns:
            continue
        adapter = ad_platforms.adapter_for(db, biz.id, platform)
        if adapter is None:
            continue
        by_ext = {c.external_campaign_id: c for c in campaigns}
        start = (now - timedelta(days=LOOKBACK_DAYS)).date()
        try:
            results = adapter.daily_results(list(by_ext), start, now.date())
        except ad_platforms.PlatformError as e:
            ad_platforms.record(db, business_id=biz.id, platform=platform, action="sync", actor=by,
                                source="system", ok=False, detail=str(e))
            out["platforms"][platform] = {"ok": False, "error": str(e)}
            continue
        finally:
            adapter.close()
        matched: dict[int, dict[str, dict[str, int]]] = {}
        for r in results:
            c = by_ext.get(r.external_id)
            if c is None:
                continue
            d = matched.setdefault(c.id, {}).setdefault(r.day, {"spend_cents": 0, "impressions": 0, "clicks": 0})
            d["spend_cents"] += r.spend_cents
            d["impressions"] += r.impressions
            d["clicks"] += r.clicks
        label = ad_platforms.LABELS.get(platform, platform)
        alerts = ad_imports.store_days(db, biz, platform, matched, {c.id: c for c in campaigns},
                                       source=f"{label} (synced from its API)", import_id=None, now=now, by=by)
        ad_platforms.record(db, business_id=biz.id, platform=platform, action="sync", actor=by, source="system",
                            detail=f"{len(results)} day rows for {len(matched)} campaign(s), {start} to {now.date()}.")
        out["platforms"][platform] = {"ok": True, "rows": len(results), "campaigns": len(matched)}
        out["alerts"] += alerts
    return out


def run_all(db: Session, *, now: Optional[datetime] = None) -> dict[int, dict[str, Any]]:
    """Every real client with something to sync. Commits per business so one
    platform outage never blocks the others."""
    done: dict[int, dict[str, Any]] = {}
    for biz in db.execute(select(Business).execution_options(include_all_tenants=True)).scalars().all():
        if not managed_ads.is_managed(biz):
            continue
        token = current_tenant_id.set(biz.id)
        try:
            res = sync_business(db, biz, now=now)
            if res["platforms"]:
                db.commit()
                done[biz.id] = res
            else:
                db.rollback()
        except Exception:
            db.rollback()
            log.exception("Ad sync failed for business %s", biz.id)
        finally:
            current_tenant_id.reset(token)
    return done
