"""SQLAlchemy ORM models for the Popular Network dashboard.

Schema philosophy:
- Things the dashboard *mutates* (posts, approvals, reviews, settings,
  connections, marketing_plan) get normalized columns.
- Things the dashboard *reads as a derived blob* (performance summary,
  channel mix, sparkline data, attention feed, week recap) get JSON columns —
  cheap to evolve, not worth normalizing for Phase A.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    owner: Mapped[str] = mapped_column(String(120))
    owner_initials: Mapped[str] = mapped_column(String(8))
    location: Mapped[str] = mapped_column(String(160))
    publisher: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40))
    tier: Mapped[int] = mapped_column(Integer)
    tier_label: Mapped[str] = mapped_column(String(120))
    monthly_price: Mapped[int] = mapped_column(Integer)
    joined_days_ago: Mapped[int] = mapped_column(Integer)
    joined_date: Mapped[str] = mapped_column(String(40))
    voice_interview: Mapped[str] = mapped_column(String(40))
    tech_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    years_in_town: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ase_certified: Mapped[bool] = mapped_column(Boolean, default=False)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    external_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    platform: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # draft|pending|approved|published
    title: Mapped[str] = mapped_column(String(280))
    draft: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    external_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("posts.id"), nullable=True)
    review_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reviews.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="post")  # post|review
    platform: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(280))
    draft: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_review_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # approved|edited|rejected
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    external_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    platform: Mapped[str] = mapped_column(String(16))
    stars: Mapped[int] = mapped_column(Integer)
    when_label: Mapped[str] = mapped_column(String(40))
    author: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    ai_draft_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|sent
    response_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    response_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReviewAggregate(Base):
    __tablename__ = "review_aggregates"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    aggregate: Mapped[float] = mapped_column(Float)
    total: Mapped[int] = mapped_column(Integer)
    sparkline_json: Mapped[Any] = mapped_column(JSON)  # list[float]
    sparkline_labels_json: Mapped[Any] = mapped_column(JSON)  # list[str]


class PerformanceSummary(Base):
    """Single-row-per-business snapshot of the performance tab.

    JSON columns hold the structured pieces the dashboard renders as charts —
    not normalized for Phase A; we can promote any of these to their own table
    later if Phase B/D needs to mutate them granularly.
    """

    __tablename__ = "performance_summary"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    reach_value: Mapped[int] = mapped_column(Integer)
    reach_prev: Mapped[int] = mapped_column(Integer)
    reach_delta: Mapped[str] = mapped_column(String(16))
    engagement_value: Mapped[int] = mapped_column(Integer)
    engagement_prev: Mapped[int] = mapped_column(Integer)
    engagement_delta: Mapped[str] = mapped_column(String(16))
    followers_value: Mapped[str] = mapped_column(String(16))
    followers_prev: Mapped[str] = mapped_column(String(16))
    followers_delta: Mapped[str] = mapped_column(String(16))
    ctr_value: Mapped[str] = mapped_column(String(16))
    ctr_prev: Mapped[str] = mapped_column(String(16))
    ctr_delta: Mapped[str] = mapped_column(String(16))
    channel_mix_json: Mapped[Any] = mapped_column(JSON)
    top_posts_json: Mapped[Any] = mapped_column(JSON)
    insights_json: Mapped[Any] = mapped_column(JSON)
    daily_reach_current_json: Mapped[Any] = mapped_column(JSON)
    daily_reach_prev_json: Mapped[Any] = mapped_column(JSON)


class MarketingPlan(Base):
    __tablename__ = "marketing_plan"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    audience: Mapped[str] = mapped_column(Text)
    value_prop: Mapped[str] = mapped_column(Text)
    switching_json: Mapped[Any] = mapped_column(JSON)
    customer_language_json: Mapped[Any] = mapped_column(JSON)
    proof_points_json: Mapped[Any] = mapped_column(JSON)
    channels_json: Mapped[Any] = mapped_column(JSON)
    q3_goals_json: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SettingsRow(Base):
    __tablename__ = "settings"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    cadence: Mapped[str] = mapped_column(String(16), default="weekly")  # each|weekly|auto
    # Notification preferences — list[{key, label, on, via, muted?}].
    # Added 2026-05-21 in B.4; nullable so existing DBs survive.
    notifications_json: Mapped[Any] = mapped_column(JSON, nullable=True)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    platform: Mapped[str] = mapped_column(String(16))
    account_label: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="connected")
    last_verified_text: Mapped[str] = mapped_column(String(80))


class DashboardNotices(Base):
    """The Home view's 'Needs your attention' feed + week recap.

    These are partly hand-curated and partly agent-generated; storing as JSON
    keeps Phase A simple and avoids a premature schema commitment.
    """

    __tablename__ = "dashboard_notices"

    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    attention_json: Mapped[Any] = mapped_column(JSON)
    week_recap_json: Mapped[Any] = mapped_column(JSON)
    stats_overrides_json: Mapped[Any] = mapped_column(JSON, nullable=True)


class ChatTurn(Base):
    """Pre-seeded AI Agent conversation (Phase A: read-only seed; Phase C: live)."""

    __tablename__ = "chat_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    conversation_id: Mapped[str] = mapped_column(String(40), default="seed")
    who: Mapped[str] = mapped_column(String(16))  # owner|agent
    when_label: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    attachment_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Escalation(Base):
    """Records 'Talk to a human' modal submissions for Phase B/F follow-up."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Phase D — Display Ad Amplification (Phase 1.5 of the business plan)
