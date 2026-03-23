"""Lightweight query intent router.

Classifies user queries into content domain intents so the orchestrator
knows which search domains to prioritize.

Intents:
- article_news: News, politics, sports, weather, editorial content
- advertisement_business: Specific business, product, ad, deal, promotion
- mixed_local_discovery: General local discovery (events, shopping, "what's happening")
"""

import logging
import re

logger = logging.getLogger(__name__)

# Intent constants
ARTICLE_NEWS = "article_news"
AD_BUSINESS = "advertisement_business"
MIXED_DISCOVERY = "mixed_local_discovery"

# ── Keyword patterns ────────────────────────────────────────────────────

_AD_SIGNALS = [
    r"\badvertis",
    r"\bpromot",
    r"\bsponsor",
    r"\bdeal[s]?\b",
    r"\bon sale\b",
    r"\bdiscount",
    r"\bcoupon",
    r"\bfor sale\b",
    r"\bhomes? for sale\b",
    r"\blisting[s]?\b",
    r"\bwhere can i (?:get|buy|find)\b",
    r"\bwho (?:sells|offers|provides)\b",
    r"\bwhat is .+ (?:advertising|promoting|offering)\b",
    r"\bwhat (?:ads|advertisements)\b",
    r"\bbusiness(?:es)?\b",
    r"\bstore[s]?\b",
    r"\bshop[s]?\b",
    r"\brestaurant[s]?\b",
    r"\btheater\b",
    r"\btheatre\b",
    r"\bgreen\s?house\b",
    r"\brealt[yi]\b",
    r"\bclinic\b",
    r"\bpharmacy\b",
]

_NEWS_SIGNALS = [
    r"\bnews\b",
    r"\barticle[s]?\b",
    r"\breport(?:ed|s|ing)?\b",
    r"\bcity council\b",
    r"\bschool board\b",
    r"\bcounty\b",
    r"\belection\b",
    r"\bvote[ds]?\b",
    r"\blegislat",
    r"\bbudget\b",
    r"\bmeeting\b",
    r"\bfire\b",
    r"\baccident\b",
    r"\bcrime\b",
    r"\bpolice\b",
    r"\bcourt\b",
    r"\bsentenc",
    r"\barrest",
    r"\beditorial[s]?\b",
    r"\bopinion\b",
    r"\bletter to the editor\b",
    r"\bsports?\b",
    r"\bgame[s]?\b",
    r"\bseason\b",
    r"\bweather\b",
    r"\bforecast\b",
    r"\bobituar",
]

_MIXED_SIGNALS = [
    r"\bwhat'?s happening\b",
    r"\bthis weekend\b",
    r"\bthings to do\b",
    r"\blocal\b",
    r"\bin (?:windom|pipestone|st\.?\s*james|jackson|worthington)\b",
    r"\bevent[s]?\b",
    r"\bfestival\b",
    r"\bconcert\b",
    r"\bfair\b",
]


def classify_intent(query: str) -> str:
    """Classify a user query into a content domain intent.

    Args:
        query: The user's search query.

    Returns:
        One of: ARTICLE_NEWS, AD_BUSINESS, MIXED_DISCOVERY.
    """
    q = query.lower().strip()

    ad_score = sum(1 for p in _AD_SIGNALS if re.search(p, q))
    news_score = sum(1 for p in _NEWS_SIGNALS if re.search(p, q))
    mixed_score = sum(1 for p in _MIXED_SIGNALS if re.search(p, q))

    # Strong signal wins; ad and news take priority over mixed on ties
    if ad_score > news_score and ad_score >= mixed_score and ad_score > 0:
        intent = AD_BUSINESS
    elif news_score > ad_score and news_score >= mixed_score and news_score > 0:
        intent = ARTICLE_NEWS
    elif mixed_score > ad_score and mixed_score > news_score:
        intent = MIXED_DISCOVERY
    elif ad_score > 0:
        intent = AD_BUSINESS
    elif news_score > 0:
        intent = ARTICLE_NEWS
    elif mixed_score > 0:
        intent = MIXED_DISCOVERY
    else:
        # Default to mixed for ambiguous queries
        intent = MIXED_DISCOVERY

    logger.info(
        f"Intent: '{intent}' for query '{query}' "
        f"(ad={ad_score}, news={news_score}, mixed={mixed_score})"
    )
    return intent
