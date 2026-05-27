"""Phase G — auto-extractors for ingested chatbot conversations.

When the publisher's consumer-facing chatbot POSTs a transcript to
/api/chatbot/ingest, we don't get sentiment / topic / escalation flags from
the publisher — we derive them from the transcript itself.

Three functions live here:
- extract_topic_label(transcript) -> str
- score_sentiment(transcript) -> str  (positive|neutral|negative|mixed)
- detect_escalation(transcript) -> tuple[bool, Optional[str]]

The functions are intentionally rule-based + heuristic — we don't want every
ingest to spend an LLM call. If accuracy proves too low at pilot scale, the
swap point to an LLM-based classifier is one function each.

All three take the same shape: list[{who: 'consumer'|'bot', text, at}].
"""
from __future__ import annotations

from typing import Optional


# A "turn" is the list-of-dicts shape used everywhere else: {who, text, at}.
Transcript = list[dict[str, str]]


# ---------------------------------------------------------------------------
# Topic label — short headline derived from the first consumer turn
# ---------------------------------------------------------------------------
#
# The headline goes into the ChatbotPreviewView list ("How does Universal
# Document Extractor work?"). Owners scan the list looking for patterns; a
# verbatim consumer question is the most useful pattern-spotter we have.

_TOPIC_MAX_CHARS = 90


def extract_topic_label(transcript: Transcript) -> str:
    """Return a short headline derived from the first consumer turn.

    Heuristic: pull the first consumer message, collapse whitespace, truncate
    at _TOPIC_MAX_CHARS with an ellipsis. If the consumer never spoke (rare —
    a bot-initiated session that ended before any consumer reply), return a
    neutral placeholder. We deliberately don't try to clean punctuation: the
    raw "?" or "!" at the end carries useful information about what the
    consumer was doing (asking vs. exclaiming).
    """
    first_consumer = next(
        (t.get("text", "") for t in transcript if t.get("who") == "consumer"),
        "",
    )
    normalized = " ".join(first_consumer.split())
    if not normalized:
        return "(no consumer messages)"
    if len(normalized) > _TOPIC_MAX_CHARS:
        return normalized[: _TOPIC_MAX_CHARS - 1].rstrip() + "…"
    return normalized


# ---------------------------------------------------------------------------
# Sentiment — keyword tally over consumer turns
# ---------------------------------------------------------------------------
#
# Only the consumer turns count. The bot's text is full of cheerful CS-rep
# language that would skew everything to positive.

_POSITIVE_MARKERS = (
    "thanks", "thank you", "great", "perfect", "exactly", "love",
    "awesome", "amazing", "helpful", "appreciate", "sounds good",
    "works for me", "i'll sign up", "i'll try", "let me try",
    "that works", "yes please", "needed", "got it", "makes sense",
    "perfect — that's", "that's what i needed",
)

_NEGATIVE_MARKERS = (
    "frustrated", "doesn't work", "broken", "garbage", "useless",
    "refund", "cancel", "disappointed", "terrible", "not happy",
    "annoyed", "ridiculous", "waste of", "this is bad", "complain",
    "complaint", "horrible", "awful", "scam", "rip-off", "ripoff",
    "doesn't help", "not helpful", "i hate", "won't work",
)


def score_sentiment(transcript: Transcript) -> str:
    """Classify the consumer's overall tone across the session.

    Returns one of: 'positive', 'neutral', 'negative', 'mixed'.

    Tally positive vs. negative markers across consumer turns. If both
    fire, the consumer was conflicted → 'mixed'. If neither fires, the
    session was a neutral information exchange → 'neutral'. Otherwise
    whichever side has more hits wins.
    """
    consumer_text = " ".join(
        (t.get("text", "") or "").lower()
        for t in transcript
        if t.get("who") == "consumer"
    )
    if not consumer_text:
        return "neutral"
    pos_hits = sum(1 for m in _POSITIVE_MARKERS if m in consumer_text)
    neg_hits = sum(1 for m in _NEGATIVE_MARKERS if m in consumer_text)
    if pos_hits and neg_hits:
        return "mixed"
    if not pos_hits and not neg_hits:
        return "neutral"
    return "positive" if pos_hits >= neg_hits else "negative"


