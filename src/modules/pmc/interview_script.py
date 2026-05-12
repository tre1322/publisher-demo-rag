"""Interview script — the canonical question backbone for PMC interviews.

This script has THREE consumers in the W2 pipeline:

  1. pre_interview_brief() / quantitative_by_section() — turn the script
     into the email + page that tell the owner what topics will be covered
     and what to fill in async before their voice call.

  2. transcript_to_pmc() — uses the script as the EXPECTED OUTPUT
     STRUCTURE in the LLM prompt that extracts a PMC from a transcript.
     Each question key becomes a section in the PMC; the agent also
     produces a STRATEGIC SUMMARY block at the top from the question
     answers (the plan generator reads that summary first).

  3. (W2.2 — voice) live agent — drives the conversation question by
     question, follows up using `follow_up_hints`, narrates pacing.

VERSIONING. Bump SCRIPT_VERSION when a question materially changes. The
version is recorded with each PMC row (`script_version` column) so we
can tell which interviews ran with which questions and re-run extraction
when the script evolves.

DECISIONS ENCODED (Trevor 2026-05-09):
  Decision 2 — Tone: warm-personal
    "Tell me how you got into the business" not "I have 12 questions about
    your operation". Earns trust by sounding like a friend who knows the
    business, not an enterprise procurement form. Verbose owners give the
    richest PMCs and warm tone keeps them talking.

  Decision 3 — Length: adaptive, 60-min hard cap
    Targets ~35 min of actual conversation. Agent narrates pacing
    ("we have about 15 min left, want to cover X or save it for next
    time?"). Hard 45 frustrated verbose owners; adaptive lets the agent
    decide when to push and when to wrap.

  Pre-interview prep is REQUIRED (Trevor 2026-05-09): "we will definitely
  let them know them before they start." Owner sees brief + fills
  quantitative form before the voice call is scheduled.

VERSION HISTORY:
  1.0.0 (2026-05-09) — initial. 6 quantitative + 12 qualitative.
  1.2.0 (2026-05-10) — material strategy upgrade. Reframed from
    "interview transcript" to "marketing intelligence file." Added
    competitive landscape, channel mix, content/channel comfort,
    offer boundaries, anti-customer, decision authority. Form grouped
    into 5 visual sections. transcript_to_pmc now produces a STRATEGIC
    SUMMARY block at the top of every PMC. The 8-decision plan
    framework drives the question set:
      who to target / what to amplify / why us / where to show up /
      when / how to convert / against whom / success target
  1.3.0 (2026-05-11) — owner-driven service classification. Rewrote
    `priority_services` from two-way ("amplify / don't promote") to
    three-way ("amplify / maintain / mute"), letting the owner sort
    their own service list rather than asking the LLM to infer the
    bucket from indirect signals. Pairs with prompt v3, which uses
    the owner's pile assignments verbatim and only falls back to
    inference when the owner skipped a service. Driven by a real-LLM
    test (Westbrook Auto, 2026-05-11) where v2's binary push/mute
    framing forced the LLM to either fabricate decisiveness on a
    cash-cow service or hedge against the prompt's instructions.
"""

from dataclasses import dataclass, field
from enum import Enum

# ──────────────────────────────────────────────────────────────────────
#  VERSION + KNOBS
# ──────────────────────────────────────────────────────────────────────

SCRIPT_VERSION = "1.3.0"

INTERVIEW_TONE = "warm-personal"
INTERVIEW_TARGET_MINUTES = 35
INTERVIEW_LENGTH_CAP_MINUTES = 60


class Category(str, Enum):
    IDENTITY = "identity"
    OPERATION = "operation"
    VOICE = "voice"
    CUSTOMERS = "customers"
    DIFFERENTIATION = "differentiation"
    GROWTH = "growth"
    SWITCHING = "switching"


# Form sections, in render order. Pre-interview form is grouped into these
# visual sections so a 23-field form doesn't feel like a 23-field form.
FORM_SECTIONS: list[str] = [
    "Basic business info",
    "Contact and online presence",
    "Services and sales",
    "Marketing and competition",
    "Assets and permissions",
]


