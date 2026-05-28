"""Ads router — Phase E.1.

Endpoints:
    GET    /api/ads                            slice for the Ads & Spend view
    GET    /api/ads/budgets                    list per-platform caps for current month
    PUT    /api/ads/budgets/{platform}         set/update monthly cap
    GET    /api/ads/campaigns                  list w/ filters
    POST   /api/ads/campaigns                  create new campaign
    PUT    /api/ads/campaigns/{id}             edit (pause/resume/change budget)
    DELETE /api/ads/campaigns/{id}             cancel
    POST   /api/ads/campaigns/{id}/approve     Tier 2 owner-approve path
    POST   /api/ads/tick                       advance mock spend + impressions
    POST   /api/ads/connections                connect mock ad account
    DELETE /api/ads/connections/{id}           disconnect

Mocked execution: ad_campaigns get fake external_campaign_id strings on
schedule; /api/ads/tick advances actual_spend_cents on active campaigns
based on daily_budget × elapsed_hours / 24, plus plausible impressions
using platform CPM constants. Real OAuth + Meta/Google/TikTok APIs are
deferred to V2 — the dashboard UX is identical regardless.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.deps import get_tenant_id
from ..db import get_db
from ..models import (
    AdCampaign,
    AdConnection,
    AdPlatformBudget,
    Approval,
    Business,
    Post,
)

router = APIRouter()


# Platform keys we accept. Anywhere else in the app should import this set
# (or its values) rather than hardcoding strings.
AD_PLATFORMS = ("fb_ig", "google_ads", "tiktok", "linkedin")
AdPlatform = Literal["fb_ig", "google_ads", "tiktok", "linkedin"]


# Platform CPM constants — cost per 1000 impressions in cents.
# Calibrated against published 2025 rural-Minnesota benchmarks so a sales
# rep can defend the numbers. fb_ig is cheapest (broadest reach inventory),
# linkedin is most expensive (premium B2B audience).
_PLATFORM_CPM_CENTS = {
    "fb_ig":      800,   # $8.00 CPM
    "google_ads": 1500,  # $15.00 CPM
    "tiktok":     600,   # $6.00 CPM
    "linkedin":   3500,  # $35.00 CPM (B2B premium)
}

# Click-through rate by platform. Numbers reflect typical small-business
# results, not aspirational benchmarks. Used by the tick simulator to
# advance impressions → clicks plausibly.
_PLATFORM_CTR = {
    "fb_ig":      0.012,  # 1.2%
    "google_ads": 0.035,  # 3.5% (intent traffic)
    "tiktok":     0.018,  # 1.8%
    "linkedin":   0.009,  # 0.9%
}


def _current_month_year() -> str:
    return datetime.utcnow().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------

class BudgetUpdateBody(BaseModel):
    monthly_cap_cents: int = Field(ge=0, description="New monthly cap in cents. 0 disables.")
    status: Optional[Literal["active", "paused"]] = None


class CampaignCreateBody(BaseModel):
    platform: AdPlatform
    name: str = Field(min_length=1, max_length=160)
    daily_budget_cents: int = Field(ge=100, description="Min $1/day to be a real campaign.")
    duration_days: int = Field(ge=1, le=90)
    post_id: Optional[int] = None
    target_audience: Optional[str] = Field(default=None, max_length=400)
    origin: Literal["manual_owner", "agent_proposed", "agent_autonomous"] = "manual_owner"


class CampaignUpdateBody(BaseModel):
    status: Optional[Literal["scheduled", "active", "paused", "cancelled"]] = None
    daily_budget_cents: Optional[int] = Field(default=None, ge=100)


class ConnectBody(BaseModel):
    platform: AdPlatform
    account_label: Optional[str] = None  # owner can rename if desired


# ---------------------------------------------------------------------------
# Helpers — payload shapers (reused by bootstrap)
# ---------------------------------------------------------------------------

def budget_payload(b: AdPlatformBudget) -> dict[str, Any]:
    remaining = max(0, b.monthly_cap_cents - b.spend_cents)
    pct_used = (b.spend_cents / b.monthly_cap_cents) if b.monthly_cap_cents else 0.0
    return {
        "id":              b.id,
        "platform":        b.platform,
        "monthYear":       b.month_year,
        "monthlyCapCents": b.monthly_cap_cents,
        "spendCents":      b.spend_cents,
        "remainingCents":  remaining,
        "pctUsed":         pct_used,
        "status":          b.status,
    }


def campaign_payload(c: AdCampaign) -> dict[str, Any]:
    return {
        "id":                 c.id,
        "businessId":         c.business_id,
        "postId":             c.post_id,
        "platform":           c.platform,
        "name":               c.name,
        "dailyBudgetCents":   c.daily_budget_cents,
        "durationDays":       c.duration_days,
        "plannedTotalCents":  c.planned_total_cents,
        "actualSpendCents":   c.actual_spend_cents,
        "targetAudience":     c.target_audience_json,
        "status":             c.status,
        "origin":             c.origin,
        "approvedBy":         c.approved_by,
        "externalCampaignId": c.external_campaign_id,
        "performance":        c.performance_json or {},
        "createdAt":          c.created_at.isoformat() if c.created_at else None,
        "scheduledFor":       c.scheduled_for.isoformat() if c.scheduled_for else None,
        "endedAt":            c.ended_at.isoformat() if c.ended_at else None,
    }


def connection_payload(c: AdConnection) -> dict[str, Any]:
    return {
        "id":                 c.id,
        "platform":           c.platform,
        "accountLabel":       c.account_label,
        "externalAccountId":  c.external_account_id,
        "status":             c.status,
        "lastSyncedAt":       c.last_synced_at.isoformat() if c.last_synced_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ads")
def get_ads(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Bootstrap-equivalent for the Ads & Spend view.

    Returns budgets (current month), all campaigns, all connections, and
    aggregate stats. The dashboard calls this on AdsView mount and after
    every mutation.
    """
    month = _current_month_year()
    budgets = (
        db.query(AdPlatformBudget)
        .filter(AdPlatformBudget.business_id == business_id, AdPlatformBudget.month_year == month)
        .all()
    )
    campaigns = (
        db.query(AdCampaign)
        .filter(AdCampaign.business_id == business_id)
        .order_by(AdCampaign.created_at.desc())
        .all()
    )
    connections = (
        db.query(AdConnection)
        .filter(AdConnection.business_id == business_id)
        .all()
    )

    total_cap     = sum(b.monthly_cap_cents for b in budgets)
    total_spend   = sum(b.spend_cents for b in budgets)
    active_count  = sum(1 for c in campaigns if c.status == "active")
    pending_count = sum(1 for c in campaigns if c.status == "pending_approval")

    # Aggregate performance across all campaigns this month.
    total_impressions = sum((c.performance_json or {}).get("impressions", 0) for c in campaigns)
    total_clicks      = sum((c.performance_json or {}).get("clicks", 0) for c in campaigns)
    avg_ctr           = (total_clicks / total_impressions) if total_impressions else 0.0

    return {
        "monthYear":         month,
        "budgets":           [budget_payload(b) for b in budgets],
        "campaigns":         [campaign_payload(c) for c in campaigns],
        "connections":       [connection_payload(c) for c in connections],
        "totalCapCents":     total_cap,
        "totalSpendCents":   total_spend,
        "activeCount":       active_count,
        "pendingCount":      pending_count,
        "totalImpressions":  total_impressions,
        "totalClicks":       total_clicks,
        "avgCtr":            avg_ctr,
        "hasAnyCap":         total_cap > 0,
        "hasAnyCampaign":    len(campaigns) > 0,
        "hasAnyConnection":  any(c.status == "connected" for c in connections),
    }


