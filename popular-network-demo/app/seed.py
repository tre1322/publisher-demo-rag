"""Seed the SQLite DB with Quadd.ai — Trevor Slette's real business.

This seed reflects a Day-1 customer: enrolled today, voice interview complete
(see data/voice-brief/quadd_ai.json), but no marketing has been published yet.
Posts/approvals/reviews/performance are intentionally minimal — the dashboard
shows an honest "you just joined" state rather than 47 days of fabricated
history.

Marketing-plan fields pull directly from the synthesized voice brief; the
voice brief itself is loaded into the AI Agent's system prompt via
app/agent/system_prompt.py (_voice_brief_section), keyed on the business slug.

Earlier Westbrook seed is preserved in git history if we want to switch back.
"""
from __future__ import annotations

from .db import SessionLocal
from datetime import datetime

from .models import (
    AdConnection,
    AdPlatformBudget,
    Business,
    ChatTurn,
    Connection,
    DashboardNotices,
    MarketingPlan,
    PerformanceSummary,
    Post,
    Approval,
    ReachTier,
    Review,
    ReviewAggregate,
    SettingsRow,
)


def seed_if_empty() -> bool:
    """Insert Quadd.ai if no business exists yet. Returns True if inserted."""
    db = SessionLocal()
    try:
        if db.query(Business).first() is not None:
            return False

        biz = Business(
            id=1,
            slug="quadd_ai",  # matches data/voice-brief/quadd_ai.json filename
            name="Quadd.ai",
            owner="Trevor Slette",
            owner_initials="TS",
            location="New Ulm, MN",
            publisher="Cottonwood County Citizen",
            phone="(507) 822-1253",
            tier=3,
            tier_label="Tier 3 — Concierge",
            monthly_price=150,
            joined_days_ago=0,
            joined_date="Today",
            voice_interview="complete",
            tech_name=None,
            years_in_town=None,
            ase_certified=False,
        )
        db.add(biz)
        db.flush()

        # Posts — Day 1: drafts the agent prepped from your voice brief plus
        # the launch-day founder's-story post you already published.
        post_seed = [
            ("p1", "2026-05-27", "web", "draft",
             "The 5 hours a week most newsrooms throw away",
             "If your reporters are still re-typing court documents into newspaper style, you're hemorrhaging hours. Our Universal Document Extractor learns your template and returns formatted copy in minutes. One Citizen Publishing writer is reclaiming two to three hours a week, no question. Built by a 28-year newspaper publisher — not a SaaS person guessing at your problems.",
             "Lead post for quadd.ai/blog. Hooks the Universal Document Extractor (your stated lead amplify item), grounds in the Citizen Publishing testimonial (your strongest proof point), positions you as founder-publisher (your moat). Drafted from voice brief; not published yet — your call."),
            ("p2", "2026-05-28", "fb", "pending",
             "Built by a publisher, for publishers",
             "After 28 years running a newspaper, I got tired of watching journalists lose hours re-typing court documents into AP style. So we built the tool I wanted: Universal Document Extractor, AP Style Proofer, and an Interview-to-Story pipeline — all three together for one bundled price. Free 7-day trial. quadd.ai",
             "FB post targeted at publisher-association groups. Pulls the bundle framing from your interview ('if you get one, you get all three'). 7-day trial mentioned as your stated low-risk on-ramp. Pending your sign-off."),
            ("p3", "2026-05-25", "web", "published",
             "How a 28-year newspaper publisher started Quadd.ai",
             "Last December I spent two full days trying to use ChatGPT to extract a 38-page court document into newspaper style. It couldn't do it. So we built Quadd.ai. Here's the story.",
             "Founding-story blog post — published last week as the launch announcement. Sets up the founder-credibility positioning the rest of this week's drafts lean on."),
        ]
        for ext_id, date, platform, status, title, draft, reasoning in post_seed:
            db.add(Post(
                business_id=biz.id, external_id=ext_id, date=date, platform=platform,
                status=status, title=title, draft=draft, reasoning=reasoning,
            ))

        # Approvals queue — Day 1: the agent has prepped 3 posts + 1 review
        # response from the voice brief. Each maps to a voice-brief amplify
        # item or proof point so the dashboard demo shows the brief in action.
        approvals_seed = [
            ("a1", "fb", "post",
             "Built by a publisher, for publishers",
             "After 28 years running a newspaper, I got tired of watching journalists lose hours re-typing court documents into AP style. So we built the tool I wanted: Universal Document Extractor, AP Style Proofer, and an Interview-to-Story pipeline — all three together for one bundled price. Free 7-day trial. quadd.ai",
             "Founder-credibility post leveraging your '28 years' anchor. Bundle framing per voice brief. Sign off to schedule for tomorrow morning.",
             None),
            ("a2", "web", "post",
             "The hidden tax: 5 hours a week most newsrooms throw away",
             "If your reporters are still re-typing court documents into newspaper style, you're losing 5+ hours a week per writer. A Citizen Publishing writer reclaimed two to three hours, no question, after switching to our Universal Document Extractor. It learns your template and returns formatted copy in minutes.",
             "Long-form blog post anchored on the lead amplify item. Drafted from voice brief — review and sign off to publish on quadd.ai/blog.",
             None),
            ("a3", "google", "post",
             "Search ad: 'newspaper AI tools'",
             "Quadd.ai — built by a 28-year publisher. Universal Document Extractor + AP Style Proofer + Interview-to-Story. 7-day free trial. Save 5 hours a week. quadd.ai",
             "Google search ad targeting 'newspaper AI tools' + adjacent keywords. Reject if you'd rather lead with the founder-story post before paying for clicks.",
             None),
            ("a4", "web", "review",
             "Response to Citizen Publishing testimonial",
             "Thank you so much — this is exactly the kind of feedback that keeps us building. With your permission, we'd love to quote this in our marketing as proof that the time savings are real. Mind if I write back to confirm? — Trevor",
             "Drafted thank-you with an explicit permission request, per your voice brief: 'ask permission before quoting publicly.' Approve to mark the review's response as approved + sent.",
             "One of our writers is using the Universal Document Extractor every week — saves two to three hours, no question. We're rolling it to the rest of the newsroom."),
        ]
        for ext_id, platform, kind, title, draft, note, original in approvals_seed:
            db.add(Approval(
                business_id=biz.id, external_id=ext_id, platform=platform, kind=kind,
                title=title, draft=draft, note=note, original_review_text=original,
            ))

        # Reviews — Day 1: one testimonial pulled in from voice brief
        # (the Citizen Publishing writer's "two to three hours a week" quote
        # ripples through as social proof, not a literal Google review).
        # We surface it as a pinned testimonial so the dashboard's Reviews tab
        # has something honest to show.
        db.add(Review(
            business_id=biz.id, external_id="r1", platform="web", stars=5,
            when_label="this week", author="Citizen Publishing (early user)",
            body="One of our writers is using the Universal Document Extractor every week — saves two to three hours, no question. We're rolling it to the rest of the newsroom.",
            ai_draft_response="Thank you so much — this is exactly the kind of feedback that keeps us building. With your permission, we'd love to quote this in our marketing as proof that the time savings are real. Mind if I write back to confirm? — Trevor",
            response_note="Testimonial pulled from voice brief. Permission-request draft ready — per the brief, ask before quoting publicly.",
            is_pinned=True, flagged=False, response_status="draft",
        ))
        db.add(ReviewAggregate(
            business_id=biz.id,
            aggregate=0.0,
            total=0,
            sparkline_json=[],
            sparkline_labels_json=[],
        ))

        # Performance — Day 1 has no published-post analytics yet, but the
        # PerformanceSummary row must exist for /api/performance to work.
        # Use zeros + the "you just joined" copy in the helper text.
        db.add(PerformanceSummary(
            business_id=biz.id,
            reach_value=0, reach_prev=0, reach_delta="—",
            engagement_value=0, engagement_prev=0, engagement_delta="—",
            followers_value="—", followers_prev="—", followers_delta="—",
            ctr_value="—", ctr_prev="—", ctr_delta="—",
            channel_mix_json=[],
            top_posts_json=[],
            insights_json=[
                {"kind": "win", "title": "Voice brief captured",
                 "body": "Your 23-minute interview synthesized into a structured marketing brief. The AI Agent is using it to shape every draft."},
                {"kind": "win", "title": "Founder-credibility positioning locked",
                 "body": "'Built by a publisher, for publishers' — your 28-year tenure is the moat. Lean on it in every founder-voice post."},
                {"kind": "lose", "title": "No published-post data yet",
                 "body": "Approve your first post to start the performance feedback loop. Once it's live we'll start measuring reach, engagement, and CTR."},
                {"kind": "lose", "title": "Channel mix undefined",
                 "body": "Voice interview was cut short before we covered channels. Web + FB are seeded as starting points. Add LinkedIn for B2B reach when you're ready."},
            ],
            daily_reach_current_json=[0] * 30,
            daily_reach_prev_json=[0] * 30,
        ))

        # Marketing plan — populated directly from the synthesized voice brief.
        db.add(MarketingPlan(
            business_id=biz.id,
            audience="Small to medium weekly newspaper and magazine publishers — resource-constrained, often where the publisher is also the editor and salesperson. Ranges from single-paper shops up to groups of 21 papers. The pain is shared: shrinking staff, budget cuts, and too many hours lost to tasks that aren't journalism.",
            value_prop="The tool that saves hours each week with measurable ROI — freeing journalists from re-typing documents, slogging through transcriptions, and manual proofing so they can get back to actual storytelling.",
            switching_json={
                "pulls": [
                    "Universal Document Extractor (the game-changer)",
                    "Built by a 28-year publisher",
                    "Real hours saved: 2–3/week per writer",
                    "Three tools, one bundle",
                    "7-day free trial",
                    "Leading-edge AI positioning",
                ],
                "pushes": [
                    "ChatGPT can't handle long court documents",
                    "Newsroom AI skepticism (publisher must okay)",
                    "Price-sensitivity in the publishing industry",
                    "Generic SaaS that doesn't understand newsrooms",
                ],
            },
            customer_language_json=[
                "\"Oh my gosh, I can't believe I've been re-typing all this information\"",
                "\"Having to go through an interview for a half hour before I even start writing the story\"",
                "\"It's saving them two to three hours each week, no question\"",
                "\"Free enough time in your schedule\"",
            ],
            proof_points_json=[
                {"label": "Citizen Publishing writer testimonial",
                 "detail": "2–3 hours/week saved, no question — heaviest user in that org"},
                {"label": "Customer range: 1 to 21 papers",
                 "detail": "Works across scales, from single-paper shops to multi-paper groups"},
                {"label": "Founder credibility",
                 "detail": "Trevor Slette, third-generation publisher, ~28 years in the industry"},
                {"label": "Origin: court document extraction",
                 "detail": "Built specifically because ChatGPT couldn't crack a 38-page court doc"},
            ],
            channels_json=[
                {"platform": "web", "pct": 45, "color": "#1B4F9A", "label": "quadd.ai blog"},
                {"platform": "fb",  "pct": 30, "color": "#1877F2", "label": "Publisher FB groups"},
                {"platform": "paid", "pct": 25, "color": "#0E5E6F", "label": "Search ads (Google)"},
            ],
            q3_goals_json=[
                {"label": "Free-trial signups per month", "target": 25, "current": 0, "unit": "signups/mo"},
                {"label": "Paying publishers", "target": 8, "current": 0, "unit": "customers"},
                {"label": "Avg ROI cited by customers", "target": 4.0, "current": 0, "unit": "hrs/wk saved"},
            ],
        ))

        # Settings + connections
        db.add(SettingsRow(
            business_id=biz.id,
            cadence="weekly",
            notifications_json=[
                {"key": "neg_review",       "label": "New negative review (2★ or below)", "on": True,  "via": "Email + push"},
                {"key": "post_scheduled",   "label": "Posts approaching scheduled time",  "on": True,  "via": "Email digest"},
                {"key": "ad_pacing",        "label": "Ad spend pacing alerts",            "on": True,  "via": "Email"},
                {"key": "weekly_digest",    "label": "Weekly performance digest",         "on": True,  "via": "Email · Mondays 8am"},
                {"key": "knowledge_gap",    "label": "Knowledge-gap detector findings",   "on": True,  "via": "Email"},
                {"key": "competitive_intel","label": "Competitive intel digest (Tier 3)", "on": True,  "via": "Email · Fridays"},
            ],
        ))
        for platform, account, last in [
            ("web", "quadd.ai/blog", "verified today"),
            ("fb",  "@quaddai",      "verified today"),
            ("google", "quadd.ai — Google Ads", "verified today"),
        ]:
            db.add(Connection(
                business_id=biz.id, platform=platform, account_label=account,
                status="connected", last_verified_text=last,
            ))

        # Dashboard notices: Day-1 onboarding attention items.
        db.add(DashboardNotices(
            business_id=biz.id,
            attention_json=[
                {"kind": "pending", "title": "1 post pending your approval",
                 "detail": "\"Built by a publisher, for publishers\" — drafted from your voice brief, ready when you are.",
                 "cta": "Review & approve", "icon": "inbox", "tone": "amber",
                 "target": "approvals", "targetId": "a1"},
                {"kind": "gap", "title": "Your voice brief is ready",
                 "detail": "23 minutes of interview synthesized into AMPLIFY/MAINTAIN/MUTE buckets. The AI Agent is using it to draft every post.",
                 "cta": "View brief", "icon": "sparkles", "tone": "teal",
                 "target": "plan"},
                {"kind": "spend", "title": "First post not published yet",
                 "detail": "Approve the founder-credibility FB post to start the feedback loop. Once it's live we'll start measuring reach.",
                 "cta": "Go to Approvals", "icon": "trending", "tone": "teal",
                 "target": "approvals", "targetId": "a1"},
                {"kind": "review", "title": "Channel mix needs your input",
                 "detail": "Interview ended before we covered channels. Web + FB + Google ads are starting defaults. Add LinkedIn when you're ready for B2B reach.",
                 "cta": "Edit marketing plan", "icon": "alert", "tone": "red",
                 "target": "plan"},
            ],
            week_recap_json=[
                {"when": "Today, 9:33am", "text": "Quadd.ai enrolled — concierge tier, Cottonwood County Citizen publisher"},
                {"when": "Today, 9:37am", "text": "Voice interview started over LiveKit — 23 minutes captured"},
                {"when": "Today, 10:01am", "text": "Voice brief synthesized — AMPLIFY/MAINTAIN/MUTE buckets populated from your actual words"},
                {"when": "Today, 10:15am", "text": "Two posts drafted from the brief — one queued for your approval"},
                {"when": "Today, ongoing", "text": "AI Agent loaded with your voice brief — try the chat tab"},
            ],
            stats_overrides_json={
                "posts":      {"value": 0,  "prev": 0, "delta": "—", "helper": "first post pending approval"},
                "engagement": {"value": "—", "helper": "no published posts yet", "positive": False},
                "reviews":    {"value": 0, "rating": 0.0, "helper": "no public reviews yet"},
                "spend":      {"used": 0, "budget": 150, "helper": "$150/mo concierge tier"},
                "chatbot":    {"value": 0, "helper": "not yet configured"},
            },
        ))

        # No seeded chat turns — voice carries purely via the system prompt's
        # voice brief on Day 1. The owner's first message is their actual first
        # interaction with the agent.

        # Phase D — reach-tier ladder. Mirrors docs/amplora_business_plan.md §3.2.
        # The territory list is the SW Peach pilot footprint described in §4.2.
        # Multipliers anchor on the worked example ($200 → $300/$400/$500–600);
        # the Maximum tier picks the midpoint of the +150–200% range (175%).
        _seed_reach_tiers(db, business_id=1)

        # Phase E — paid-ad spend management. Quadd is Day-1: no caps set,
        # no platforms connected. The dashboard's empty state will explain
        # the value prop until the owner sets a cap or connects an account.
        _seed_ad_platform_budgets(db, business_id=1)
        _seed_ad_connections(db, business_id=1)

        db.commit()
        return True
    finally:
        db.close()