@dataclass
class Question:
    """One backbone question.

    Attributes:
        key: Stable string key. Becomes a section heading in the PMC and
             the join key for cross-version diffs. Don't rename casually.
        category: Topic group. Used for the pre-interview brief
             ("we'll cover Identity, Operation, Voice, ...").
        prompt: The warm-personal phrasing the live agent uses (W2.2)
             and that the LLM sees as the section description (W2.1).
        follow_up_hints: Sample follow-ups the agent reaches for if the
             initial answer is short. Not a script — guidance.
        weight: 1=standard, 2=important, 3=critical. If the interview
             is running long, the agent drops weight=1/2 questions first;
             weight=3 are must-cover.
        quantitative: True = captured by the pre-interview FORM (not the
             voice call). Hours, prices, services list — facts that
             don't need conversation.
        form_section: Which section of the form this field renders into
             (must match a FORM_SECTIONS entry). Voice questions leave
             this None.
    """

    key: str
    category: Category
    prompt: str
    follow_up_hints: list[str] = field(default_factory=list)
    weight: int = 1
    quantitative: bool = False
    form_section: str | None = None


# ══════════════════════════════════════════════════════════════════════
#  THE INTERVIEW SCRIPT — TREVOR'S CONTRIBUTION SLOT
# ══════════════════════════════════════════════════════════════════════
#
# v1.2.0 reframes this from "interview" to "marketing intelligence
# extraction." Question structure now maps to the 8 decisions a real
# marketing plan must make:
#
#   1. WHO to target (primary + secondary persona, anti-target)
#   2. WHAT to amplify vs mute
#   3. WHY us (positioning, proof, voice)
#   4. WHERE to show up (channel mix, budget envelope)
#   5. WHEN to show up (calendar, lead time)
#   6. HOW to convert (offer architecture, lead handling)
#   7. AGAINST WHOM (named competitive frame)
#   8. SUCCESS TARGET (owner-defined, measurable)
#
# Trevor's domain expertise drives question content. Three reasons it
# beats LLM-default questions:
#
#   1. Surfaces what owners HIDE — price-tier embarrassment, "we used
#      to do X but stopped because...", customer mix shifts.
#   2. Triggers STORY responses where useful (origin, switching), not
#      survey responses.
#   3. Matches the cadence of conversations John + Trevor will hear.
#
# Add/remove/reword freely. Bump SCRIPT_VERSION when changing materially.
# The pipeline reads whatever shape the list is in.
#
# ══════════════════════════════════════════════════════════════════════

