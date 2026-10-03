"""Campaigns, statuses and daily results on Meta (Marketing API).

A Meta campaign is a hierarchy: campaign → ad set (budget, schedule,
audience) → ad (the creative). Amplafai's campaigns boost a post the owner
approved, already published to their Facebook Page (Phase 5b), so the ad is
that post (`object_story_id` = "<page id>_<post id>").

Safety rules carried into the payloads:
  * everything is created PAUSED; `activate` is the separate step a person takes;
  * the ad set gets a LIFETIME budget (daily × days) and an end time, so Meta
    itself never spends more than the campaign's total, even if Amplafai's
    server is down;
  * if any step after the campaign fails, the half-built campaign is deleted
    so nothing is left behind on the client's account.

⚠️  Written against the Marketing API docs (Oct 2026), not yet run against a
live ad account. The first real campaign is a $5/day test on Amplafai's own
account (runbook section 7).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from .client import MetaAPIError, MetaClient, MetaError

MIN_RADIUS_MILES = 10   # Meta's limits for a radius around a city
MAX_RADIUS_MILES = 50

OBJECTIVE = "OUTCOME_AWARENESS"     # reach as many nearby people as the budget allows
OPTIMIZATION_GOAL = "REACH"
BILLING_EVENT = "IMPRESSIONS"

ACCOUNT_STATUS = {
    1: "active", 2: "disabled", 3: "unsettled (a payment is overdue)", 7: "pending a risk review",
    8: "pending settlement", 9: "in a grace period", 100: "pending closure", 101: "closed",
    201: "active", 202: "closed",
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def _ts(dt: datetime) -> str:
    """Graph datetime: ISO 8601 in UTC with an explicit offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def money_to_cents(value: Any) -> int:
    """Insights `spend` is a decimal string in the account currency ("12.345").
    Decimal, not float, rounded the way an invoice is."""
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# Reads used when Amplafai links a client's account
# --------------------------------------------------------------------------- #

def read_account(client: MetaClient, act_id: str) -> dict[str, Any]:
    d = client.get(act_id, {"fields": "name,currency,account_status,timezone_name,disable_reason,spend_cap"})
    status = int(d.get("account_status") or 0)
    try:
        cap = int(d.get("spend_cap") or 0)      # the account spending limit, in cents; 0 = none
    except (TypeError, ValueError):
        cap = 0
    return {"id": d.get("id") or act_id, "name": d.get("name") or act_id, "currency": (d.get("currency") or "").upper(),
            "status": status, "statusLabel": ACCOUNT_STATUS.get(status, f"in status {status}"),
            "timezone": d.get("timezone_name"), "spendCapCents": cap}


def read_page(client: MetaClient, page_id: str) -> dict[str, Any]:
    d = client.get(page_id, {"fields": "name"})
    return {"id": d.get("id") or page_id, "name": d.get("name")}


def split_location(location: str) -> tuple[str, Optional[str]]:
    """'Windom, MN' → ('Windom', 'Minnesota'). Unknown state → None."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return "", None
    town = parts[0]
    state = None
    if len(parts) > 1:
        raw = re.sub(r"\s+\d{5}(-\d{4})?$", "", parts[1]).strip()
        state = US_STATES.get(raw.upper()) or next((v for v in US_STATES.values() if v.lower() == raw.lower()), None)
    return town, state


def find_city(client: MetaClient, location: str) -> Optional[dict[str, Any]]:
    """The business's town as a Meta targeting location, or None if Meta has
    no city by that name in that state."""
    town, state = split_location(location)
    if not town:
        return None
    rows = client.get("search", {"type": "adgeolocation", "location_types": ["city"], "q": town,
                                 "country_code": "US", "limit": 25}).get("data") or []
    for r in rows:
        if (r.get("type") in (None, "city") and (r.get("name") or "").lower() == town.lower()
                and (state is None or (r.get("region") or "").lower() == state.lower())):
            return {"key": str(r["key"]), "name": r.get("name"), "region": r.get("region")}
    return None


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #

def radius_from_hint(hint: Optional[str], default: int) -> int:
    """'Homeowners within 20 miles' → 20, kept inside Meta's 10–50 mile range."""
    m = re.search(r"(\d{1,3})\s*(?:mi\b|miles?\b)", hint or "", re.I)
    n = int(m.group(1)) if m else default
    return max(MIN_RADIUS_MILES, min(MAX_RADIUS_MILES, n))


def targeting(geo: dict[str, Any], *, audience: Optional[str], instagram: bool) -> dict[str, Any]:
    if geo.get("zip"):
        where: dict[str, Any] = {"zips": [{"key": f"US:{geo['zip']}"}]}
    else:
        where = {"cities": [{"key": geo["key"], "radius": radius_from_hint(audience, int(geo.get("radiusMiles") or 15)),
                             "distance_unit": "mile"}]}
    return {
        "geo_locations": where,
        "age_min": 18,
        "publisher_platforms": ["facebook", "instagram"] if instagram else ["facebook"],
        # Keep Meta to the area above rather than letting it widen the audience.
        "targeting_automation": {"advantage_audience": 0},
    }