# Pilot-territory footprint. The local tier sees only its own publisher; each
# higher tier widens the radius until Maximum spans every licensed territory.
# Codes are the slugified town/city names used by the chatbot's territory router.
_SW_PEACH_TERRITORIES = {
    "local":    ["new_ulm"],
    "regional": ["new_ulm", "windom", "marshall", "pipestone"],
    "network":  ["new_ulm", "windom", "marshall", "pipestone", "westbrook", "mountain_lake", "fairmont", "tracy"],
    "maximum":  ["new_ulm", "windom", "marshall", "pipestone", "westbrook", "mountain_lake",
                 "fairmont", "tracy", "redwood_falls", "worthington", "luverne", "jackson"],
}


# Display labels for each ad platform. Used by the AdsView budget allocator
# and by the AdConnection seed helper. Order matters — this is the order the
# budget allocator card will list them in.
_AD_PLATFORMS = [
    ("fb_ig",      "Meta (Facebook + Instagram)"),
    ("google_ads", "Google Ads"),
    ("tiktok",     "TikTok Ads"),
    ("linkedin",   "LinkedIn Ads"),
]


def _current_month_year() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _seed_ad_platform_budgets(db, business_id: int) -> None:
    """One zero-cap row per platform for the current month. Owner sets caps later."""
    month = _current_month_year()
    for key, _label in _AD_PLATFORMS:
        db.add(AdPlatformBudget(
            business_id=business_id,
            platform=key,
            month_year=month,
            monthly_cap_cents=0,
            spend_cents=0,
            status="active",
        ))