INTERVIEW_SCRIPT: list[Question] = [

    # ══════════════════════════════════════════════════════════════════
    # PRE-INTERVIEW FORM — fillable async, owner's own pace
    # ══════════════════════════════════════════════════════════════════

    # ── Section 1: Basic business info ──
    Question(
        key="business_name",
        category=Category.IDENTITY,
        prompt="Confirm the business name as it appears on your sign and tax filings.",
        quantitative=True, form_section="Basic business info", weight=3,
    ),
    Question(
        key="physical_address",
        category=Category.OPERATION,
        prompt="Physical address — including suite, building, or shop bay if relevant.",
        quantitative=True, form_section="Basic business info", weight=3,
    ),
    Question(
        key="service_area",
        category=Category.CUSTOMERS,
        prompt="Primary service area: towns, counties, ZIP codes, or mileage radius. (For local SEO and ad targeting.)",
        quantitative=True, form_section="Basic business info", weight=3,
    ),
    Question(
        key="hours",
        category=Category.OPERATION,
        prompt="Operating hours by day of week. Note holiday hours separately if they differ.",
        quantitative=True, form_section="Basic business info", weight=3,
    ),
    Question(
        key="years_in_business",
        category=Category.IDENTITY,
        prompt="Year founded.",
        quantitative=True, form_section="Basic business info", weight=2,
    ),

    # ── Section 2: Contact and online presence ──
    Question(
        key="website_url",
        category=Category.OPERATION,
        prompt="Website URL.",
        quantitative=True, form_section="Contact and online presence", weight=3,
    ),
    Question(
        key="google_business_profile",
        category=Category.OPERATION,
        prompt="Google Business Profile link, if available.",
        quantitative=True, form_section="Contact and online presence", weight=3,
    ),
    Question(
        key="facebook_handle",
        category=Category.OPERATION,
        prompt="Facebook page URL or handle.",
        quantitative=True, form_section="Contact and online presence", weight=2,
    ),
    Question(
        key="instagram_handle",
        category=Category.OPERATION,
        prompt="Instagram handle.",
        quantitative=True, form_section="Contact and online presence", weight=2,
    ),
    Question(
        key="other_social_handles",
        category=Category.OPERATION,
        prompt="Other active pages: TikTok, LinkedIn, YouTube, Pinterest, Yelp, Nextdoor, etc.",
        quantitative=True, form_section="Contact and online presence", weight=1,
    ),
    Question(
        key="preferred_contact_methods",
        category=Category.OPERATION,
        prompt="Best ways for customers to contact you: phone, text, email, website form, online booking, walk-in, Facebook Messenger, etc.",
        quantitative=True, form_section="Contact and online presence", weight=3,
    ),

    # ── Section 3: Services and sales ──
    Question(
        key="services_and_prices",
        category=Category.OPERATION,
        prompt="List all services and what you charge for each. Include things you do that customers don't always know about.",
        quantitative=True, form_section="Services and sales", weight=3,
    ),
    Question(
        key="priority_services_quick",
        category=Category.GROWTH,
        prompt="Quick version: which 2-3 services do you most want to grow? (We'll dig into the 'why' on the call.)",
        quantitative=True, form_section="Services and sales", weight=3,
    ),
    Question(
        key="payment_methods",
        category=Category.OPERATION,
        prompt="Payment methods accepted (cash, card, check, etc.).",
        quantitative=True, form_section="Services and sales", weight=2,
    ),
    Question(
        key="financing_options",
        category=Category.OPERATION,
        prompt="Do you offer or accept financing? If yes, which kind (in-house, third-party, financing partners)?",
        quantitative=True, form_section="Services and sales", weight=2,
    ),
    Question(
        key="current_offers",
        category=Category.SWITCHING,
        prompt="Any promotions, discounts, packages, or seasonal offers currently running?",
        quantitative=True, form_section="Services and sales", weight=2,
    ),

    # ── Section 4: Marketing and competition ──
    Question(
        key="top_3_competitors",
        category=Category.DIFFERENTIATION,
        prompt="Name your top 3 competitors. For each: location, what they do well, what they do poorly, and what they say in their marketing.",
        quantitative=True, form_section="Marketing and competition", weight=3,
    ),
    Question(
        key="current_marketing_spend",
        category=Category.GROWTH,
        prompt="What are you currently spending on marketing each month, by channel? (Print, Facebook ads, Google ads, radio, sponsorships, etc. Rough estimates are fine.)",
        quantitative=True, form_section="Marketing and competition", weight=3,
    ),
    Question(
        key="marketing_history_quick",
        category=Category.GROWTH,
        prompt="Quick version: which marketing has worked best for you over the years? (We'll dig into specifics on the call.)",
        quantitative=True, form_section="Marketing and competition", weight=3,
    ),
    Question(
        key="owner_channel_comfort",
        category=Category.VOICE,
        prompt="Which formats are you willing to appear in? Check all: video (on-camera), photo, audio (podcast/radio), written posts, none. Anything you absolutely won't do?",
        quantitative=True, form_section="Marketing and competition", weight=3,
    ),
    Question(
        key="marketing_decision_authority",
        category=Category.OPERATION,
        prompt="Who needs to approve marketing decisions before they go live? (Just you, you + spouse, you + partner, board, etc.)",
        quantitative=True, form_section="Marketing and competition", weight=2,
    ),

    # ── Section 5: Assets and permissions ──
    Question(
        key="photos_and_assets",
        category=Category.VOICE,
        prompt="Paste links to your logo, staff photos, product photos, storefront photos, before-and-after photos, menus, brochures, current ads, or any brand materials. (Or write 'send via email' and we'll follow up.)",
        quantitative=True, form_section="Assets and permissions", weight=3,
    ),
    Question(
        key="marketing_permissions",
        category=Category.VOICE,
        prompt="What may we use in marketing? Check or list: owner name, staff names, customer photos (with their permission), project photos, testimonials, prices, current offers, awards, community sponsorships.",
        quantitative=True, form_section="Assets and permissions", weight=3,
    ),

    # ══════════════════════════════════════════════════════════════════
    # VOICE INTERVIEW — qualitative, story + judgment
    # 21 questions: 14 weight=3 (must-cover) + 7 weight=2 (drop if long)
    # ══════════════════════════════════════════════════════════════════

    # ── Phase A: Warm-up / Identity ──
    Question(
        key="origin_story",
        category=Category.IDENTITY,
        prompt="Tell me how you got into this business. What were you doing before, and what made you start this?",
        follow_up_hints=[
            "What was the first month or year actually like?",
            "Who else was involved in the early days?",
            "Looking back — what would you tell yourself on day one?",
        ],
        weight=3,
    ),
    Question(
        key="why_this_business",
        category=Category.IDENTITY,
        prompt="When you wake up Monday morning, what part of the work makes you want to come in?",
        follow_up_hints=["What part do you wish someone else would do?"],
        weight=2,
    ),
    Question(
        key="community_role",
        category=Category.IDENTITY,
        prompt="How does your business show up in the community outside of selling things?",
        follow_up_hints=[
            "Do you sponsor teams, events, schools, churches, or nonprofits?",
            "Are you involved in local causes?",
            "Do people know the owners or staff personally?",
            "Any other local businesses you cross-promote with?",
            "What community connections would you want people to know about?",
        ],
        weight=2,
    ),

    # ── Phase B: Customer foundation ──
    Question(
        key="ideal_customer",
        category=Category.CUSTOMERS,
        prompt="Walk me through your favorite kind of customer to work with — what they're like, what they need, what makes the interaction good.",
        follow_up_hints=[
            "How would they describe you to a friend?",
            "Are most of your customers like that, or just some?",
            "Demographics: age range, household type, income tier?",
        ],
        weight=3,
    ),
    Question(
        key="anti_customer",
        category=Category.CUSTOMERS,
        prompt="Are there customers who usually are not the right fit for your business — not because they're bad people, but because your service, pricing, style, or process just isn't the best match for them?",
        follow_up_hints=[
            "Are there jobs or requests you usually avoid?",
            "Are there customers who are mostly shopping on price?",
            "Are there situations where you know another provider would be a better fit?",
            "What warning signs tell you a customer may not be a good match?",
        ],
        weight=2,
    ),
    Question(
        key="customer_triggers",
        category=Category.CUSTOMERS,
        prompt="What usually happens in a customer's life or business that makes them finally call you, stop in, or buy?",
        follow_up_hints=[
            "Is it an emergency?",
            "A seasonal need?",
            "A life event?",
            "Something broke?",
            "They saw someone else do it?",
            "They're planning ahead?",
        ],
        weight=3,
    ),
    Question(
        key="sales_objections",
        category=Category.SWITCHING,
        prompt="When someone almost buys but hesitates, what usually holds them back?",
        follow_up_hints=[
            "Price?",
            "Timing?",
            "They don't understand the value?",
            "They think they can do it themselves?",
            "They trust someone else already?",
            "They need to ask a spouse, boss, or board?",
        ],
        weight=2,
    ),
    Question(
        key="proof_and_credibility",
        category=Category.DIFFERENTIATION,
        prompt="What proof do you have that customers are happy or that your work gets results?",
        follow_up_hints=[
            "Reviews — count + trajectory vs a year ago?",
            "Repeat customers — what % roughly?",
            "Before-and-after photos?",
            "Awards, certifications, media features?",
            "Years of experience?",
            "Well-known customers or projects?",
            "A customer story you're especially proud of?",
        ],
        weight=3,
    ),

    # ── Phase C: Strategic priorities ──
    Question(
        key="priority_services",
        category=Category.GROWTH,
        prompt=(
            "Let's sort every service you offer into three piles. "
            "First — which ones do you want more customers for? "
            "Second — which ones are fine right where they are? "
            "And third — which ones would you rather refer out or stop offering entirely?"
        ),
        follow_up_hints=[
            "For the 'more customers' pile — what makes those the ones you want to grow?",
            "For the 'fine where they are' pile — is it capacity, margin, or you just don't love that work?",
            "For the 'refer out / stop' pile — where would those customers go instead?",
            "Anything you offer only because people expect it, but wish you didn't?",
            "Any service you'd love to add but don't yet?",
        ],
        weight=3,
    ),
    Question(
        key="profitability_direction",
        category=Category.GROWTH,
        prompt="Without sharing anything you're uncomfortable with, which types of customers, products, or services are most valuable to the business?",
        follow_up_hints=[
            "What brings people back again and again?",
            "What's a one-time sale versus a long-term customer?",
            "What service looks small but leads to bigger work later?",
            "Are there low-margin items you advertise mostly to get people in the door?",
            "Average customer value range — per visit, per year?",
        ],
        weight=3,
    ),
    Question(
        key="capacity_and_bottlenecks",
        category=Category.OPERATION,
        prompt="If your marketing worked really well next month, where would the business feel the strain first?",
        follow_up_hints=[
            "Too many phone calls?",
            "Not enough staff?",
            "Limited appointment slots?",
            "Inventory limits?",
            "Seasonal workload?",
            "Would you rather have more weekday business, evening business, online orders, appointments, walk-ins?",
        ],
        weight=3,
    ),
    Question(
        key="seasonality_calendar",
        category=Category.GROWTH,
        prompt="Walk me through your year. What are your busy seasons, slow seasons, big deadlines, events, holidays, or times when people should be thinking about you?",
        follow_up_hints=[
            "When should marketing start before the busy season?",
            "Any annual sales or events?",
            "Any months you need help filling?",
            "Any dates where advertising is too late?",
        ],
        weight=2,
    ),

    # ── Phase D: Positioning + voice ──
    Question(
        key="differentiation_and_positioning",
        category=Category.DIFFERENTIATION,
        prompt="When a customer is choosing between you and a competitor, what's the thing that closes the deal? AND if you had to put it in one sentence — for [target] who [need], we are [category] that [distinction] — how would you fill in the blanks?",
        follow_up_hints=[
            "What can a customer expect from you that they can't expect from anyone else?",
            "What would your competitors say is the thing you do best?",
            "How do you price compared to competitors — premium, middle, value?",
        ],
        weight=3,
    ),
    Question(
        key="voice_and_tone",
        category=Category.VOICE,
        prompt="If your business were a person, how would they talk? Formal, casual, dry humor, blunt, warm? Give me a couple words other people have used to describe how you communicate.",
        follow_up_hints=[
            "Any phrases or words you find yourself using a lot?",
            "How do you sign off — first name, full name, 'the team'?",
        ],
        weight=2,
    ),
    Question(
        key="brand_guardrails",
        category=Category.VOICE,
        prompt="Are there any words, claims, styles, jokes, offers, or types of advertising you absolutely do not want associated with your business?",
        follow_up_hints=[
            "Anything that feels too salesy?",
            "Anything competitors say that you dislike?",
            "Any promises you never want made?",
            "Any legal, professional, or industry restrictions?",
        ],
        weight=3,
    ),

    # ── Phase E: Conversion architecture ──
    Question(
        key="switching_incentive_and_lead_magnet",
        category=Category.SWITCHING,
        prompt="If somebody is currently using a competitor and you wanted them to try you instead, what would you offer or say to make the switch easy? AND do you have anything you give away free — a quote, a checklist, a sample — to get someone in the door for the first time?",
        follow_up_hints=[
            "What would convince them you're worth the trouble of switching?",
            "Any first-time-customer offer or pitch?",
            "Do you have a referral program?",
            "Loyalty rewards for repeat customers?",
        ],
        weight=3,
    ),
    Question(
        key="offer_boundaries",
        category=Category.SWITCHING,
        prompt="Are there any discounts, promotions, bundles, guarantees, or offers you are comfortable using — and any you never want to use?",
        follow_up_hints=[
            "Do you ever offer first-time customer discounts?",
            "Do you prefer value-adds instead of discounts?",
            "Are there minimum prices or margins we should protect?",
            "Can the AI suggest offers for approval, or should it avoid discounts entirely?",
        ],
        weight=3,
    ),
    Question(
        key="marketing_history",
        category=Category.GROWTH,
        prompt="What kinds of marketing have worked best for you in the past — newspaper ads, Facebook, radio, direct mail, events, referrals, signs, sponsorships, email?",
        follow_up_hints=[
            "Anything that completely flopped?",
            "Any ad, phrase, or offer people still mention?",
            "Where do most new customers say they heard about you?",
            "Do you track this formally or mostly by memory?",
            "Do you have an email list? Platform, size, last send date?",
            "SMS marketing?",
        ],
        weight=3,
    ),
    Question(
        key="lead_handling",
        category=Category.OPERATION,
        prompt="When a new customer reaches out, what happens next?",
        follow_up_hints=[
            "Phone, email, form, Facebook message, walk-in?",
            "Who responds?",
            "How fast do you usually respond?",
            "Do you want appointments, calls, online orders, quote requests, or store visits?",
            "What information should a customer have ready?",
        ],
        weight=3,
    ),

    # ── Phase F: Success target + close ──
    Question(
        key="marketing_success_definition",
        category=Category.GROWTH,
        prompt="Six months from now, how would you know this marketing is working?",
        follow_up_hints=[
            "More calls?",
            "More walk-ins?",
            "More appointments?",
            "Higher-quality customers?",
            "More awareness?",
            "More repeat business?",
            "More traffic during slow times?",
            "More sales of a specific product or service?",
            "How often do you want to see numbers — daily, weekly, monthly?",
        ],
        weight=3,
    ),
    Question(
        key="anything_else",
        category=Category.IDENTITY,
        prompt="Anything I haven't asked that you'd want potential customers — and the AI helping us run your marketing — to know?",
        weight=2,
    ),
]


