"""System-prompt builder for the AI Agent (Phase C.1).

Pulls live state out of the DB and assembles a single string for the Claude
system block. Designed to be cache-friendly: the structural sections change
rarely within a session, so the entire system text is sent with
`cache_control: ephemeral` from the chat router.

Voice carries two ways:
  1. The seeded conversation (app/seed.py:323-340) is passed back to Claude as
     `messages` on every turn, so Claude continues the established register
     without explicit instructions.
  2. If a voice brief exists for the business (data/voice-brief/{slug}.json,
     produced by the Amplora PMC pipeline OR the local
     scripts/synthesize_voice_brief.py fallback), it's inlined into the system
     prompt with AMPLIFY/MAINTAIN/MUTE buckets + VOICE field — the W2.1 PMC v4
     shape. See _voice_brief_section().
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Business, MarketingPlan, PerformanceSummary, Post, Review

# Repo-relative dir where synthesized voice-brief JSON files live, one per
# business slug. Separate from data/voice-brief/ which holds gitignored raw
# materials (audio recordings, transcripts) — see scripts/synthesize_voice_brief.py.
_VOICE_BRIEF_DIR = Path(__file__).resolve().parent.parent.parent / "voice-briefs"


def build_system_prompt(db: Session, business_id: int) -> str:
    """Assemble the system prompt for a given business.

    Sections, in order:
      1. Role + Westbrook context (who you are, who you're talking to)
      2. Voice rules (Trevor's domain knowledge — see TODO)
      3. Live data snapshot (marketing plan, recent posts, insights, pinned review)
      4. Formatting + tool-use conventions
    """
    biz = db.get(Business, business_id)
    plan = db.get(MarketingPlan, business_id)
    perf = db.get(PerformanceSummary, business_id)
    recent_posts = (
        db.query(Post)
        .filter(Post.business_id == business_id)
        .order_by(Post.id.desc())
        .limit(8)
        .all()
    )
    pinned_review = (
        db.query(Review)
        .filter(Review.business_id == business_id, Review.is_pinned.is_(True))
        .first()
    )

    sections: list[str] = []

    # --- 1. Role + business context -----------------------------------------
    sections.append(_role_section(biz))

    # --- 2. Voice brief (placeholder until PMC interview pipeline lands) ----
    sections.append(_voice_brief_section(biz))

    # --- 3. Live data snapshot ----------------------------------------------
    sections.append(_marketing_plan_section(plan))
    sections.append(_recent_posts_section(recent_posts))
    sections.append(_performance_insights_section(perf))
    sections.append(_pinned_review_section(pinned_review))

    # --- 4. Behavior + formatting -------------------------------------------
    sections.append(_behavior_rules())

    return "\n\n".join(s for s in sections if s)


def _role_section(biz: Business | None) -> str:
    if biz is None:
        return "You are the AI Agent for a small business on the Popular Network."
    return (
        f"You are the AI Agent for **{biz.name}** in {biz.location}. "
        f"The owner is {biz.owner}. "
        f"You're a marketing partner who knows this shop, this town, and the data. "
        f"You're not a generic chatbot — you've spent time on the voice interview, "
        f"you've read 47 days of post performance, and you know Westbrook's seasonal "
        f"rhythms (lake traffic at Currents/Spirit Lake/the river, school calendar, "
        f"farm season, hunting season)."
    )


def _voice_brief_section(biz: Business | None) -> str:
    """Load and format the per-business voice brief, if one exists.

    Looks for data/voice-brief/{biz.slug}.json. Returns "" silently when no
    brief is present — Westbrook seed runs this path until a brief is added.

    Schema (matches the W2.1 PMC v4 shape):
      voice, amplify[], maintain[], mute[], audience, value_prop,
      customer_language[], proof_points[], constraints[], seasonal_patterns[],
      notes
    """
    if biz is None or not biz.slug:
        return ""
    path = _VOICE_BRIEF_DIR / f"{biz.slug}.json"
    if not path.exists():
        return ""
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    lines: list[str] = ["## Voice brief (from owner interview)"]

    voice = (brief.get("voice") or "").strip()
    if voice:
        lines.append(f"\n**Voice:** {voice}")

    def _bucket(key: str, header: str) -> None:
        items = brief.get(key) or []
        if not items:
            return
        lines.append(f"\n**{header}:**")
        for item in items:
            if isinstance(item, dict):
                label = (item.get("label") or "").strip()
                detail = (item.get("detail") or "").strip()
                lines.append(f"- {label}: {detail}" if label else f"- {detail}")
            else:
                lines.append(f"- {item}")

    _bucket("amplify", "AMPLIFY (play these up)")
    _bucket("maintain", "MAINTAIN (status quo, supporting)")
    _bucket("mute", "MUTE (play down or avoid)")

    audience = (brief.get("audience") or "").strip()
    if audience:
        lines.append(f"\n**Audience:** {audience}")
    value_prop = (brief.get("value_prop") or "").strip()
    if value_prop:
        lines.append(f"**Value prop:** {value_prop}")

    cust_lang = brief.get("customer_language") or []
    if cust_lang:
        lines.append("\n**Customer language (their words, not yours):**")
        for phrase in cust_lang:
            lines.append(f"- {phrase}")

    _bucket("proof_points", "Proof points")

    constraints = brief.get("constraints") or []
    if constraints:
        lines.append("\n**Constraints:**")
        for c in constraints:
            lines.append(f"- {c}")

    seasonal = brief.get("seasonal_patterns") or []
    if seasonal:
        lines.append("\n**Seasonal patterns:**")
        for s in seasonal:
            lines.append(f"- {s}")

    notes = (brief.get("notes") or "").strip()
    if notes:
        lines.append(f"\n**Notes:** {notes}")

    return "\n".join(lines)


def _marketing_plan_section(plan: MarketingPlan | None) -> str:
    if plan is None:
        return ""
    lines = ["## Marketing plan (what the owner has told us matters)"]
    if plan.audience:
        lines.append(f"**Target audience:** {plan.audience}")
    if plan.value_prop:
        lines.append(f"**Value prop:** {plan.value_prop}")
    if plan.customer_language_json:
        phrases = "; ".join(plan.customer_language_json[:6])
        lines.append(f"**Customer language:** {phrases}")
    if plan.proof_points_json:
        proofs = "; ".join(
            f"{p['label']} ({p['detail']})" if isinstance(p, dict) else str(p)
            for p in plan.proof_points_json[:4]
        )
        lines.append(f"**Proof points:** {proofs}")
    if plan.switching_json:
        s = plan.switching_json
        if isinstance(s, dict):
            pulls = ", ".join(s.get("pulls", [])[:5])
            pushes = ", ".join(s.get("pushes", [])[:5])
            if pulls:
                lines.append(f"**Why customers choose this shop:** {pulls}")
            if pushes:
                lines.append(f"**Why they leave others:** {pushes}")
    return "\n".join(lines)


def _recent_posts_section(posts: list[Post]) -> str:
    if not posts:
        return ""
    lines = ["## Recent posts (last 8, newest first)"]
    for p in posts:
        lines.append(f"- [{p.date}] {p.platform.upper()} · {p.status} · {p.title}")
    return "\n".join(lines)


def _performance_insights_section(perf: PerformanceSummary | None) -> str:
    if perf is None or not perf.insights_json:
        return ""
    lines = ["## Performance insights (last 30 days)"]
    lines.append(
        f"Reach {perf.reach_value:,} ({perf.reach_delta} vs prior 30). "
        f"Engagement {perf.engagement_value:,} ({perf.engagement_delta}). "
        f"Followers {perf.followers_value} ({perf.followers_delta}). "
        f"CTR {perf.ctr_value} ({perf.ctr_delta})."
    )
    for ins in perf.insights_json:
        kind = ins.get("kind", "note").upper()
        title = ins.get("title", "")
        body = ins.get("body", "")
        lines.append(f"- **{kind}** — {title}: {body}")
    return "\n".join(lines)


def _pinned_review_section(review: Review | None) -> str:
    if review is None:
        return ""
    return (
        "## Pinned review (owner is paying attention to this one)\n"
        f"{review.stars}★ · {review.platform.upper()} · {review.when_label} · {review.author}\n"
        f"> {review.body}"
    )


def _behavior_rules() -> str:
    return (
        "## Behavior\n\n"
        "- Continue the voice and register established in your prior turns with this owner.\n"
        "- Use **bold** for at most one phrase per response — the key decision or angle.\n"
        "- Keep responses tight (under 5 sentences) unless the owner asked for depth.\n"
        "- Plain text only. No headings, no markdown lists — the dashboard renders **bold** "
        "and nothing else.\n"
        "- End with a concrete next move (a proposed draft, a recommendation, or a single "
        "targeted question).\n"
        "\n## Tool use\n\n"
        "- When the owner asks for a draft of a post, use the **draft_post** tool so it "
        "lands in their Approvals queue as an editable card. Drafts are reversible and "
        "don't publish until the owner approves them. After firing, reply with one short "
        "sentence pointing to the card (e.g. \"Drafted — card's below. Want me to sharpen "
        "the hook?\") rather than re-pasting the draft.\n"
        "- When the owner asks for a draft response to a specific review, use "
        "**draft_review_response** with that review's id.\n"
        "- When the owner explicitly mentions budget, spend, or boosting a post, use "
        "**propose_boost** — don't just describe what a boost would cost."
    )
