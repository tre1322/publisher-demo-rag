"""GET /api/bootstrap — returns everything the dashboard renders on first paint.

Shape matches the constants formerly hardcoded in dashboard.html (business,
stats, attention, weekRecap, posts, approvals, performance, reviews,
marketingPlan, chat, settings). When dashboard.html switches to fetch this
endpoint, the visual output should be byte-identical (modulo JSON ordering).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Approval,
    Business,
    ChatTurn,
    Connection,
    DashboardNotices,
    MarketingPlan,
    PerformanceSummary,
    Post,
    Review,
    ReviewAggregate,
    SettingsRow,
)

router = APIRouter()


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
        "platform": r.platform,
        "stars": r.stars,
        "when": r.when_label,
        "author": r.author,
        "body": r.body,
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
        "platform": r.platform,
        "stars": r.stars,
        "when": r.when_label,
        "author": r.author,
        "body": r.body,
        "response": r.ai_draft_response or r.owner_response,
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


def _stats_payload(notices: DashboardNotices | None, agg: ReviewAggregate | None) -> dict[str, Any]:
    """Compose the Home-view stat tiles. Phase A: use seeded overrides verbatim."""
    overrides = (notices.stats_overrides_json if notices else None) or {}
    reviews_stat = overrides.get("reviews") or {
        "value": 7,
        "rating": agg.aggregate if agg else 4.8,
        "helper": f"{agg.aggregate if agg else 4.8} ★ avg",
    }
    return {
        "posts":      overrides.get("posts",      {"value": 12, "prev": 8, "delta": "+4", "helper": "across FB, IG, GBP"}),
        "engagement": overrides.get("engagement", {"value": "+28%", "helper": "vs prior 30 days", "positive": True}),
        "reviews":    reviews_stat,
        "spend":      overrides.get("spend",      {"used": 112, "budget": 150, "helper": "$200 / mo recommended"}),
        "chatbot":    overrides.get("chatbot",    {"value": 34, "helper": "Tier 3 preview"}),
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

    payload: dict[str, Any] = {
        "business": _business_payload(biz),
        "stats": _stats_payload(notices, agg),
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
    }
    return payload