# ──────────────────────────────────────────────────────────────────────
#  Helpers (pipeline-facing — don't change without bumping consumers)
# ──────────────────────────────────────────────────────────────────────


def quantitative_questions() -> list[Question]:
    """Subset captured by the pre-interview FORM (in declared order)."""
    return [q for q in INTERVIEW_SCRIPT if q.quantitative]


def qualitative_questions() -> list[Question]:
    """Subset driven by the voice call (in declared order)."""
    return [q for q in INTERVIEW_SCRIPT if not q.quantitative]


def quantitative_by_section() -> list[tuple[str, list[Question]]]:
    """Quantitative questions grouped by `form_section`, in declared section order.

    Used by pmc_prep.html to render the form as 5 visual sections instead
    of one intimidating 23-field wall.
    """
    grouped: dict[str, list[Question]] = {s: [] for s in FORM_SECTIONS}
    other: list[Question] = []
    for q in quantitative_questions():
        if q.form_section in grouped:
            grouped[q.form_section].append(q)
        else:
            other.append(q)
    out = [(s, qs) for s, qs in grouped.items() if qs]
    if other:
        out.append(("Other", other))
    return out


def topic_areas() -> list[str]:
    """Human-readable topic list (from voice call categories)."""
    seen: set[str] = set()
    out: list[str] = []
    for q in qualitative_questions():
        label = q.category.value.replace("_", " ").title()
        if label not in seen:
            out.append(label)
            seen.add(label)
    return out


def pre_interview_brief() -> str:
    """The plain-text brief shown to the owner BEFORE the voice call.

    Per Trevor 2026-05-09: "This is an important interview, the scope of
    their entire marketing plan (and this project as a whole) relies on
    this, and we will definitely let them know them before they start."
    """
    topics = "\n".join(f"  - {t}" for t in topic_areas())
    return (
        "Before your interview\n"
        "═════════════════════\n"
        "\n"
        f"Estimated time: about {INTERVIEW_TARGET_MINUTES} minutes "
        f"(we won't go past {INTERVIEW_LENGTH_CAP_MINUTES}).\n"
        "\n"
        "We'll cover the following topics:\n"
        f"{topics}\n"
        "\n"
        "Before the call, please fill out the form on this page. These are\n"
        "the facts (hours, services, prices, contact info, online presence,\n"
        "competitors, current marketing) we don't need to spend the\n"
        "interview talking about. Filling them in advance keeps the\n"
        "conversation focused on the parts that only YOU can answer:\n"
        "who you are, who you serve, what you want to grow, and what\n"
        "makes you different.\n"
        "\n"
        "There are no wrong answers. We can pause whenever you need to think.\n"
    )