# ---------------------------------------------------------------------------
# Source of truth: docs/amplora_business_plan.md §3.2.
# - Local Reach (default, +0%):  selling pub + that territory's chatbot only
# - Regional Reach (+50%):       adjacent territories within ~50 miles
# - Network Reach (+100%):       full network within ~100 miles
# - Maximum Reach (+150–200%):   every licensed territory
# Worked example: a $200 nursing help-wanted ad becomes $300 / $400 / $500–600.
# Revenue split when ad surfaces cross-territory: 65 selling / 25 receiving / 10 platform.


class ReachTier(Base):
    """Per-business config for the 4 reach tiers shown in the Reach Configurator.

    Seeded once per business; mutable later if a publisher wants to override the
    default ladder for their territory. The `multiplier_pct` is what the
    Compose UI shows next to each tier ("+50% of base ad rate"); the estimator
    function uses it to compute the price delta.
    """

    __tablename__ = "reach_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    tier_key: Mapped[str] = mapped_column(String(16))  # local|regional|network|maximum
    label: Mapped[str] = mapped_column(String(64))
    multiplier_pct: Mapped[int] = mapped_column(Integer)  # 0, 50, 100, 175
    radius_miles: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    territories_json: Mapped[Any] = mapped_column(JSON)  # list[str] of territory codes covered at this tier
    description: Mapped[str] = mapped_column(Text)


class AdImpressionsByTerritory(Base):
    """Per-post per-territory impression + revenue tally.

    Populated by the chatbot's ad-serve pipeline (out of scope for the demo —
    rows are seeded fixture-style). The Cross-Territory Revenue card on
    PerformanceView reads this to show "where did your ad actually surface."
    """

    __tablename__ = "ad_impressions_by_territory"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("posts.id"), nullable=True)
    territory_code: Mapped[str] = mapped_column(String(32))  # e.g. "windom", "marshall", "pipestone"
    territory_label: Mapped[str] = mapped_column(String(80))
    impressions: Mapped[int] = mapped_column(Integer)
    paid_reach_tier: Mapped[str] = mapped_column(String(16))  # local|regional|network|maximum
    revenue_cents: Mapped[int] = mapped_column(Integer)  # gross before 65/25/10 split
    is_selling_territory: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD


class LocalFirstLog(Base):
    """Daily summary of how the Local-First Algorithm performed in this territory.

    Three counters per day:
    - queries_total: every consumer query the chatbot answered
    - queries_local_first: queries where local results filled the response
    - queries_out_of_territory_paid: queries where a paid out-of-territory ad
      surfaced because local supply didn't fully answer
    """

    __tablename__ = "local_first_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    log_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    queries_total: Mapped[int] = mapped_column(Integer, default=0)
    queries_local_first: Mapped[int] = mapped_column(Integer, default=0)
    queries_out_of_territory_paid: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Phase E — Multi-platform paid ad spend management
# ---------------------------------------------------------------------------
# Per docs/amplora_business_plan.md §3.4 V2 roadmap (autonomous ad spend mgmt).
# Pulled forward from V2 so the demo can show tier-3 ownership in practice.
#
# Distinct from Phase D's reach amplification: D = chatbot reach on Amplora's
# network (money to Amplora/publishers). E = direct paid spend on external
# platforms (money to Meta / Google / TikTok / LinkedIn). They coexist on
# Compose but the IA keeps them visually distinct (violet vs amber accents).
#
# Operational model: mocked execution. ad_campaigns rows get fake
# external_campaign_id; the /api/ads/tick endpoint advances spend +
# impressions on active campaigns. Real OAuth + platform APIs deferred.
#
# Tier gating: Business.tier 2 → tools queue proposals (Approval row).
# Business.tier 3 → tools mutate state autonomously within owner-set caps.


# Canonical platform keys for ad spend (distinct from Compose's social-post
# platforms `fb` / `ig` / `gbp` / `web`). `fb_ig` is one entity because
# Meta Ads Manager handles both FB + IG with one budget. `google_ads` is
# distinct from `gbp` (which is the Business Profile listing presence).
_AD_PLATFORM_KEYS = ("fb_ig", "google_ads", "tiktok", "linkedin")


