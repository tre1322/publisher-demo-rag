"""Pull campaign performance from the LinkedIn Ad Analytics API.

Maps LinkedIn's analytics rows onto the same performance_json shape the mock
tick simulator produces ({impressions, clicks, ctr}), plus real spend in cents
— so the Ads & Spend dashboard renders real numbers through the exact code path
that renders the mock ones.

⚠️  UNTESTED AGAINST THE LIVE API (skeleton ahead of MDP approval). The
adAnalytics finder uses Rest.li date-range encoding which is fiddly; validate
the first real pull against a test ad account.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .client import LinkedInClient
from .errors import LinkedInAPIError, LinkedInError

# Analytics metrics we ask LinkedIn for. costInLocalCurrency is a decimal
# string in the account currency; we convert to cents to match our schema.
_FIELDS = ("impressions", "clicks", "costInLocalCurrency", "pivotValues")


def _cost_to_cents(cost: object) -> int:
    try:
        return int(round(float(cost) * 100))
    except (TypeError, ValueError):
        return 0


def fetch_campaign_analytics(
    client: LinkedInClient,
    *,
    campaign_urns: list[str],
    start: date,
    end: date,
) -> dict[str, dict]:
    """Return {campaign_urn: {impressions, clicks, ctr, spend_cents}} for the
    date range. Campaigns with no delivery in the window are simply absent from
    the result (caller treats missing as zero).
    """
    if not campaign_urns:
        return {}

    params: dict[str, object] = {
        "q": "analytics",
        "pivot": "CAMPAIGN",
        "timeGranularity": "ALL",
        "dateRange.start.year": start.year,
        "dateRange.start.month": start.month,
        "dateRange.start.day": start.day,
        "dateRange.end.year": end.year,
        "dateRange.end.month": end.month,
        "dateRange.end.day": end.day,
        "fields": ",".join(_FIELDS),
    }
    # Rest.li list param: campaigns[0], campaigns[1], ...
    for i, urn in enumerate(campaign_urns):
        params[f"campaigns[{i}]"] = urn

    try:
        data = client.get("/rest/adAnalytics", params=params)
    except LinkedInAPIError as e:
        raise LinkedInError(f"analytics fetch failed: {e}") from e

    out: dict[str, dict] = {}
    for row in data.get("elements", []):
        # pivotValues carries the campaign URN this row aggregates.
        pivots = row.get("pivotValues") or []
        urn = pivots[0] if pivots else None
        if not urn:
            continue
        impressions = int(row.get("impressions", 0) or 0)
        clicks = int(row.get("clicks", 0) or 0)
        out[urn] = {
            "impressions": impressions,
            "clicks": clicks,
            "ctr": (clicks / impressions) if impressions else 0.0,
            "spend_cents": _cost_to_cents(row.get("costInLocalCurrency")),
        }
    return out


def date_range_last_n_days(n: int, *, today: Optional[date] = None) -> tuple[date, date]:
    """Convenience: (start, end) spanning the last n days inclusive of today."""
    from datetime import timedelta

    end = today or date.today()
    return end - timedelta(days=max(0, n - 1)), end
