"""Tools the AI Agent can call (Phase C.3).

Anthropic-format tool definitions + Python executors. Each tool returns a
ToolResult — `text` is fed back to Claude as the tool_result block; `attachment`
is persisted on the agent ChatTurn so the dashboard renders an inline card.

Loop shape: multi-step. The chat router passes these tools to messages.create
and continues the conversation until stop_reason == "end_turn" or
max_tool_iterations is hit. The safety net is the data model — draft_post and
draft_review_response create rows in `posts` / `approvals` with status="draft",
propose_boost writes to a new `boost_proposals` row. Nothing publishes; the
owner approves from the dashboard.

The `description` strings below are what Claude sees alongside the schema —
they're the trigger conditions. Two are marked TODO and need owner-voice input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..models import (
    AdCampaign,
    AdConnection,
    AdPlatformBudget,
    Approval,
    Business,
    MarketingPlan,
    Post,
    Review,
)

# Cap the loop in chat.py so a chatty model can't fan out indefinitely.
MAX_TOOL_ITERATIONS = 4


@dataclass
class ToolResult:
    """What a tool executor returns.

    `text` — short string fed back to Claude as the tool_result.content. Keep
             it tight; long results balloon the next model call.
    `attachment` — dict the frontend renders as a card under the agent turn.
                   Must include a `kind` discriminator the ChatView switches on.
    `is_error` — set True if the tool couldn't do its job; Claude will see this
                 as a tool_result with is_error and can apologize/retry.
    """

    text: str
    attachment: dict[str, Any]
    is_error: bool = False


# --------------------------------------------------------------------------- #
# Schemas — what Claude sees when deciding which tool (if any) to call.
# --------------------------------------------------------------------------- #

_PLATFORM_ENUM = ["facebook", "instagram", "google", "tiktok", "linkedin", "x"]


# Owner-set policy (Trevor, 2026-05-26 v2): widened trigger after live test.
# v1 was too cautious — Sonnet 4.6 read "Draft me a LinkedIn post" as "show me
# a draft" rather than firing the tool. v2 makes the trigger explicit and
# reassures the model the side effect is low-cost (drafts don't publish).
_DRAFT_POST_DESCRIPTION = (
    "Draft a social post and save it to the owner's Approvals queue as a "
    "draft. Fire this tool whenever the owner asks you for a draft, a post, "
    "or written copy on a specific topic — phrases like 'draft me a "
    "[platform] post about X,' 'write me one for X,' 'give me a draft about "
    "X,' or picking among options you suggested. This tool ONLY creates a "
    "draft and an Approval row — nothing publishes until the owner "
    "separately approves it in the dashboard. When in doubt about whether "
    "the owner has committed, fire the tool: the draft is reversible and "
    "the owner can edit or reject it. Do NOT fire on speculative phrasing "
    "where the owner is still exploring an idea ('we could post about…', "
    "'maybe we should mention…') — ask which angle first. Match the owner's "
    "voice as established in prior turns and the voice brief — not generic "
    "ad-copy register."
)

# Owner-set policy (Trevor, 2026-05-26): anchor-driven — only propose boosts
# when budget/reach is in the air or owner signals openness to spending.
_PROPOSE_BOOST_DESCRIPTION = (
    "Propose a paid boost for an existing approved or drafted post. Call "
    "this when one of three signals is present: (1) the owner brings up "
    "budget, spend, reach, or 'boost' explicitly; (2) you've just "
    "identified a clearly winning post via regenerate_insights AND the "
    "owner has indicated openness to spending in prior turns; (3) the "
    "owner asks 'what should I boost?' or similar. Do NOT propose boosts "
    "unprompted just because a post performed well — the owner has a "
    "budget they've already set and may not be in 'spend more' mode. The "
    "proposal queues as a card; the owner accepts, edits, or declines in "
    "the dashboard."
)

_DRAFT_REVIEW_RESPONSE_DESCRIPTION = (
    "Draft a public response to a specific customer review by review_id. Use "
    "this when the owner asks for help responding to a review or when you're "
    "recommending a response to a flagged or pinned review. The draft is "
    "saved as an Approval (kind='review') and only sent after the owner "
    "approves it in the Reviews tab — do not promise it's been sent."
)

_REGENERATE_INSIGHTS_DESCRIPTION = (
    "Recompute the Performance tab insights (top channels, best posting "
    "windows, anomalies). Use this when the insights are clearly stale "
    "(>7 days old) or when the owner asks for fresh analysis. Don't call "
    "this on every turn — the underlying data only changes daily."
)

# Phase E.4 — Ad-spend tools. Tier 2 → these queue approvals; Tier 3 → they
# mutate state autonomously within owner-set caps. Tier logic enforced in
# code (see _check_tier and the branching in each executor), NOT in the
# prompt — same anti-prompt-yelling discipline as draft_post's regex
# trigger. The agent doesn't need to know about tiers.
_ALLOCATE_BUDGET_DESCRIPTION = (
    "Set or adjust the monthly spend cap for a specific ad platform "
    "(meta / google / tiktok / linkedin). Fire this when the owner says "
    "something like 'put $200 on Meta this month,' 'set my Google budget "
    "to $150,' 'I want to spend $300 on LinkedIn,' or 'cap TikTok at "
    "zero — pause it.' One platform per call. On Tier 3 plans the cap "
    "is set immediately; on Tier 2 it queues for the owner's approval."
)

_SCHEDULE_BOOST_DESCRIPTION = (
    "Schedule a paid boost campaign for an existing post on a specific "
    "ad platform. Distinct from propose_boost (which is just a "
    "lightweight estimate card): schedule_boost actually creates the "
    "campaign in the dashboard's Ads & Spend tab. Fire this when the "
    "owner says 'boost that post for $30/day on Facebook,' 'run that "
    "for a week on Google,' or after they've explicitly approved a "
    "propose_boost. On Tier 3 the campaign goes live; on Tier 2 it "
    "queues for explicit owner approval."
)

_PAUSE_CAMPAIGN_DESCRIPTION = (
    "Pause an active or scheduled paid-ad campaign by campaign_id. Use "
    "this when the owner says 'pause that campaign,' 'stop spending on "
    "X,' or you notice a campaign is significantly underperforming. "
    "Tier 3 pauses immediately; Tier 2 queues the pause request for "
    "owner approval. Restart is owner-only — agent never re-activates."
)


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "draft_post",
        "description": _DRAFT_POST_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": _PLATFORM_ENUM,
                    "description": "Which platform the post is for.",
                },
                "topic": {
                    "type": "string",
                    "description": (
                        "Short canonical title for the post (becomes the "
                        "Post.title — owner sees it in approvals queue)."
                    ),
                },
                "brief": {
                    "type": "string",
                    "description": (
                        "The full post body, written in the owner's voice. "
                        "Don't use generic ad-copy register — match the "
                        "voice brief and prior chat turns."
                    ),
                },
            },
            "required": ["platform", "topic", "brief"],
        },
    },
    {
        "name": "propose_boost",
        "description": _PROPOSE_BOOST_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "integer",
                    "description": (
                        "The Post.id to boost. Must already exist — either "
                        "an approved post the owner is happy with or one "
                        "you just drafted via draft_post."
                    ),
                },
                "daily_budget": {
                    "type": "number",
                    "description": "USD per day. Recommended floor: $5, ceiling: $50.",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to run the boost (1–14).",
                },
                "audience_hint": {
                    "type": "string",
                    "description": (
                        "One short sentence describing the target audience. "
                        "E.g. 'B2B newspaper publishers in the upper Midwest, "
                        "owners and editors, 35–65'."
                    ),
                },
            },
            "required": ["post_id", "daily_budget", "days", "audience_hint"],
        },
    },
    {
        "name": "draft_review_response",
        "description": _DRAFT_REVIEW_RESPONSE_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "review_id": {
                    "type": "integer",
                    "description": "The Review.id to respond to.",
                },
                "draft": {
                    "type": "string",
                    "description": (
                        "Your proposed public response. Match the owner's "
                        "voice — don't sound like a customer-service script."
                    ),
                },
            },
            "required": ["review_id", "draft"],
        },
    },
    {
        "name": "regenerate_insights",
        "description": _REGENERATE_INSIGHTS_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "allocate_platform_budget",
        "description": _ALLOCATE_BUDGET_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["meta", "google", "tiktok", "linkedin"],
                    "description": "Which ad platform. 'meta' covers FB+IG together.",
                },
                "monthly_cents": {
                    "type": "integer",
                    "description": "New cap in cents (e.g. 20000 = $200/month). Zero pauses the platform.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief sentence explaining why this cap, for the owner.",
                },
            },
            "required": ["platform", "monthly_cents", "reasoning"],
        },
    },
    {
        "name": "schedule_boost",
        "description": _SCHEDULE_BOOST_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {
                    "type": "integer",
                    "description": "Post.id to boost — must already exist as approved/draft.",
                },
                "platform": {
                    "type": "string",
                    "enum": ["meta", "google", "tiktok", "linkedin"],
                    "description": "Ad platform for the boost.",
                },
                "daily_budget_cents": {
                    "type": "integer",
                    "description": "USD/day in cents. Min 100 ($1). Recommended 1000-5000 ($10-50).",
                },
                "duration_days": {
                    "type": "integer",
                    "description": "Run length in days (1-90).",
                },
                "audience_hint": {
                    "type": "string",
                    "description": "Short description of target audience for the platform's targeting.",
                },
            },
            "required": ["post_id", "platform", "daily_budget_cents", "duration_days", "audience_hint"],
        },
    },
    {
        "name": "pause_campaign",
        "description": _PAUSE_CAMPAIGN_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {
                    "type": "integer",
                    "description": "AdCampaign.id to pause.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief sentence — why you're pausing, for the owner's record.",
                },
            },
            "required": ["campaign_id", "reason"],
        },
    },
]


# Phase E.4 — platform key translation. The agent uses friendly names
# ("meta" / "google"); the data model uses canonical keys ("fb_ig" /
# "google_ads"). One-way mapping at the tool boundary.
_AGENT_TO_AD_PLATFORM = {
    "meta":     "fb_ig",
    "google":   "google_ads",
    "tiktok":   "tiktok",
    "linkedin": "linkedin",
}


# --------------------------------------------------------------------------- #
# Executors — one per tool. Each takes (db, business_id, input_dict) and
# returns a ToolResult.
# --------------------------------------------------------------------------- #


def _exec_draft_post(db: Session, business_id: int, args: dict[str, Any]) -> ToolResult:
    platform = args["platform"]
    topic = args["topic"]
    brief = args["brief"]
    today = datetime.utcnow().date().isoformat()

    post = Post(
        business_id=business_id,
        date=today,
        platform=platform,
        status="draft",
        title=topic,
        draft=brief,
        reasoning="Drafted by AI Agent via chat (C.3 tool use).",
    )
    db.add(post)
    db.flush()  # populate post.id

    approval = Approval(
        business_id=business_id,
        post_id=post.id,
        kind="post",
        platform=platform,
        title=topic,
        draft=brief,
        note="Awaiting your review — drafted by AI Agent.",
    )
    db.add(approval)
    db.flush()

    return ToolResult(
        text=(
            f"Drafted post #{post.id} on {platform}: '{topic}'. "
            f"Saved as draft + queued for approval (#{approval.id}). "
            f"Not yet published — owner will approve in the Approvals queue."
        ),
        attachment={
            "kind": "draft-post-card",
            "post_id": post.id,
            "approval_id": approval.id,
            "platform": platform,
            "title": topic,
            "draft": brief,
        },
    )


def _exec_propose_boost(db: Session, business_id: int, args: dict[str, Any]) -> ToolResult:
    post_id = args["post_id"]
    daily_budget = float(args["daily_budget"])
    days = int(args["days"])
    audience_hint = args["audience_hint"]

    post = db.get(Post, post_id)
    if post is None or post.business_id != business_id:
        return ToolResult(
            text=f"No post with id={post_id} exists for this business.",
            attachment={"kind": "tool-error", "tool": "propose_boost", "reason": "post_not_found"},
            is_error=True,
        )

    total_budget = daily_budget * days
    # Cheap reach estimate — 1000 impressions per $5/day, ±25%. Not load-bearing.
    est_reach = int(daily_budget * days * 200)
    est_clicks = int(est_reach * 0.012)

    return ToolResult(
        text=(
            f"Boost proposal queued for post #{post_id}: ${daily_budget:.0f}/day × {days} days "
            f"(${total_budget:.0f} total). Est. reach {est_reach:,} / clicks {est_clicks:,}. "
            f"Owner sees this as a card and can accept, edit, or decline."
        ),
        attachment={
            "kind": "boost-card",
            "post_id": post_id,
            "post": post.title,
            "budget": f"${total_budget:.0f} (${daily_budget:.0f}/day × {days})",
            "ages": "—",
            "radius": "—",
            "reach": f"~{est_reach:,}",
            "clicks": f"~{est_clicks:,}",
            "audience_hint": audience_hint,
        },
    )


def _exec_draft_review_response(
    db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    review_id = args["review_id"]
    draft = args["draft"]

    review = db.get(Review, review_id)
    if review is None or review.business_id != business_id:
        return ToolResult(
            text=f"No review with id={review_id} exists for this business.",
            attachment={
                "kind": "tool-error",
                "tool": "draft_review_response",
                "reason": "review_not_found",
            },
            is_error=True,
        )

    review.ai_draft_response = draft
    review.response_status = "draft"

    approval = Approval(
        business_id=business_id,
        review_id=review_id,
        kind="review",
        platform=review.platform,
        title=f"Response to {review.author}'s {review.stars}★ review",
        draft=draft,
        original_review_text=review.body,
        note="Awaiting your review — drafted by AI Agent.",
    )
    db.add(approval)
    db.flush()

    return ToolResult(
        text=(
            f"Drafted a response to review #{review_id} ({review.stars}★, {review.author}). "
            f"Saved as approval #{approval.id}. Not yet posted — owner reviews and approves "
            f"in the Reviews tab."
        ),
        attachment={
            "kind": "review-response-card",
            "review_id": review_id,
            "approval_id": approval.id,
            "platform": review.platform,
            "stars": review.stars,
            "author": review.author,
            "original": review.body,
            "draft": draft,
        },
    )


def _exec_regenerate_insights(
    db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    # Phase C.3: stub that touches the marketing-plan updated_at to signal a
    # refresh, returns a deterministic "did the work" payload. Real recompute
    # is wired in B.6 (insights endpoint) — we'll hook into that in a follow-up.
    plan = db.get(MarketingPlan, business_id)
    if plan is not None:
        plan.updated_at = datetime.utcnow()

    return ToolResult(
        text=(
            "Insights refresh requested. Performance tab will reflect the latest "
            "rollup on next load. (Phase C.3 stub — real recompute lands when this "
            "hook calls the B.6 insights endpoint.)"
        ),
        attachment={"kind": "insights-refreshed", "refreshed_at": datetime.utcnow().isoformat()},
    )


# --------------------------------------------------------------------------- #
# Phase E.4 — ad-spend tool executors with tier gating.
# --------------------------------------------------------------------------- #


def _business_is_tier3(db: Session, business_id: int) -> bool:
    """Tier 3 (Concierge) = autonomous spend allocation within owner caps.
    Tier 2 (everyone else) = proposal-only path through Approvals queue.
    """
    biz = db.get(Business, business_id)
    return biz is not None and (biz.tier or 0) >= 3


def _current_month_year() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _exec_allocate_platform_budget(
    db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    raw_platform = args["platform"]
    cents = int(args["monthly_cents"])
    reasoning = args["reasoning"]

    platform = _AGENT_TO_AD_PLATFORM.get(raw_platform)
    if platform is None:
        return ToolResult(
            text=f"Unknown ad platform '{raw_platform}'. Use meta / google / tiktok / linkedin.",
            attachment={"kind": "tool-error", "tool": "allocate_platform_budget", "reason": "bad_platform"},
            is_error=True,
        )
    if cents < 0:
        return ToolResult(
            text="monthly_cents must be ≥ 0. Zero pauses; use a positive cap to enable.",
            attachment={"kind": "tool-error", "tool": "allocate_platform_budget", "reason": "negative_cap"},
            is_error=True,
        )

    is_tier3 = _business_is_tier3(db, business_id)
    month = _current_month_year()
    dollars = cents / 100

    if is_tier3:
        # Mutate the budget row directly.
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
            row = AdPlatformBudget(
                business_id=business_id,
                platform=platform,
                month_year=month,
                monthly_cap_cents=cents,
                spend_cents=0,
                status="active",
            )
            db.add(row)
        else:
            row.monthly_cap_cents = cents
            row.updated_at = datetime.utcnow()
        return ToolResult(
            text=(
                f"Tier 3 autonomous allocation: set {raw_platform} monthly cap to "
                f"${dollars:,.0f} ({reasoning})."
            ),
            attachment={
                "kind": "allocate-budget-card",
                "platform": raw_platform,
                "platformKey": platform,
                "monthlyCents": cents,
                "monthlyDollars": f"${dollars:,.0f}",
                "reasoning": reasoning,
                "tierMode": "autonomous",
            },
        )

    # Tier 2 — queue a proposal-style Approval row.
    db.add(Approval(
        business_id=business_id,
        external_id=f"allocate-{platform}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        kind="boost",  # reuse boost kind so frontend renders consistently
        platform=platform,
        title=f"Allocate ${dollars:,.0f}/mo to {raw_platform}",
        draft=f"Set monthly cap to ${dollars:,.0f} on {raw_platform}. {reasoning}",
        note="Agent-proposed budget allocation. On Tier 3 this would have executed automatically.",
    ))
    return ToolResult(
        text=(
            f"Tier 2 proposal queued: allocate ${dollars:,.0f}/mo to {raw_platform}. "
            f"Owner sees it in Approvals. ({reasoning})"
        ),
        attachment={
            "kind": "allocate-budget-card",
            "platform": raw_platform,
            "platformKey": platform,
            "monthlyCents": cents,
            "monthlyDollars": f"${dollars:,.0f}",
            "reasoning": reasoning,
            "tierMode": "proposal",
        },
    )


def _exec_schedule_boost(
    db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    post_id = int(args["post_id"])
    raw_platform = args["platform"]
    daily_cents = int(args["daily_budget_cents"])
    days = int(args["duration_days"])
    audience = args["audience_hint"]

    platform = _AGENT_TO_AD_PLATFORM.get(raw_platform)
    if platform is None:
        return ToolResult(
            text=f"Unknown ad platform '{raw_platform}'.",
            attachment={"kind": "tool-error", "tool": "schedule_boost", "reason": "bad_platform"},
            is_error=True,
        )
    if daily_cents < 100:
        return ToolResult(
            text="daily_budget_cents must be ≥ 100 ($1).",
            attachment={"kind": "tool-error", "tool": "schedule_boost", "reason": "budget_too_small"},
            is_error=True,
        )
    if not (1 <= days <= 90):
        return ToolResult(
            text="duration_days must be 1–90.",
            attachment={"kind": "tool-error", "tool": "schedule_boost", "reason": "bad_duration"},
            is_error=True,
        )

    post = db.get(Post, post_id)
    if post is None or post.business_id != business_id:
        return ToolResult(
            text=f"No post #{post_id} for this business — call draft_post first or pick an existing one.",
            attachment={"kind": "tool-error", "tool": "schedule_boost", "reason": "post_not_found"},
            is_error=True,
        )

    is_tier3 = _business_is_tier3(db, business_id)
    planned_total = daily_cents * days
    daily_dollars = daily_cents / 100
    total_dollars = planned_total / 100

    # On Tier 3, also check that the cap has headroom. If exceeded, fall
    # back to Tier 2 behavior so we never auto-blow past the owner's cap.
    if is_tier3:
        month = _current_month_year()
        budget = (
            db.query(AdPlatformBudget)
            .filter(
                AdPlatformBudget.business_id == business_id,
                AdPlatformBudget.platform == platform,
                AdPlatformBudget.month_year == month,
            )
            .one_or_none()
        )
        if budget is not None and budget.monthly_cap_cents > 0:
            remaining = budget.monthly_cap_cents - budget.spend_cents
            if planned_total > remaining:
                # Soft-fall to proposal so the owner sees and decides.
                is_tier3 = False

    status = "scheduled" if is_tier3 else "pending_approval"
    origin = "agent_autonomous" if is_tier3 else "agent_proposed"
    approved_by = "agent" if is_tier3 else None
    external_id = None
    if is_tier3:
        from ..routers.ads import _mock_external_id
        external_id = _mock_external_id(platform)

    campaign = AdCampaign(
        business_id=business_id,
        post_id=post_id,
        platform=platform,
        name=f"Boost: {post.title}",
        daily_budget_cents=daily_cents,
        duration_days=days,
        planned_total_cents=planned_total,
        target_audience_json={"hint": audience},
        status=status,
        origin=origin,
        approved_by=approved_by,
        external_campaign_id=external_id,
        performance_json={"impressions": 0, "clicks": 0, "ctr": 0.0},
        scheduled_for=datetime.utcnow() if is_tier3 else None,
    )
    db.add(campaign)
    db.flush()

    if not is_tier3:
        db.add(Approval(
            business_id=business_id,
            external_id=f"boost-{campaign.id}",
            post_id=post_id,
            kind="boost",
            platform=platform,
            title=f"Boost: {post.title}",
            draft=(
                f"${daily_dollars:.0f}/day × {days} days "
                f"(${total_dollars:.0f} total) on {raw_platform}. Targeting: {audience}."
            ),
            note="Proposed by your AI Agent. Click Approve to schedule.",
        ))

    text = (
        f"Tier 3 autonomous: scheduled ${total_dollars:.0f} boost on {raw_platform} "
        f"(${daily_dollars:.0f}/day × {days} days), campaign #{campaign.id}."
        if is_tier3
        else f"Tier 2 proposal queued: ${total_dollars:.0f} boost on {raw_platform}. "
             f"Owner approves it in the Approvals queue."
    )

    return ToolResult(
        text=text,
        attachment={
            "kind": "schedule-boost-card",
            "campaignId": campaign.id,
            "postId": post_id,
            "postTitle": post.title,
            "platform": raw_platform,
            "platformKey": platform,
            "dailyCents": daily_cents,
            "dailyDollars": f"${daily_dollars:.0f}",
            "days": days,
            "totalCents": planned_total,
            "totalDollars": f"${total_dollars:.0f}",
            "audience": audience,
            "tierMode": "autonomous" if is_tier3 else "proposal",
            "status": status,
        },
    )


def _exec_pause_campaign(
    db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    campaign_id = int(args["campaign_id"])
    reason = args["reason"]

    campaign = db.get(AdCampaign, campaign_id)
    if campaign is None or campaign.business_id != business_id:
        return ToolResult(
            text=f"No campaign #{campaign_id} for this business.",
            attachment={"kind": "tool-error", "tool": "pause_campaign", "reason": "campaign_not_found"},
            is_error=True,
        )
    if campaign.status not in ("active", "scheduled"):
        return ToolResult(
            text=f"Campaign #{campaign_id} is {campaign.status}, can't pause.",
            attachment={"kind": "tool-error", "tool": "pause_campaign", "reason": "bad_status"},
            is_error=True,
        )

    is_tier3 = _business_is_tier3(db, business_id)
    if is_tier3:
        campaign.status = "paused"
        return ToolResult(
            text=f"Tier 3 autonomous: paused campaign #{campaign_id} ({reason}).",
            attachment={
                "kind": "pause-campaign-card",
                "campaignId": campaign_id,
                "campaignName": campaign.name,
                "platform": campaign.platform,
                "reason": reason,
                "tierMode": "autonomous",
            },
        )

    # Tier 2 — queue a proposal
    db.add(Approval(
        business_id=business_id,
        external_id=f"pause-{campaign_id}",
        kind="boost",
        platform=campaign.platform,
        title=f"Pause campaign: {campaign.name}",
        draft=f"Pause active campaign #{campaign_id} on {campaign.platform}. Reason: {reason}",
        note="Agent-proposed pause. Approve to halt the campaign.",
    ))
    return ToolResult(
        text=f"Tier 2 proposal queued: pause campaign #{campaign_id}. ({reason})",
        attachment={
            "kind": "pause-campaign-card",
            "campaignId": campaign_id,
            "campaignName": campaign.name,
            "platform": campaign.platform,
            "reason": reason,
            "tierMode": "proposal",
        },
    )


# --------------------------------------------------------------------------- #
# Dispatcher — chat.py calls this with the tool name + Claude's args.
# --------------------------------------------------------------------------- #


_EXECUTORS: dict[str, Callable[[Session, int, dict[str, Any]], ToolResult]] = {
    "draft_post": _exec_draft_post,
    "propose_boost": _exec_propose_boost,
    "draft_review_response": _exec_draft_review_response,
    "regenerate_insights": _exec_regenerate_insights,
    "allocate_platform_budget": _exec_allocate_platform_budget,
    "schedule_boost":           _exec_schedule_boost,
    "pause_campaign":           _exec_pause_campaign,
}


def execute_tool(
    name: str, db: Session, business_id: int, args: dict[str, Any]
) -> ToolResult:
    """Run the tool. Caller is responsible for db.commit() after the full loop."""
    fn = _EXECUTORS.get(name)
    if fn is None:
        return ToolResult(
            text=f"Unknown tool: {name}. Available tools: {list(_EXECUTORS)}.",
            attachment={"kind": "tool-error", "tool": name, "reason": "unknown_tool"},
            is_error=True,
        )
    return fn(db, business_id, args)