class AdPlatformBudget(Base):
    """Per-business, per-platform monthly cap and rolling spend.

    One row per (business_id, platform, month_year). Spend ticks up as the
    /api/ads/tick endpoint advances active campaigns. Cap enforced when the
    agent's schedule_boost tool runs at Tier 3.
    """

    __tablename__ = "ad_platform_budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    platform: Mapped[str] = mapped_column(String(16))  # fb_ig | google_ads | tiktok | linkedin
    month_year: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    monthly_cap_cents: Mapped[int] = mapped_column(Integer, default=0)
    spend_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdCampaign(Base):
    """A single paid-ad campaign on an external platform.

    May or may not link to a Post (boost-on-launch ties campaign to post;
    standalone campaigns are post_id=None and use a free-text topic in name).
    Status progression: draft → pending_approval (Tier 2) → scheduled →
    active → completed (or paused / cancelled at any point).
    """

    __tablename__ = "ad_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    post_id: Mapped[Optional[int]] = mapped_column(ForeignKey("posts.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(16))  # fb_ig | google_ads | tiktok | linkedin
    name: Mapped[str] = mapped_column(String(160))
    daily_budget_cents: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer)
    planned_total_cents: Mapped[int] = mapped_column(Integer)
    actual_spend_cents: Mapped[int] = mapped_column(Integer, default=0)
    target_audience_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    origin: Mapped[str] = mapped_column(String(20), default="manual_owner")  # manual_owner|agent_proposed|agent_autonomous
    approved_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # owner|agent|null
    external_campaign_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    performance_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_ticked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AdConnection(Base):
    """Ad-platform account connection (distinct from social presence Connection).

    Connection (existing) = social presence: FB page, IG account, GBP listing.
    AdConnection (new)    = ad account auth: Meta Ads Manager, Google Ads,
                            TikTok Ads, LinkedIn Campaign Manager.

    OAuth deferred — `oauth_token` and `external_account_id` are mocked for
    the demo. status=connected lets schedule_boost actually create campaigns
    for that platform; status=disconnected blocks it with a clear error.
    """

    __tablename__ = "ad_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    platform: Mapped[str] = mapped_column(String(16))  # fb_ig | google_ads | tiktok | linkedin
    account_label: Mapped[str] = mapped_column(String(160))
    external_account_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    oauth_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # MOCKED — real OAuth is V2 work
    status: Mapped[str] = mapped_column(String(20), default="disconnected")  # connected|disconnected|error
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Phase F.1 — Tier 4 Inventory Verticals (auto / ag / realty / RV/boat / lumber)
# ---------------------------------------------------------------------------
# Per docs/amplora_business_plan.md §3.3:
#   Tier 4 Base ($799/mo): up to 50 listings, one feed integration.
#   Tier 4 Plus ($1,299/mo): up to 250 listings, multi-feed, multi-location.
#   Tier 4 Enterprise ($1,999+/mo): unlimited, custom integrations.
#
# Quadd.ai is B2B SaaS so it has no real inventory — the demo bumps Quadd to
# Tier 4 and shows the empty-state-as-marketing surface (per Phase D pattern).
# Real Tier 4 customers would be auto dealers, realtors, ag-equipment yards.


# Canonical feed types. `generic_csv` is the universal fallback for verticals
# we don't have a native adapter for yet.
_INVENTORY_FEED_TYPES = (
    "dealercenter",   # auto — DealerCenter DMS
    "vauto",          # auto — vAuto inventory
    "tractor_house",  # ag  — TractorHouse marketplace export
    "mls",            # realty — MLS RETS / RESO Web API
    "boat_trader",    # marine — BoatTrader feed
    "generic_csv",    # fallback — upload any structured CSV
)