def _seed_ad_connections(db, business_id: int) -> None:
    """One disconnected row per platform. Connect flow lives in Settings (E.6)."""
    for key, label in _AD_PLATFORMS:
        db.add(AdConnection(
            business_id=business_id,
            platform=key,
            account_label=label,
            external_account_id=None,
            oauth_token=None,
            status="disconnected",
        ))


def _seed_reach_tiers(db, business_id: int) -> None:
    ladder = [
        {
            "tier_key": "local",
            "label": "Local Reach",
            "multiplier_pct": 0,
            "radius_miles": None,
            "description": "Included in your base ad rate. Your publication + the Cottonwood County Citizen chatbot in New Ulm.",
        },
        {
            "tier_key": "regional",
            "label": "Regional Reach (+50%)",
            "multiplier_pct": 50,
            "radius_miles": 50,
            "description": "Adds chatbot reach in adjacent territories within 50 miles. Good fit for help-wanted, real estate, auto sales, ag.",
        },
        {
            "tier_key": "network",
            "label": "Network Reach (+100%)",
            "multiplier_pct": 100,
            "radius_miles": 100,
            "description": "Full network reach across every territory within 100 miles of New Ulm. Built for advertisers happy to drive customers in from an hour away.",
        },
        {
            "tier_key": "maximum",
            "label": "Maximum Reach (+175%)",
            "multiplier_pct": 175,
            "radius_miles": None,
            "description": "Every licensed territory across the Amplora network. Use when geography genuinely doesn't matter — niche services, statewide audiences.",
        },
    ]
    for row in ladder:
        db.add(ReachTier(
            business_id=business_id,
            tier_key=row["tier_key"],
            label=row["label"],
            multiplier_pct=row["multiplier_pct"],
            radius_miles=row["radius_miles"],
            territories_json=_SW_PEACH_TERRITORIES[row["tier_key"]],
            description=row["description"],
        ))
