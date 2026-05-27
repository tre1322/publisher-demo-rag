"""Billing / Usage router — Phase F.2.

Endpoints:
    GET    /api/billing                            slice for Settings → Billing sub-tab
    GET    /api/billing/usage                      current-month usage counters
    GET    /api/billing/invoices                   invoice history
    POST   /api/billing/change-tier-request        record tier-change intent (no charge)

Stripe is NOT wired into this demo. Invoices are mock; "Change tier" records
intent only. When Stripe integration lands later, `external_invoice_id` on
BillingInvoice gets filled and the change-tier endpoint becomes the gate to a
real stripe.Subscription.modify call. The Tier 4 prices come from the business
plan §3.3 — Base $799, Plus $1,299, Enterprise $1,999.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    BillingInvoice,
    Business,
    TierChangeRequest,
    UsageMetric,
)

router = APIRouter()


# Tier → monthly price in dollars. Source: docs/amplora_business_plan.md.
# Tier 4 sub-tiers collapse to "Tier 4 Base" here — multi-tier (Plus /
# Enterprise) is a Tier 4 internal SKU detail, not an upgrade option from
# the dashboard's perspective.
TIER_PRICES = {
    1: 30,
    2: 75,
    3: 150,
    4: 799,
}

TIER_LABELS = {
    1: "Tier 1 — Self-serve",
    2: "Tier 2 — Surfaced",
    3: "Tier 3 — Concierge",
    4: "Tier 4 — Inventory",
}


# Display names + short tooltips for usage rows. The Billing view reads this
# list to know what to render — adding a new metric is just adding a row here
# plus seeding the UsageMetric row in seed.py.
USAGE_METRIC_DEFS = [
    {"key": "posts_published",        "label": "Posts published",          "unit": "posts",          "tooltip": "Across all platforms this month"},
    {"key": "chatbot_conversations",  "label": "Chatbot conversations",    "unit": "conversations", "tooltip": "Consumer sessions handled by your chatbot"},
    {"key": "ads_run",                "label": "Active ad campaigns",      "unit": "campaigns",      "tooltip": "Distinct campaigns active this month"},
    {"key": "agent_token_cents",      "label": "Agent compute (billed)",   "unit": "cents",          "tooltip": "AI Agent token spend, billed back at cost"},
]


def _current_month_year() -> str:
    return datetime.utcnow().strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------

class TierChangeBody(BaseModel):
    to_tier: int = Field(ge=1, le=4)
    note: Optional[str] = Field(default=None, max_length=400)


# ---------------------------------------------------------------------------
# Payload shapers
# ---------------------------------------------------------------------------

def usage_payload(business_id: int, db: Session) -> dict[str, Any]:
    month = _current_month_year()
    rows = (
        db.query(UsageMetric)
        .filter(UsageMetric.business_id == business_id, UsageMetric.month_year == month)
        .all()
    )
    by_key = {r.metric_key: r.value for r in rows}
    metrics = []
    for d in USAGE_METRIC_DEFS:
        metrics.append({
            **d,
            "value": by_key.get(d["key"], 0),
        })
    return {
        "monthYear": month,
        "metrics":   metrics,
    }


def invoice_payload(inv: BillingInvoice) -> dict[str, Any]:
    return {
        "id":           inv.id,
        "periodLabel":  inv.period_label,
        "periodStart":  inv.period_start,
        "periodEnd":    inv.period_end,
        "amountCents":  inv.amount_cents,
        "status":       inv.status,
        "tierAtPeriod": inv.tier_at_period,
        "lineItems":    inv.line_items_json or [],
        "issuedAt":     inv.issued_at.isoformat() if inv.issued_at else None,
        "external":     inv.external_invoice_id,
    }


def tier_change_payload(req: TierChangeRequest) -> dict[str, Any]:
    return {
        "id":         req.id,
        "fromTier":   req.from_tier,
        "toTier":     req.to_tier,
        "note":       req.note,
        "status":     req.status,
        "createdAt":  req.created_at.isoformat() if req.created_at else None,
        "handledAt":  req.handled_at.isoformat() if req.handled_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/billing")
def get_billing(business_id: int = 1, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Full slice for Settings → Billing sub-tab."""
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")

    invoices = (
        db.query(BillingInvoice)
        .filter(BillingInvoice.business_id == business_id)
        .order_by(BillingInvoice.period_start.desc(), BillingInvoice.id.desc())
        .limit(24)
        .all()
    )
    pending = (
        db.query(TierChangeRequest)
        .filter(
            TierChangeRequest.business_id == business_id,
            TierChangeRequest.status == "pending",
        )
        .order_by(TierChangeRequest.created_at.desc())
        .first()
    )

    paid_to_date_cents = sum(i.amount_cents for i in invoices if i.status == "paid")

    return {
        "currentTier":     biz.tier,
        "currentTierLabel": biz.tier_label or TIER_LABELS.get(biz.tier, f"Tier {biz.tier}"),
        "monthlyPrice":    biz.monthly_price,
        "monthlyPriceCents": biz.monthly_price * 100,
        "tierOptions": [
            {
                "tier":       t,
                "label":      TIER_LABELS.get(t, f"Tier {t}"),
                "monthlyPrice": p,
                "isCurrent":  t == biz.tier,
                "isUpgrade":  t > biz.tier,
            }
            for t, p in TIER_PRICES.items()
        ],
        "usage":           usage_payload(business_id, db),
        "invoices":        [invoice_payload(i) for i in invoices],
        "paidToDateCents": paid_to_date_cents,
        "pendingTierChange": tier_change_payload(pending) if pending else None,
        "stripeEnabled":   False,  # mirrors BILLING_ENABLED=false in prod
    }


@router.get("/billing/usage")
def get_usage(business_id: int = 1, db: Session = Depends(get_db)) -> dict[str, Any]:
    return usage_payload(business_id, db)


@router.get("/billing/invoices")
def list_invoices(
    business_id: int = 1,
    limit: int = 24,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = (
        db.query(BillingInvoice)
        .filter(BillingInvoice.business_id == business_id)
        .order_by(BillingInvoice.period_start.desc(), BillingInvoice.id.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [invoice_payload(r) for r in rows]


@router.post("/billing/change-tier-request")
def request_tier_change(
    body: TierChangeBody,
    business_id: int = 1,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Records a tier-change request. NO CHARGE happens — Stripe deferred.

    When Stripe lands, this endpoint becomes the gate that creates a
    stripe.Subscription.modify call. For now it just records intent so the
    sales team has a queue of upgrade-interest signals to chase.
    """
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")
    if body.to_tier == biz.tier:
        raise HTTPException(status_code=400, detail=f"already on tier {biz.tier}")

    # If a pending request already exists, supersede it (latest wins).
    existing = (
        db.query(TierChangeRequest)
        .filter(
            TierChangeRequest.business_id == business_id,
            TierChangeRequest.status == "pending",
        )
        .all()
    )
    for r in existing:
        r.status = "cancelled"
        r.handled_at = datetime.utcnow()

    req = TierChangeRequest(
        business_id=business_id,
        from_tier=biz.tier,
        to_tier=body.to_tier,
        note=body.note,
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return tier_change_payload(req)