class InventoryFeed(Base):
    """A connected inventory data source for a Tier 4 business.

    One row per (business_id, feed_type, optional location_label) — a multi-
    location dealer might have two vAuto feeds, one per lot. The credential
    blob is opaque (per-adapter shape); for the demo every adapter is mocked
    behind a CSV import path so `config_json` carries fixture metadata.
    """

    __tablename__ = "inventory_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    feed_type: Mapped[str] = mapped_column(String(24))  # see _INVENTORY_FEED_TYPES
    location_label: Mapped[str] = mapped_column(String(120))
    config_json: Mapped[Any] = mapped_column(JSON, nullable=True)  # adapter-specific config (URL, account_id, schedule)
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected|disconnected|error|paused
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # e.g. "47 listings · 0 errors"
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventoryListing(Base):
    """A single sellable item in a Tier 4 business's inventory.

    The shape is intentionally vertical-agnostic — `attributes_json` carries
    whatever per-vertical fields (year/make/model for auto, bed/bath for
    realty, hours-on-engine for ag equipment) without forcing a schema.
    `is_stale` denormalizes the (days_listed > 30 AND clicks_30d == 0) rule
    so the UI doesn't have to recompute on every list call.
    """

    __tablename__ = "inventory_listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("inventory_feeds.id"))
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    external_id: Mapped[str] = mapped_column(String(80))  # ID from the source system
    title: Mapped[str] = mapped_column(String(280))
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|sold|pending|stale|removed
    days_listed: Mapped[int] = mapped_column(Integer, default=0)
    query_impressions_30d: Mapped[int] = mapped_column(Integer, default=0)
    clicks_30d: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    attributes_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventoryFacetHit(Base):
    """Faceted-search visibility — what chatbot queries surfaced this business's inventory.

    One row per (business_id, facet_text, day). The Inventory view's
    "search visibility" card reads the top-N rows by hits to show "your
    listings rank #1 for '4WD trucks under $20k near Westbrook'."
    """

    __tablename__ = "inventory_facet_hits"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    facet_text: Mapped[str] = mapped_column(String(200))  # e.g. "4WD trucks under $20k near Westbrook"
    rank: Mapped[int] = mapped_column(Integer, default=1)  # 1 = #1 result
    hits_30d: Mapped[int] = mapped_column(Integer, default=0)
    sample_listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("inventory_listings.id"), nullable=True)


# ---------------------------------------------------------------------------
# Phase F.2 — Billing / Usage panel
# ---------------------------------------------------------------------------
# Settings → Billing sub-tab. Stripe is not wired into this demo (matches the
# Amplafai prod stance BILLING_ENABLED=false). Invoices are mock; "Change tier"
# CTA records intent only — doesn't actually charge.


class UsageMetric(Base):
    """Per-business, per-month, per-metric usage counter.

    Rolled up once per month for the Billing view. Metrics shown in v1:
      - posts_published
      - chatbot_conversations
      - ads_run
      - agent_token_spend_cents (modeled as cost-of-AI billed back)
    """

    __tablename__ = "usage_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    month_year: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    metric_key: Mapped[str] = mapped_column(String(40))
    value: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BillingInvoice(Base):
    """A single monthly invoice. Mock-only — Stripe integration deferred.

    The invoice row exists so the Billing view has a history table to render
    and a 'Download PDF' button to no-op against. When Stripe lands, this
    table becomes a mirror of stripe.Invoice and `external_invoice_id` fills.
    """

    __tablename__ = "billing_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    period_label: Mapped[str] = mapped_column(String(40))  # e.g. "May 2026"
    period_start: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    period_end: Mapped[str] = mapped_column(String(10))    # YYYY-MM-DD
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="paid")  # paid|due|past_due|refunded
    tier_at_period: Mapped[int] = mapped_column(Integer)
    line_items_json: Mapped[Any] = mapped_column(JSON, nullable=True)  # list[{label, amount_cents}]
    external_invoice_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)  # filled when Stripe lands
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TierChangeRequest(Base):
    """Owner-initiated tier-change intent. Records the request only — no charge."""

    __tablename__ = "tier_change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    from_tier: Mapped[int] = mapped_column(Integer)
    to_tier: Mapped[int] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|completed|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Phase F.3 — Tier 3+ Chatbot Preview
# ---------------------------------------------------------------------------
# Owner-facing transcript viewer for the consumer-facing chatbot (the OTHER
# AI, not the AI Agent in Chat). One row per consumer session. The dashboard's
# ChatbotPreviewView lists these with sentiment + escalation flags.


class ChatbotConversation(Base):
    """A single consumer ↔ chatbot session.

    `transcript_json` is the source of truth — list of {who, text, at} turns.
    `sentiment` is computed at session end (positive|neutral|negative|mixed).
    `escalation_flag` marks sessions where the chatbot detected an unhandled
    question that should reach the owner — shown as a chip in the list.
    `topic_label` is a short headline ("Hours on Memorial Day?", "Quote for
    18-month-old truck") so the list reads without expanding every row.
    """

    __tablename__ = "chatbot_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    external_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    consumer_label: Mapped[str] = mapped_column(String(80))  # e.g. "anonymous · iPhone Safari"
    topic_label: Mapped[str] = mapped_column(String(200))
    transcript_json: Mapped[Any] = mapped_column(JSON)  # list[{who: 'consumer'|'bot', text, at}]
    sentiment: Mapped[str] = mapped_column(String(16), default="neutral")  # positive|neutral|negative|mixed
    escalation_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    referrer_label: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)  # e.g. "Cottonwood County Citizen → article"
