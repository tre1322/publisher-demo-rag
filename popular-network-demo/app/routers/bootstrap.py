"""GET /api/bootstrap — returns everything the dashboard renders on first paint.

Shape matches the constants formerly hardcoded in dashboard.html (business,
stats, attention, weekRecap, posts, approvals, performance, reviews,
marketingPlan, chat, settings, voiceBrief). When dashboard.html switches to
fetch this endpoint, the visual output should be byte-identical (modulo JSON
ordering).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AdCampaign,
    AdConnection,
    AdImpressionsByTerritory,
    AdPlatformBudget,
    Approval,
    BillingInvoice,
    Business,
    ChatTurn,
    ChatbotConversation,
    Connection,
    DashboardNotices,
    InventoryFeed,
    InventoryListing,
    LocalFirstLog,
    MarketingPlan,
    PerformanceSummary,
    Post,
    ReachTier,
    Review,
    ReviewAggregate,
    SettingsRow,
    UsageMetric,
)

router = APIRouter()

# Where synthesized voice briefs live, keyed by business slug.
# (Mirrors app/agent/system_prompt._VOICE_BRIEF_DIR — kept independent so a
# refactor of one doesn't accidentally break the other.)
_VOICE_BRIEF_DIR = Path(__file__).resolve().parent.parent.parent / "voice-briefs"


def _load_voice_brief(slug: str | None) -> Optional[dict[str, Any]]:
    """Return the parsed voice-brief dict for this business slug, or None.

    The brief is the artifact of the W2.1 PMC pipeline (or local fallback via
    scripts/synthesize_voice_brief.py). Dashboard surfaces it in the AI Agent
    side rail ('What the agent knows') and could later show it in Marketing
    Plan as a structured panel.
    """
    if not slug:
        return None
    path = _VOICE_BRIEF_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _business_payload(biz: Business) -> dict[str, Any]:
    return {
        "name": biz.name,
        "owner": biz.owner,
        "ownerInitials": biz.owner_initials,
        "location": biz.location,
        "publisher": biz.publisher,
        "phone": biz.phone,
        "tier": biz.tier,
        "tierLabel": biz.tier_label,
        "monthlyPrice": biz.monthly_price,
        "joinedDaysAgo": biz.joined_days_ago,
        "joinedDate": biz.joined_date,
        "voiceInterview": biz.voice_interview,
        "techName": biz.tech_name,
        "yearsInTown": biz.years_in_town,
        "aseCertified": biz.ase_certified,
    }


def _post_payload(p: Post) -> dict[str, Any]:
    return {
        "id": p.external_id or f"p{p.id}",
        "internalId": p.id,
        "date": p.date,
        "platform": p.platform,
        "status": p.status,
        "title": p.title,
        "draft": p.draft,
        "reasoning": p.reasoning,
    }


def _approval_payload(a: Approval) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": a.external_id or f"a{a.id}",
        "internalId": a.id,
        "platform": a.platform,
        "title": a.title,
        "draft": a.draft,
        "note": a.note,
    }
    if a.kind == "review":
        payload["kind"] = "review"
        payload["original"] = a.original_review_text
    return payload


def _review_payload(r: Review) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": r.external_id or f"r{r.id}",
        "internalId": r.id,
        "platform": r.platform,
        "stars": r.stars,
        "when": r.when_label,
        "author": r.author,
        "body": r.body,
        "responseStatus": r.response_status,
    }
    if r.flagged:
        payload["response"] = "(see flagged response above)"
        payload["flagged"] = True
    else:
        payload["response"] = r.owner_response
    return payload


def _pinned_review_payload(r: Review) -> dict[str, Any]:
    return {
        "id": r.external_id or f"r{r.id}",
        "internalId": r.id,
        "platform": r.platform,
        "stars": r.stars,
        "when": r.when_label,
        "author": r.author,
        "body": r.body,
        "response": r.owner_response or r.ai_draft_response,
        "responseStatus": r.response_status,
        "note": r.response_note,
    }


def _performance_payload(perf: PerformanceSummary) -> dict[str, Any]:
    return {
        "reach":      {"value": perf.reach_value,      "prev": perf.reach_prev,      "delta": perf.reach_delta,      "positive": True},
        "engagement": {"value": perf.engagement_value, "prev": perf.engagement_prev, "delta": perf.engagement_delta, "positive": True},
        "followers":  {"value": perf.followers_value,  "prev": perf.followers_prev,  "delta": perf.followers_delta,  "positive": True, "helper": "net new this period"},
        "ctr":        {"value": perf.ctr_value,        "prev": perf.ctr_prev,        "delta": perf.ctr_delta,        "positive": True, "helper": "paid social"},
        "channelMix": perf.channel_mix_json,
        "topPosts":   perf.top_posts_json,
        "insights":   perf.insights_json,
        "dailyReachCurrent": perf.daily_reach_current_json,
        "dailyReachPrev":    perf.daily_reach_prev_json,
    }


def _marketing_plan_payload(mp: MarketingPlan) -> dict[str, Any]:
    return {
        "audience": mp.audience,
        "valueProp": mp.value_prop,
        "switching": mp.switching_json,
        "customerLanguage": mp.customer_language_json,
        "proofPoints": mp.proof_points_json,
        "channels": mp.channels_json,
        "q3Goals": mp.q3_goals_json,
    }


def _chat_payload(turn: ChatTurn) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "who": turn.who,
        "when": turn.when_label,
        "text": turn.text,
    }
    if turn.attachment_json:
        payload["attachment"] = turn.attachment_json
    return payload


def _stats_payload(
    notices: DashboardNotices | None,
    agg: ReviewAggregate | None,
    *,
    tier: int = 0,
    chatbot_count: int = 0,
) -> dict[str, Any]:
    """Compose the Home-view stat tiles. Phase A: use seeded overrides verbatim.

    Phase F polish: the chatbot tile is computed from the live ChatbotConversation
    count (not a seeded override), so the Home tile and Sidebar badge agree after
    fixture import. Below Tier 3 the tile stays as a "Tier 3+ preview" upsell.
    """
    overrides = (notices.stats_overrides_json if notices else None) or {}
    reviews_stat = overrides.get("reviews") or {
        "value": 7,
        "rating": agg.aggregate if agg else 4.8,
        "helper": f"{agg.aggregate if agg else 4.8} ★ avg",
    }
    if tier < 3:
        chatbot_stat = {"value": 0, "helper": "Tier 3+ preview"}
    elif chatbot_count == 0:
        chatbot_stat = {"value": 0, "helper": "not yet configured"}
    else:
        chatbot_stat = {"value": chatbot_count, "helper": "served this month"}
    return {
        "posts":      overrides.get("posts",      {"value": 12, "prev": 8, "delta": "+4", "helper": "across FB, IG, GBP"}),
        "engagement": overrides.get("engagement", {"value": "+28%", "helper": "vs prior 30 days", "positive": True}),
        "reviews":    reviews_stat,
        "spend":      overrides.get("spend",      {"used": 112, "budget": 150, "helper": "$200 / mo recommended"}),
        "chatbot":    chatbot_stat,
    }


@router.get("/bootstrap")
def get_bootstrap(business_id: int = 1, db: Session = Depends(get_db)) -> dict[str, Any]:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(status_code=404, detail=f"business {business_id} not found")

    posts = (
        db.query(Post)
        .filter(Post.business_id == business_id)
        .order_by(Post.date.asc(), Post.id.asc())
        .all()
    )
    approvals = (
        db.query(Approval)
        .filter(Approval.business_id == business_id, Approval.decision.is_(None))
        .order_by(Approval.id.asc())
        .all()
    )
    reviews_rows = (
        db.query(Review)
        .filter(Review.business_id == business_id)
        .order_by(Review.is_pinned.desc(), Review.id.asc())
        .all()
    )
    pinned = next((r for r in reviews_rows if r.is_pinned), None)

    agg = db.get(ReviewAggregate, business_id)
    perf = db.get(PerformanceSummary, business_id)
    plan = db.get(MarketingPlan, business_id)
    notices = db.get(DashboardNotices, business_id)
    settings_row = db.get(SettingsRow, business_id)
    connections = (
        db.query(Connection)
        .filter(Connection.business_id == business_id)
        .order_by(Connection.id.asc())
        .all()
    )
    chat_turns = (
        db.query(ChatTurn)
        .filter(ChatTurn.business_id == business_id, ChatTurn.conversation_id == "seed")
        .order_by(ChatTurn.id.asc())
        .all()
    )

    # Phase D: reach tiers + territory revenue + local-first telemetry.
    # Tiers are always seeded (config); the two telemetry slices are empty
    # for a Day-1 customer and populate as the chatbot serves real ads.
    reach_tiers = (
        db.query(ReachTier)
        .filter(ReachTier.business_id == business_id)
        .order_by(ReachTier.multiplier_pct.asc())
        .all()
    )
    impressions_rows = (
        db.query(AdImpressionsByTerritory)
        .filter(AdImpressionsByTerritory.business_id == business_id)
        .all()
    )
    local_first_rows = (
        db.query(LocalFirstLog)
        .filter(LocalFirstLog.business_id == business_id)
        .order_by(LocalFirstLog.log_date.asc())
        .all()
    )

    # Phase E: slim aggregate slice for Home tile + other contexts. The
    # AdsView itself fetches /api/ads for the full per-row payload — no
    # point bloating bootstrap with every campaign + budget row.
    from datetime import datetime as _dt
    _ads_month = _dt.utcnow().strftime("%Y-%m")
    ad_budgets = (
        db.query(AdPlatformBudget)
        .filter(AdPlatformBudget.business_id == business_id, AdPlatformBudget.month_year == _ads_month)
        .all()
    )
    ad_connections_rows = (
        db.query(AdConnection)
        .filter(AdConnection.business_id == business_id)
        .all()
    )
    ad_active_campaigns = (
        db.query(AdCampaign)
        .filter(
            AdCampaign.business_id == business_id,
            AdCampaign.status.in_(("active", "scheduled")),
        )
        .count()
    )
    ad_pending_campaigns = (
        db.query(AdCampaign)
        .filter(AdCampaign.business_id == business_id, AdCampaign.status == "pending_approval")
        .count()
    )

    # Phase F.1: inventory slim slice (counts only — full data via /api/inventory).
    inv_feed_count    = db.query(InventoryFeed).filter(InventoryFeed.business_id == business_id).count()
    inv_listing_count = db.query(InventoryListing).filter(InventoryListing.business_id == business_id).count()
    inv_stale_count   = (
        db.query(InventoryListing)
        .filter(InventoryListing.business_id == business_id, InventoryListing.is_stale.is_(True))
        .count()
    )

    # Phase F.2: billing slim slice (current month usage totals + tier).
    billing_invoice_count = db.query(BillingInvoice).filter(BillingInvoice.business_id == business_id).count()
    usage_rows = (
        db.query(UsageMetric)
        .filter(UsageMetric.business_id == business_id, UsageMetric.month_year == _ads_month)
        .all()
    )
    usage_by_key = {r.metric_key: r.value for r in usage_rows}

    # Phase F.3: chatbot slim slice (count + escalation count).
    chatbot_total_count    = db.query(ChatbotConversation).filter(ChatbotConversation.business_id == business_id).count()
    chatbot_escalation_ct  = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.business_id == business_id,
            ChatbotConversation.escalation_flag.is_(True),
        )
        .count()
    )

    payload: dict[str, Any] = {
        "business": _business_payload(biz),
        "stats": _stats_payload(notices, agg, tier=biz.tier, chatbot_count=chatbot_total_count),
        "attention": notices.attention_json if notices else [],
        "weekRecap": notices.week_recap_json if notices else [],
        "posts": [_post_payload(p) for p in posts],
        "approvals": [_approval_payload(a) for a in approvals],
        "performance": _performance_payload(perf) if perf else {},
        "reviews": {
            "aggregate": agg.aggregate if agg else 0.0,
            "total": agg.total if agg else 0,
            "sparkline": agg.sparkline_json if agg else [],
            "sparklineLabels": agg.sparkline_labels_json if agg else [],
            "pinned": _pinned_review_payload(pinned) if pinned else None,
            "recent": [_review_payload(r) for r in reviews_rows],
        },
        "marketingPlan": _marketing_plan_payload(plan) if plan else {},
        "chat": [_chat_payload(t) for t in chat_turns],
        "settings": {
            "cadence": settings_row.cadence if settings_row else "weekly",
            "notifications": (settings_row.notifications_json if settings_row else None) or [],
            "connections": [
                {
                    "platform": c.platform,
                    "account": c.account_label,
                    "status": c.status,
                    "last": c.last_verified_text,
                }
                for c in connections
            ],
        },
        # Voice brief: synthesized artifact from the owner's interview. The
        # AI Agent already injects it into its system prompt; this surface
        # makes it visible to the dashboard's UI (e.g. "What the agent knows"
        # panel). None when no brief exists for this business yet.
        "voiceBrief": _load_voice_brief(biz.slug),
        # Phase D — Display Ad Amplification surfaces.
        "reach": _reach_payload(reach_tiers, impressions_rows, local_first_rows),
        # Phase E — paid-ad spend management aggregate slice. Full per-row
        # data lives at GET /api/ads (called by AdsView on mount).
        "ads": _ads_payload(ad_budgets, ad_connections_rows, ad_active_campaigns, ad_pending_campaigns, _ads_month),
        # Phase F.1 — inventory slim slice. Full data via /api/inventory.
        "inventory": {
            "tierEligible":   biz.tier >= 4,
            "feedCount":      inv_feed_count,
            "listingCount":   inv_listing_count,
            "staleCount":     inv_stale_count,
            "hasAnyFeed":     inv_feed_count > 0,
        },
        # Phase F.2 — billing slim slice.
        "billing": {
            "tier":              biz.tier,
            "tierLabel":         biz.tier_label,
            "monthlyPrice":      biz.monthly_price,
            "invoiceCount":      billing_invoice_count,
            "currentUsage":      usage_by_key,
            "stripeEnabled":     False,
        },
        # Phase F.3 — chatbot slim slice.
        "chatbot": {
            "tierEligible":      biz.tier >= 3,
            "conversationCount": chatbot_total_count,
            "escalationCount":   chatbot_escalation_ct,
        },
    }
    return payload


# 65 / 25 / 10 split when an ad sold by Publisher A surfaces in Publisher B's
# chatbot territory. Source: amplora_business_plan.md §7.2.
_REVENUE_SPLIT = {"selling": 0.65, "receiving": 0.25, "platform": 0.10}


def _ads_payload(
    budgets: list[AdPlatformBudget],
    connections: list[AdConnection],
    active_count: int,
    pending_count: int,
    month_year: str,
) -> dict[str, Any]:
    """Slim aggregate slice for Home tile + other contexts.

    The AdsView itself calls /api/ads for the full per-row payload. This
    slice exists so Home / Compose / Performance can ask "are we paid-ads
    enabled at all?" without an extra fetch.
    """
    total_cap     = sum(b.monthly_cap_cents for b in budgets)
    total_spend   = sum(b.spend_cents for b in budgets)
    has_any_conn  = any(c.status == "connected" for c in connections)
    return {
        "monthYear":         month_year,
        "totalCapCents":     total_cap,
        "totalSpendCents":   total_spend,
        "activeCount":       active_count,
        "pendingCount":      pending_count,
        "connectionStatus":  {c.platform: c.status for c in connections},
        "hasAnyCap":         total_cap > 0,
        "hasAnyConnection":  has_any_conn,
    }


def _reach_payload(
    tiers: list[ReachTier],
    impressions: list[AdImpressionsByTerritory],
    local_first: list[LocalFirstLog],
) -> dict[str, Any]:
    # ---- tier ladder (always present, sourced from seed) ----
    tier_payload = [
        {
            "key": t.tier_key,
            "label": t.label,
            "multiplierPct": t.multiplier_pct,
            "radiusMiles": t.radius_miles,
            "territories": t.territories_json,
            "description": t.description,
        }
        for t in tiers
    ]

    # ---- territory revenue (Day-1 honest: empty when no served ads yet) ----
    by_territory: dict[str, dict[str, Any]] = {}
    total_impressions = 0
    total_gross_cents = 0
    for row in impressions:
        bucket = by_territory.setdefault(
            row.territory_code,
            {"code": row.territory_code, "label": row.territory_label, "impressions": 0, "grossCents": 0},
        )
        bucket["impressions"] += row.impressions
        bucket["grossCents"] += row.revenue_cents
        total_impressions += row.impressions
        total_gross_cents += row.revenue_cents

    by_territory_list = sorted(by_territory.values(), key=lambda b: b["impressions"], reverse=True)
    revenue_split = {
        "sellingCents":   int(round(total_gross_cents * _REVENUE_SPLIT["selling"])),
        "receivingCents": int(round(total_gross_cents * _REVENUE_SPLIT["receiving"])),
        "platformCents":  int(round(total_gross_cents * _REVENUE_SPLIT["platform"])),
        "split":          _REVENUE_SPLIT,
    }

    # ---- local-first telemetry (Day-1 honest: empty when no queries yet) ----
    queries_total = sum(r.queries_total for r in local_first)
    queries_local_first = sum(r.queries_local_first for r in local_first)
    queries_oot_paid = sum(r.queries_out_of_territory_paid for r in local_first)
    local_first_pct = (queries_local_first / queries_total) if queries_total else None

    return {
        "tiers": tier_payload,
        "territoryRevenue": {
            "totalImpressions": total_impressions,
            "totalGrossCents":  total_gross_cents,
            "byTerritory":      by_territory_list,
            "split":            revenue_split,
            "hasData":          total_impressions > 0,
        },
        "localFirst": {
            "queriesTotal":             queries_total,
            "queriesLocalFirst":        queries_local_first,
            "queriesOutOfTerritoryPaid": queries_oot_paid,
            "localFirstPct":            local_first_pct,
            "hasData":                  queries_total > 0,
        },
    }
