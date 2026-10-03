"""Phase 5a — one interface to every ad platform's API.

Each platform (LinkedIn now, Meta in 5c) plugs in an adapter with the same
five actions. `adapter_for` returns one only when that platform's API is
switched on for the app AND this business has a working connection to it;
otherwise it returns None and the change goes to Amplafai's hand queue
(app/managed_ads.py), exactly as in Phase 4.

The safety rules live with the callers, not the adapters:
  * campaigns are always created PAUSED; turning one on is a separate step a
    person takes (POST /api/ads/campaigns/{id}/turn-on), and it is logged;
  * pause / restart / cancel call the platform, and if the platform call
    fails the change falls back to a request for Amplafai to do by hand;
  * every call, success or failure, is written to the action log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional, Protocol

from sqlalchemy.orm import Session

from .models import AdActionLog

log = logging.getLogger("popular_network.ad_platforms")

LABELS = {"fb_ig": "Meta", "google_ads": "Google Ads", "tiktok": "TikTok", "linkedin": "LinkedIn"}


class PlatformError(Exception):
    """The ad platform refused or couldn't be reached. Message is owner-safe."""


@dataclass
class DayResult:
    external_id: str
    day: str            # YYYY-MM-DD, the platform's reporting day
    spend_cents: int
    impressions: int
    clicks: int


class Adapter(Protocol):
    platform: str

    def create_paused(self, *, name: str, daily_budget_cents: int, duration_days: int,
                      audience: Optional[str]) -> str: ...

    def activate(self, external_id: str, *, ends_at: datetime) -> None: ...

    def pause(self, external_id: str) -> None: ...

    def cancel(self, external_id: str) -> None: ...

    def daily_results(self, external_ids: list[str], start: date, end: date) -> list[DayResult]: ...

    def close(self) -> None: ...


Factory = Callable[[Session, int], Optional[Adapter]]
_FACTORIES: dict[str, Factory] = {}


def register(platform: str, factory: Factory) -> None:
    _FACTORIES[platform] = factory


def adapter_for(db: Session, business_id: int, platform: str) -> Optional[Adapter]:
    """A live adapter for this business + platform, or None (Amplafai by hand)."""
    factory = _FACTORIES.get(platform)
    if factory is None:
        return None
    try:
        return factory(db, business_id)
    except Exception:  # a broken connection means "by hand", never a crash
        log.exception("Couldn't build the %s adapter for business %s", platform, business_id)
        return None


def record(db: Session, *, business_id: int, action: str, actor: Optional[str], source: Optional[str],
           ok: bool = True, detail: Optional[str] = None, campaign_id: Optional[int] = None,
           platform: Optional[str] = None) -> None:
    """Write one line to the action log (never commits)."""
    db.add(AdActionLog(business_id=business_id, campaign_id=campaign_id, platform=platform, action=action,
                       actor=actor, source=source, ok=ok, detail=(detail or "")[:2000],
                       created_at=datetime.utcnow()))


# --------------------------------------------------------------------------- #
# LinkedIn (switched on by LINKEDIN_CLIENT_ID/SECRET + a connected ad account)
# --------------------------------------------------------------------------- #

class LinkedInAdapter:
    platform = "linkedin"

    def __init__(self, client, account_urn: str):
        self._client = client
        self._account = account_urn

    def _call(self, fn, *args, **kwargs):
        from .integrations import linkedin as li

        try:
            return fn(*args, **kwargs)
        except li.LinkedInError as e:
            raise PlatformError(f"LinkedIn said: {e}") from e

    def create_paused(self, *, name, daily_budget_cents, duration_days, audience):
        from .integrations import linkedin as li

        return self._call(li.create_boost_campaign, self._client, account_urn=self._account, name=name,
                          daily_budget_cents=daily_budget_cents, duration_days=duration_days, status="PAUSED")

    def activate(self, external_id, *, ends_at):
        from .integrations import linkedin as li

        self._call(li.set_campaign_status, self._client, external_id, "ACTIVE")

    def pause(self, external_id):
        from .integrations import linkedin as li

        self._call(li.set_campaign_status, self._client, external_id, "PAUSED")

    def cancel(self, external_id):
        from .integrations import linkedin as li

        self._call(li.set_campaign_status, self._client, external_id, "ARCHIVED")

    def daily_results(self, external_ids, start, end):
        from .integrations import linkedin as li

        rows = self._call(li.fetch_daily_analytics, self._client, campaign_urns=external_ids, start=start, end=end)
        return [DayResult(r["campaign_urn"], r["day"], r["spend_cents"], r["impressions"], r["clicks"]) for r in rows]

    def close(self):
        self._client.close()


def _linkedin_factory(db: Session, business_id: int) -> Optional[Adapter]:
    from .integrations import linkedin as li
    from .integrations import linkedin_store as store

    if not li.is_live():
        return None
    conn = store.get_connection(db, business_id)
    if not store.connection_is_live(conn) or not conn.account_urn:
        return None
    return LinkedInAdapter(store.build_client(db, conn), conn.account_urn)


register("linkedin", _linkedin_factory)