@router.get("/ads/budgets")
def list_budgets(business_id: int = Depends(get_tenant_id), db: Session = Depends(get_db)) -> dict[str, Any]:
    month = _current_month_year()
    rows = (
        db.query(AdPlatformBudget)
        .filter(AdPlatformBudget.business_id == business_id, AdPlatformBudget.month_year == month)
        .all()
    )
    return {"monthYear": month, "budgets": [budget_payload(r) for r in rows]}


@router.put("/ads/budgets/{platform}")
def update_budget(
    platform: AdPlatform,
    body: BudgetUpdateBody,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    month = _current_month_year()
    row = (
        db.query(AdPlatformBudget)
        .filter(
            AdPlatformBudget.business_id == business_id,
            AdPlatformBudget.platform == platform,
            AdPlatformBudget.month_year == month,
        )
        .one_or_none()
    )
    if row is None:
        # First budget set for this platform/month — insert.
        row = AdPlatformBudget(
            business_id=business_id,
            platform=platform,
            month_year=month,
            monthly_cap_cents=body.monthly_cap_cents,
            spend_cents=0,
            status=body.status or "active",
        )
        db.add(row)
    else:
        row.monthly_cap_cents = body.monthly_cap_cents
        if body.status:
            row.status = body.status
        row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return budget_payload(row)


@router.get("/ads/campaigns")
def list_campaigns(
    business_id: int = Depends(get_tenant_id),
    status: Optional[str] = Query(None, description="Filter by status"),
    platform: Optional[str] = Query(None, description="Filter by platform"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(AdCampaign).filter(AdCampaign.business_id == business_id)
    if status:
        q = q.filter(AdCampaign.status == status)
    if platform:
        q = q.filter(AdCampaign.platform == platform)
    rows = q.order_by(AdCampaign.created_at.desc()).all()
    return [campaign_payload(c) for c in rows]


@router.post("/ads/campaigns")
def create_campaign(
    body: CampaignCreateBody,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")

    # If post_id supplied, verify it belongs to this business.
    if body.post_id is not None:
        post = db.get(Post, body.post_id)
        if post is None or post.business_id != business_id:
            raise HTTPException(status_code=400, detail=f"post {body.post_id} does not belong to this business")

    # Manual creation by the owner always lands as scheduled (the owner
    # already approved by clicking 'Create'). Agent-proposed → pending_approval
    # at Tier 2. Agent-autonomous → scheduled at Tier 3.
    if body.origin == "agent_proposed":
        status = "pending_approval"
        approved_by = None
    elif body.origin == "agent_autonomous":
        status = "scheduled"
        approved_by = "agent"
    else:
        status = "scheduled"
        approved_by = "owner"

    planned = body.daily_budget_cents * body.duration_days

    audience_json: Optional[dict[str, Any]] = None
    if body.target_audience:
        audience_json = {"hint": body.target_audience}

    campaign = AdCampaign(
        business_id=business_id,
        post_id=body.post_id,
        platform=body.platform,
        name=body.name,
        daily_budget_cents=body.daily_budget_cents,
        duration_days=body.duration_days,
        planned_total_cents=planned,
        actual_spend_cents=0,
        target_audience_json=audience_json,
        status=status,
        origin=body.origin,
        approved_by=approved_by,
        external_campaign_id=_mock_external_id(body.platform) if status == "scheduled" else None,
        performance_json={"impressions": 0, "clicks": 0, "ctr": 0.0},
        scheduled_for=datetime.utcnow() if status == "scheduled" else None,
        last_ticked_at=None,
    )
    db.add(campaign)
    db.flush()

    # Tier 2 proposal → also create an Approval row so the owner sees it
    # in the existing Approvals queue.
    if status == "pending_approval":
        db.add(Approval(
            business_id=business_id,
            external_id=f"boost-{campaign.id}",
            post_id=body.post_id,
            kind="boost",
            platform=body.platform,
            title=f"Boost: {body.name}",
            draft=f"${body.daily_budget_cents / 100:.0f}/day × {body.duration_days} days "
                  f"(${planned / 100:.0f} total) on {body.platform}.",
            note=f"Proposed by Quadd's AI agent. Click Approve to schedule.",
        ))

    db.commit()
    db.refresh(campaign)
    return campaign_payload(campaign)


@router.put("/ads/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int,
    body: CampaignUpdateBody,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    c = db.get(AdCampaign, campaign_id)
    if c is None or c.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id} not found")

    if body.status:
        # Status state machine: pending_approval is owner-approval-only via
        # /approve endpoint; everything else can transition freely.
        if c.status == "pending_approval" and body.status != "cancelled":
            raise HTTPException(
                status_code=409,
                detail="Use POST /api/ads/campaigns/{id}/approve to advance from pending_approval.",
            )
        c.status = body.status
        if body.status == "cancelled":
            c.ended_at = datetime.utcnow()
    if body.daily_budget_cents is not None:
        if c.status in ("active", "completed"):
            raise HTTPException(status_code=409, detail="Cannot change budget on active/completed campaigns.")
        c.daily_budget_cents = body.daily_budget_cents
        c.planned_total_cents = c.daily_budget_cents * c.duration_days

    db.commit()
    db.refresh(c)
    return campaign_payload(c)


@router.delete("/ads/campaigns/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    c = db.get(AdCampaign, campaign_id)
    if c is None or c.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id} not found")
    c.status = "cancelled"
    c.ended_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": campaign_id, "status": "cancelled"}


@router.post("/ads/campaigns/{campaign_id}/approve")
def approve_campaign(
    campaign_id: int,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    c = db.get(AdCampaign, campaign_id)
    if c is None or c.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id} not found")
    if c.status != "pending_approval":
        raise HTTPException(status_code=409, detail=f"campaign is {c.status}, not pending_approval")
    c.status = "scheduled"
    c.approved_by = "owner"
    c.scheduled_for = datetime.utcnow()
    c.external_campaign_id = _mock_external_id(c.platform)
    # Also resolve the matching Approval row.
    approval = (
        db.query(Approval)
        .filter(
            Approval.business_id == business_id,
            Approval.external_id == f"boost-{campaign_id}",
            Approval.decision.is_(None),
        )
        .one_or_none()
    )
    if approval is not None:
        approval.decision = "approved"
        approval.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return campaign_payload(c)


@router.post("/ads/tick")
def tick_simulator(
    business_id: int = Depends(get_tenant_id),
    hours: float = Query(24.0, ge=0.1, le=24.0, description="Hours to advance"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Advance mock spend + impressions on active and scheduled campaigns.

    Pure stand-in for real Meta/Google/TikTok/LinkedIn API polling. Called
    by a dashboard button ('Simulate today's performance') for predictable
    demo behavior. Real production would replace this with platform-specific
    insights API calls on a cron.
    """
    now = datetime.utcnow()
    campaigns = (
        db.query(AdCampaign)
        .filter(
            AdCampaign.business_id == business_id,
            AdCampaign.status.in_(("scheduled", "active")),
        )
        .all()
    )
    month = _current_month_year()
    budgets_by_platform: dict[str, AdPlatformBudget] = {
        b.platform: b
        for b in db.query(AdPlatformBudget)
        .filter(AdPlatformBudget.business_id == business_id, AdPlatformBudget.month_year == month)
        .all()
    }

    advanced = 0
    completed = 0
    for c in campaigns:
        # Scheduled campaigns become active on first tick.
        if c.status == "scheduled":
            c.status = "active"

        # Daily spend rate × elapsed hours.
        spend_this_tick = int(round(c.daily_budget_cents * hours / 24))
        # Don't let a single campaign overshoot its planned total.
        max_remaining = c.planned_total_cents - c.actual_spend_cents
        spend_this_tick = max(0, min(spend_this_tick, max_remaining))

        # Honor the per-platform monthly cap. If exceeded, pause campaign.
        budget_row = budgets_by_platform.get(c.platform)
        if budget_row and budget_row.monthly_cap_cents > 0:
            cap_remaining = max(0, budget_row.monthly_cap_cents - budget_row.spend_cents)
            if cap_remaining <= 0:
                c.status = "paused"
                continue
            spend_this_tick = min(spend_this_tick, cap_remaining)

        # Compute plausible impressions + clicks from CPM and CTR constants.
        cpm = _PLATFORM_CPM_CENTS.get(c.platform, 1000)
        ctr = _PLATFORM_CTR.get(c.platform, 0.012)
        impressions_this_tick = int(spend_this_tick * 1000 / cpm) if cpm else 0
        # ±15% noise on clicks so the numbers feel real, not arithmetical.
        clicks_this_tick = int(impressions_this_tick * ctr * random.uniform(0.85, 1.15))

        c.actual_spend_cents += spend_this_tick
        if budget_row:
            budget_row.spend_cents += spend_this_tick

        perf = dict(c.performance_json or {})
        perf["impressions"] = perf.get("impressions", 0) + impressions_this_tick
        perf["clicks"]      = perf.get("clicks", 0) + clicks_this_tick
        perf["ctr"]         = (perf["clicks"] / perf["impressions"]) if perf["impressions"] else 0.0
        c.performance_json = perf

        c.last_ticked_at = now
        advanced += 1

        # Mark complete if it ran out budget or reached plan.
        if c.actual_spend_cents >= c.planned_total_cents:
            c.status = "completed"
            c.ended_at = now
            completed += 1

    db.commit()
    return {
        "ok":         True,
        "tickedAt":   now.isoformat(),
        "hours":      hours,
        "advanced":   advanced,
        "completed":  completed,
    }


@router.post("/ads/connections")
def connect_account(
    body: ConnectBody,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Mock OAuth flow. Real OAuth deferred to V2."""
    row = (
        db.query(AdConnection)
        .filter(AdConnection.business_id == business_id, AdConnection.platform == body.platform)
        .one_or_none()
    )
    label = body.account_label or _default_account_label(body.platform)
    if row is None:
        row = AdConnection(
            business_id=business_id,
            platform=body.platform,
            account_label=label,
            external_account_id=_mock_external_id(body.platform),
            oauth_token="mock_token_" + _mock_external_id(body.platform).split("_")[-1],
            status="connected",
            last_synced_at=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.status = "connected"
        row.account_label = label
        row.external_account_id = _mock_external_id(body.platform)
        row.oauth_token = "mock_token_" + row.external_account_id.split("_")[-1]
        row.last_synced_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return connection_payload(row)


@router.delete("/ads/connections/{connection_id}")
def disconnect_account(
    connection_id: int,
    business_id: int = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.get(AdConnection, connection_id)
    if row is None or row.business_id != business_id:
        raise HTTPException(status_code=404, detail=f"connection {connection_id} not found")
    row.status = "disconnected"
    row.external_account_id = None
    row.oauth_token = None
    db.commit()
    return {"ok": True, "id": connection_id, "status": "disconnected"}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_external_id(platform: str) -> str:
    """Synthetic external IDs that look like real platform campaign/account IDs."""
    suffix = random.randint(1000, 9999)
    prefixes = {
        "fb_ig":      "mock_meta",
        "google_ads": "mock_google",
        "tiktok":     "mock_tiktok",
        "linkedin":   "mock_linkedin",
    }
    return f"{prefixes.get(platform, 'mock')}_{suffix}"


def _default_account_label(platform: str) -> str:
    labels = {
        "fb_ig":      "Meta Ads — Quadd.ai (mock)",
        "google_ads": "Google Ads — Quadd.ai (mock)",
        "tiktok":     "TikTok Ads — Quadd.ai (mock)",
        "linkedin":   "LinkedIn Campaign Manager — Quadd.ai (mock)",
    }
    return labels.get(platform, f"{platform} (mock)")
