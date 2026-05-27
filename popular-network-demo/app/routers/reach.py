"""Reach router — Phase D.1.

POST /api/reach/estimate
    body:  { tier_key, base_rate_cents, days, platforms[], audience_hint? }
    returns: { tier, estimated_impressions, estimated_unique_reach,
               cost_breakdown: { base_cents, uplift_cents, total_cents },
               territories: [...] }

The endpoint is called live by the Reach Configurator in ComposeView whenever
the owner picks a tier or changes the daily budget. Math is deterministic and
runs in-process; later phases swap for real impression-supply data from the
chatbot's ad-serve pipeline.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ReachTier

router = APIRouter()


class ReachEstimateBody(BaseModel):
    tier_key: Literal["local", "regional", "network", "maximum"]
    base_rate_cents: int = Field(ge=0, description="Selling pub's base ad rate in cents (e.g. 20000 for $200)")
    days: int = Field(ge=1, le=90, description="Run duration in days")
    platforms: list[str] = Field(default_factory=list, description="Platforms the ad runs on (fb/ig/gbp/web)")
    audience_hint: Optional[str] = None


def estimate_reach(
    *,
    tier: ReachTier,
    base_rate_cents: int,
    days: int,
    platforms: list[str],
) -> dict[str, Any]:
    """Compute price + impression estimate for a reach tier.

    This is the function the Reach Configurator card in ComposeView calls
    every time the owner picks a tier. The output drives THREE numbers the
    advertiser sees before submitting:
      - cost_breakdown.total_cents  → "$300" headline
      - cost_breakdown.uplift_cents → "+$100" delta vs base
      - estimated_impressions       → "~18,400 impressions"
      - estimated_unique_reach      → "~12,200 unique people"

    Calibrated against the pilot-phase chatbot supply assumption in
    docs/amplora_business_plan.md §6 ("1,000+ MAU per pilot territory within
    90 days"). At ~150 chatbot queries/day per territory and ~30% ad-surface
    rate, that's ~400 ad-impressions/day per territory — the per-territory
    daily baseline below.

    Worked example for a $200 nursing help-wanted ad, 7 days, fb+web:
        local    → $200,  ~3.4k impressions,  ~2.7k unique
        regional → $300,  ~13.4k impressions, ~10.7k unique
        network  → $400,  ~26.9k impressions, ~21.5k unique
        maximum  → $550,  ~40.3k impressions, ~32.2k unique

    Model:
      - Impressions scale LINEARLY in territory count (Trevor's call — each
        added town is incremental supply, not diminishing).
      - Platform factor is SUBLINEAR (FB↔IG audience overlap is real, so
        going 1→4 platforms is +45%, not +300%).
      - Unique-reach rate falls with days (saturation: same locals see your
        ad more times as the campaign runs longer), floored at 40%.
      - Cost math is exact: total = base × (1 + multiplier_pct/100). This
        is the contract publishers sell against.
    """
    PER_TERRITORY_DAILY_IMPRESSIONS = 400
    PLATFORM_FACTOR = {1: 1.0, 2: 1.2, 3: 1.35, 4: 1.45}

    n_territories = len(tier.territories_json)
    n_platforms = max(1, len(platforms))
    platform_factor = PLATFORM_FACTOR.get(n_platforms, 1.45)

    raw_impressions = n_territories * PER_TERRITORY_DAILY_IMPRESSIONS * days * platform_factor
    impressions = int(round(raw_impressions / 100) * 100)  # nearest 100 — feels like an estimate

    # Day 1 → 95% unique; day 7 → 80%; day 30+ → floored at 40%.
    unique_rate = max(0.40, 0.95 - 0.025 * (days - 1))
    unique = int(round(impressions * unique_rate / 100) * 100)

    uplift = base_rate_cents * tier.multiplier_pct // 100

    return {
        "estimated_impressions": impressions,
        "estimated_unique_reach": unique,
        "cost_breakdown": {
            "base_cents":   base_rate_cents,
            "uplift_cents": uplift,
            "total_cents":  base_rate_cents + uplift,
        },
    }


@router.post("/reach/estimate")
def post_reach_estimate(
    body: ReachEstimateBody,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    tier = (
        db.query(ReachTier)
        .filter(ReachTier.business_id == business_id, ReachTier.tier_key == body.tier_key)
        .one_or_none()
    )
    if tier is None:
        raise HTTPException(status_code=404, detail=f"no reach tier '{body.tier_key}' for business {business_id}")

    math = estimate_reach(
        tier=tier,
        base_rate_cents=body.base_rate_cents,
        days=body.days,
        platforms=body.platforms,
    )

    return {
        "tier": {
            "key": tier.tier_key,
            "label": tier.label,
            "multiplierPct": tier.multiplier_pct,
            "radiusMiles": tier.radius_miles,
            "description": tier.description,
        },
        "estimatedImpressions": math["estimated_impressions"],
        "estimatedUniqueReach": math["estimated_unique_reach"],
        "costBreakdown": {
            "baseCents":   math["cost_breakdown"]["base_cents"],
            "upliftCents": math["cost_breakdown"]["uplift_cents"],
            "totalCents":  math["cost_breakdown"]["total_cents"],
        },
        "territories": tier.territories_json,
    }