# ---------------------------------------------------------------------------
# Escalation detection — the high-leverage Trevor-input function
# ---------------------------------------------------------------------------
#
# This determines which conversations fire the amber "Escalate" badge in the
# dashboard list. Get it right and owners trust the badge → it surfaces real
# leads + coverage gaps. Get it wrong and they tune out → real leads ghost.
#
# Signals we care about (from looking at the seeded fixtures + Trevor's
# pilot conversations with publishers):
#
#   HIGH-VALUE LEAD: multi-paper group, EDU / journalism school, enterprise
#       account, bulk pricing inquiry, year-up-front discount ask, "connect
#       me to the owner."
#
#   COVERAGE GAP / PRODUCT FEEDBACK: undocumented feature, feature on roadmap
#       but not shipped, integration we don't have ("does it work with X?"),
#       "the bot couldn't answer", "is this even supported."
#
#   CONSUMER REQUESTING HUMAN: "talk to a human", "let me speak to someone",
#       "is anyone available", "I'd like to talk to the owner."
#
#   CHURN / REFUND RISK: "I want to cancel", "I'm thinking of canceling",
#       "this isn't working for us", strong negative sentiment + a specific
#       complaint.
#
#   BOT FAILED: bot replied "I don't have that information" or similar,
#       consumer's last turn went unanswered (consumer spoke last for >30s
#       and bot never replied → unhandled session).
#
# The function returns (escalate: bool, reason: Optional[str]). The reason
# is the human-readable string shown in the dashboard's "Why flagged" line —
# write it like an owner-facing call-out, e.g. "Multi-paper group lead (7
# papers) — high-value prospect."


# Pre-baked phrase tuples Trevor can use — feel free to extend, replace,
# or ignore in favor of more nuanced regex / scoring. Keep the lowercase
# convention so a single .lower() at the top of the function covers
# everything.

ESCALATION_HUMAN_REQUEST = (
    "talk to a human", "speak to someone", "speak with a human",
    "talk to a person", "live agent", "real person", "real human",
    "talk to the owner", "speak to the owner", "talk to trevor",
    "human support", "have someone reach out", "have someone call",
    "have someone email me", "i'd like to talk", "owner outreach",
)

ESCALATION_HIGH_VALUE_LEAD = (
    "multi-paper", "multiple papers", "group of", "our group",
    "journalism school", "journalism program", "university",
    "academic license", "institutional license", "enterprise",
    "bulk", "volume discount", "year up front", "yearly contract",
    "annual contract", "year-up-front", "site license",
    "70 papers", "more than 10",  # tweak as needed
)

ESCALATION_CHURN_RISK = (
    "i want to cancel", "thinking of cancel", "cancel my account",
    "want a refund", "want my money back", "not working for us",
    "not what i expected", "switching to", "moving to a competitor",
    "looking at alternatives",
)

ESCALATION_COVERAGE_GAP = (
    "does it work with", "do you integrate with",
    "is there an integration", "is that supported",
    "i don't see that", "it doesn't say", "couldn't find",
    "not documented", "where is the docs", "where are the docs",
    "i wish you", "would be nice if",
)

# The bot itself signaling it can't answer is a strong escalation signal —
# even if the consumer didn't ask for a human, the bot's "I don't know"
# is a coverage gap the owner should see.
ESCALATION_BOT_GAVE_UP = (
    "i don't have that information", "i don't know",
    "i'm not able to answer", "i can't help with that",
    "outside of what i can help with", "you'd need to contact",
    "let me connect you", "i'll flag your conversation",
)


def detect_escalation(transcript: Transcript) -> tuple[bool, Optional[str]]:
    """Return (escalate: bool, owner-facing reason: Optional[str]).

    TODO Trevor: write the body. Below is a starter sketch — feel free to
    replace it wholesale. The five categories above (HUMAN_REQUEST,
    HIGH_VALUE_LEAD, CHURN_RISK, COVERAGE_GAP, BOT_GAVE_UP) are the
    signals we extracted from the fixture corpus and from your pilot
    publisher conversations. Stack rank matters — if a session hits both
    HIGH_VALUE_LEAD and BOT_GAVE_UP, the lead is the headline.

    Examples of expected output (from the existing fixtures):

      "Our group runs 7 weekly papers..."
        → (True, "Multi-paper group lead (7 papers) — high-value prospect")

      "I run a journalism program at a state university — about 80 students"
        → (True, "Journalism program lead (~80 seats) — institutional license")

      "Does the document extractor output land directly in InDesign?"
        → (True, "Coverage gap: InDesign export — feature exists but undocumented")

      "Honestly — why pay you when ChatGPT is free?"
        → (False, None)  # negative-leaning sentiment but no actionable lead

      "What's a typical false-positive rate?"
        → (False, None)  # neutral information exchange, no escalation
    """
    # ↓↓↓ Trevor — your rules go here. Replace this whole block. ↓↓↓
    # Below is a minimal placeholder so the function returns something
    # sensible while you write the real logic. Remove or rewrite freely.
    consumer_text = " ".join(
        (t.get("text", "") or "").lower()
        for t in transcript
        if t.get("who") == "consumer"
    )
    if any(p in consumer_text for p in ESCALATION_HUMAN_REQUEST):
        return True, "Consumer asked to speak with a human"
    return False, None
    # ↑↑↑ Trevor — your rules go here. ↑↑↑
