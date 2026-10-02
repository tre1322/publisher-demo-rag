"""Creating a business, and telling demo accounts from real clients (Phase 1).

`create_business` is the one way a new client gets set up. It writes every
row the dashboard reads, all of them honestly empty: no posts, reviews,
insights, connected accounts, or sample conversations, and nothing copied
from Quadd. The admin console calls it; so do the smokes.

`is_demo` / `require_demo` gate the sales-demo affordances (the ad-spend
simulator, canned insights, sample imports). Quadd is the demo account;
every business created here is real unless the caller says otherwise.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import (
    Business,
    DashboardNotices,
    MarketingPlan,
    PerformanceSummary,
    ReviewAggregate,
    SettingsRow,
)
from .routers.billing import TIER_LABELS, TIER_PRICES
from .seed import (
    _seed_ad_connections,
    _seed_ad_platform_budgets,
    _seed_billing_usage,
    _seed_reach_tiers,
)

DEMO_ONLY_DETAIL = (
    "That's a demo-account feature. Real accounts only show real activity, "
    "so it's switched off here."
)

DEFAULT_NOTIFICATIONS = [
    {"key": "neg_review",     "label": "New negative review (2★ or below)", "on": True, "via": "Email"},
    {"key": "post_scheduled", "label": "Posts waiting for your approval",   "on": True, "via": "Email"},
    {"key": "ad_pacing",      "label": "Ad spend pacing alerts",            "on": True, "via": "Email"},
    {"key": "weekly_digest",  "label": "Weekly summary",                    "on": True, "via": "Email · Mondays"},
]


def is_demo(biz: Optional[Business]) -> bool:
    return bool(biz is not None and biz.is_demo)


def require_demo(db: Session, business_id: int) -> Business:
    """409 unless the business is a demo account."""
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")
    if not is_demo(biz):
        raise HTTPException(status_code=409, detail=DEMO_ONLY_DETAIL)
    return biz


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:48] or "business"


def _unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while db.query(Business).filter(Business.slug == slug).first() is not None:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _initials(owner: str) -> str:
    parts = [p for p in re.split(r"\s+", owner.strip()) if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def create_business(
    db: Session,
    *,
    name: str,
    owner: str,
    location: str = "",
    publisher: str = "",
    phone: str = "",
    tier: int = 2,
    website: Optional[str] = None,
    demo: bool = False,
) -> Business:
    """Create a business with a clean first day. Flushes; caller commits."""
    if tier not in TIER_PRICES:
        raise ValueError(f"tier must be one of {sorted(TIER_PRICES)}")
    now = datetime.utcnow().replace(microsecond=0)
    biz = Business(
        slug=_unique_slug(db, name),
        name=name.strip(),
        owner=owner.strip(),
        owner_initials=_initials(owner),
        location=location.strip(),
        publisher=publisher.strip(),
        phone=phone.strip(),
        tier=tier,
        tier_label=TIER_LABELS[tier],
        monthly_price=TIER_PRICES[tier],
        joined_days_ago=0,
        joined_date="Today",
        enrolled_at=now,
        voice_interview="pending",
        ase_certified=False,
        is_demo=demo,
        website=(website or "").strip() or None,
    )
    db.add(biz)
    db.flush()

    db.add(SettingsRow(
        business_id=biz.id,
        cadence="weekly",
        notifications_json=[dict(n) for n in DEFAULT_NOTIFICATIONS],
        ad_autonomy_enabled=False,
    ))
    db.add(MarketingPlan(
        business_id=biz.id,
        audience="",
        value_prop="",
        switching_json={"pulls": [], "pushes": []},
        customer_language_json=[],
        proof_points_json=[],
        channels_json=[],
        q3_goals_json=[],
        updated_at=now,
    ))
    db.add(PerformanceSummary(
        business_id=biz.id,
        reach_value=0, reach_prev=0, reach_delta="—",
        engagement_value=0, engagement_prev=0, engagement_delta="—",
        followers_value="—", followers_prev="—", followers_delta="—",
        ctr_value="—", ctr_prev="—", ctr_delta="—",
        channel_mix_json=[],
        top_posts_json=[],
        insights_json=[],
        daily_reach_current_json=[0] * 30,
        daily_reach_prev_json=[0] * 30,
    ))
    db.add(ReviewAggregate(
        business_id=biz.id, aggregate=0.0, total=0, sparkline_json=[], sparkline_labels_json=[],
    ))
    db.add(DashboardNotices(
        business_id=biz.id,
        attention_json=[
            {"kind": "gap", "title": "Your voice interview is next",
             "detail": "Amplafai will set up a short interview so your AI agent writes the way you talk. "
                       "Until then, drafts lean on your marketing plan.",
             "cta": "Open marketing plan", "icon": "sparkles", "tone": "teal", "target": "plan"},
            {"kind": "pending", "title": "Your first drafts will land in Approvals",
             "detail": "Nothing is posted without your sign-off. Ask the AI agent for a first post any time.",
             "cta": "Open AI agent", "icon": "inbox", "tone": "amber", "target": "chat"},
        ],
        week_recap_json=[{"when_iso": now.isoformat(), "text": f"{biz.name} joined Amplafai"}],
        # No overrides: the Home tiles are computed from real data.
        stats_overrides_json=None,
    ))
    _seed_reach_tiers(db, business_id=biz.id, publisher=biz.publisher or None, location=biz.location or None)
    _seed_ad_platform_budgets(db, business_id=biz.id)
    _seed_ad_connections(db, business_id=biz.id)
    _seed_billing_usage(db, business_id=biz.id, tier=tier)
    db.flush()
    return biz