def create_paused(client: MetaClient, act_id: str, *, name: str, lifetime_budget_cents: int, days: int,
                  targeting_spec: dict[str, Any], page_id: str, story_id: str,
                  instagram_id: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """Campaign + ad set + ad, all PAUSED. Returns the campaign id."""
    now = now or datetime.utcnow()
    camp = client.post(f"{act_id}/campaigns", {
        "name": name[:200], "objective": OBJECTIVE, "status": "PAUSED", "buying_type": "AUCTION",
        "special_ad_categories": [],
        # Required since v24 when the budget is on the ad set; 0 keeps each
        # ad set's budget exact (1 would let Meta move up to 20% between them).
        "is_adset_budget_sharing_enabled": 0,
    })
    campaign_id = str(camp.get("id") or "")
    if not campaign_id:
        raise MetaError("Meta didn't return a campaign ID.")
    try:
        adset = client.post(f"{act_id}/adsets", {
            "name": f"{name[:180]} · ad set", "campaign_id": campaign_id, "status": "PAUSED",
            "lifetime_budget": int(lifetime_budget_cents),
            # Provisional; activate() sets the real end from the moment it's turned on.
            "start_time": _ts(now), "end_time": _ts(now + timedelta(days=days)),
            "billing_event": BILLING_EVENT, "optimization_goal": OPTIMIZATION_GOAL,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "targeting": targeting_spec,
        })
        creative_spec: dict[str, Any] = {"object_story_id": story_id}
        if instagram_id:
            creative_spec["instagram_user_id"] = instagram_id
        creative = client.post(f"{act_id}/adcreatives", {"name": f"{name[:180]} · post", **creative_spec})
        client.post(f"{act_id}/ads", {
            "name": name[:200], "adset_id": adset["id"], "status": "PAUSED",
            "creative": {"creative_id": creative["id"]},
        })
    except (MetaError, KeyError) as e:
        try:  # nothing half-built stays on the client's account
            client.post(campaign_id, {"status": "DELETED"})
        except MetaError:
            pass
        if isinstance(e, KeyError):
            raise MetaError("Meta's reply was missing an ID; the campaign was removed.") from e
        raise
    return campaign_id


def activate(client: MetaClient, campaign_id: str, *, ends_at: datetime, now: Optional[datetime] = None) -> None:
    """Turn on (or restart) a campaign until ends_at. The campaign itself goes
    ACTIVE last, so nothing delivers until every part is ready."""
    now = now or datetime.utcnow()
    adsets = client.get_all(f"{campaign_id}/adsets", {"fields": "id,end_time,status"})
    ads = client.get_all(f"{campaign_id}/ads", {"fields": "id,status"})
    if not adsets or not ads:
        raise MetaError("This campaign has no ad on Meta yet. Amplafai adds it in Ads Manager, then it can be turned on.")
    if ends_at <= now + timedelta(hours=1):
        raise MetaError("This campaign's end date has passed, so it can't run again. Start a new one.")
    for s in adsets:
        client.post(s["id"], {"end_time": _ts(ends_at), "status": "ACTIVE"})
    for a in ads:
        if a.get("status") != "ACTIVE":
            client.post(a["id"], {"status": "ACTIVE"})
    client.post(campaign_id, {"status": "ACTIVE"})


def set_status(client: MetaClient, object_id: str, status: str) -> None:
    """PAUSED / ACTIVE / ARCHIVED / DELETED on a campaign (children follow it)."""
    client.post(object_id, {"status": status})


def daily_results(client: MetaClient, act_id: str, campaign_ids: list[str], start: date, end: date) -> list[dict[str, Any]]:
    """One row per campaign per day, days in the ad account's time zone."""
    if not campaign_ids:
        return []
    params = {"level": "campaign", "time_increment": 1,
              "time_range": {"since": start.isoformat(), "until": end.isoformat()},
              "fields": "campaign_id,spend,impressions,clicks,date_start", "limit": 500}
    try:
        rows = client.get_all(f"{act_id}/insights", {
            **params, "filtering": [{"field": "campaign.id", "operator": "IN", "value": [str(c) for c in campaign_ids]}]})
    except MetaAPIError as e:
        if e.code != 100:
            raise
        # The one-call filter was refused as a parameter error: ask each campaign instead.
        rows = []
        for cid in campaign_ids:
            rows.extend(client.get_all(f"{cid}/insights", params))
    out = []
    for r in rows:
        if not r.get("campaign_id") or not r.get("date_start"):
            continue
        out.append({"campaign_id": str(r["campaign_id"]), "day": r["date_start"],
                    "spend_cents": money_to_cents(r.get("spend")),
                    "impressions": int(r.get("impressions") or 0), "clicks": int(r.get("clicks") or 0)})
    return out
