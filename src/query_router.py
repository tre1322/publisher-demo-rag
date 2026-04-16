"""Query intent router — decides which corpora to retrieve from.

The v1 pipeline merged article chunks, ad chunks, directory entries, events,
and sponsored answers into ONE evidence bag for every query, regardless of
what the user was actually asking. That was the architectural smell the
external reviewers flagged: it maximizes the chance the LLM will confidently
stitch together a wrong answer from semantically-nearby-but-different corpora.

The router classifies each query into a small number of intents and tells
the caller which retrieval lanes to run. Cheap keyword/regex classifier for
now — Claude-based classification is overkill for the signal quality we need.

Intent contract (strings, not enums, so they round-trip through JSON logs):
    article_qa          Default. Questions about news, people, events.
                        Retrieve: articles (+ sponsored for rev loop).
                        Skip: ads, directory, events (noise for fact QA).
    business_lookup     "Who sells X?", "where can I buy Y?", "is there a
                        restaurant in Windom?"
                        Retrieve: directory, ads, sponsored.
                        Skip: articles.
    event_lookup        "What's happening this weekend?", "any concerts?",
                        "upcoming festivals"
                        Retrieve: events (+ articles for feature stories).
    current_edition     "What's in this week's paper?", "feature story",
                        "latest news"
                        Retrieve: articles filtered to current-edition
                        chunks specifically. (This is what replaces the old
                        multiplicative current-edition boost.)
    out_of_scope        "What's the weather?", "stock price", "recipe for X",
                        "define Y"
                        Abstain immediately — don't even retrieve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

Intent = str  # "article_qa" | "business_lookup" | "event_lookup" | "current_edition" | "out_of_scope"


@dataclass
class RouteDecision:
    intent: Intent
    reason: str              # short human-readable why (goes to logs)
    use_articles: bool
    use_ads_directory: bool
    use_events: bool
    use_sponsored: bool      # load-bearing revenue loop — stays true for most intents
    current_edition_only: bool  # narrow articles retrieval to current-edition IDs
    abstain_message: str | None  # set iff intent == out_of_scope


# Out-of-scope topics — things a small-town paper RAG genuinely can't answer,
# and where the LLM's general knowledge is the MOST dangerous (confident wrong
# answers about weather, stock prices, recipes, etc.).
_OUT_OF_SCOPE_PATTERNS = [
    (re.compile(r"\b(weather|forecast|temperature|rain|snow|wind|storm)\b", re.I),
     "weather/forecast queries are not in the article archive"),
    (re.compile(r"\bstock\s+(price|quote|market)\b|\bnasdaq\b|\bdow\s+jones\b", re.I),
     "stock prices are not in the article archive"),
    (re.compile(r"\brecipe\b|\bhow\s+do\s+I\s+(make|cook|bake)\b", re.I),
     "recipes/how-to cooking are not in the article archive"),
    (re.compile(r"\bdefine\b|\bwhat\s+does\s+\w+\s+mean\b", re.I),
     "dictionary definitions are not in the article archive"),
    # Pro-league scores — the local paper doesn't cover them. High school /
    # college local teams still go through article_qa.
    (re.compile(r"\b(nfl|nba|mlb|nhl|premier\s+league|world\s+cup)\b", re.I),
     "professional league scores are not in the local archive"),
]

_BUSINESS_PATTERNS = [
    re.compile(r"\bwho\s+sells\b", re.I),
    re.compile(r"\bwhere\s+can\s+i\s+(buy|get|find|purchase)\b", re.I),
    re.compile(r"\bis\s+there\s+a\s+\w+\s+(shop|store|restaurant|business|place)\b", re.I),
    re.compile(r"\b(restaurant|store|shop|business|service)s?\s+(in|near|around)\b", re.I),
    re.compile(r"\bany\s+(good|open)\s+\w+\s+(shops?|stores?|restaurants?|places?)\b", re.I),
    re.compile(r"\bon\s+sale\b|\bdiscount\b|\bcoupon\b", re.I),
    # "I need a/an X" — most common service-request phrasing. Typo-independent.
    re.compile(r"\bi\s+need\s+(a|an|some|the)\b", re.I),
    # "where do I go for/to" — directional service lookup.
    re.compile(r"\bwhere\s+do\s+i\s+go\s+(for|to)\b", re.I),
    # Bare trade/profession nouns — imply need for a service provider.
    re.compile(
        r"\b(electrician|plumber|contractor|mechanic|doctor|dentist|"
        r"lawyer|attorney|roofer|painter|landscaper|vet|veterinarian|"
        r"pharmacist|barber|stylist|tailor|accountant|realtor|handyman|"
        r"carpenter|welder|hvac|chiropractor|optometrist)\b",
        re.I,
    ),
    # Help-wanted / job-seeker phrasings — surface help-wanted ads.
    # Covers: "who hiring", "who is hiring", "who's hiring", "whos hiring",
    # "anyone hiring", "hiring in/at/near/for/now", "now hiring",
    # "help wanted", "looking to hire", "job opening(s)", "open positions".
    re.compile(
        r"\b(who(?:\s+is|'?s)?\s+hiring|"
        r"anyone\s+hiring|"
        r"hiring\s+(?:in|at|near|for|now)|"
        r"now\s+hiring|"
        r"help\s+wanted|"
        r"looking\s+to\s+hire|"
        r"job\s+openings?|"
        r"open\s+positions?)\b",
        re.I,
    ),
    # "jobs/work/employment" + optional "are/is" + preposition/adjective.
    # Catches "jobs available", "jobs ARE available", "jobs in X", etc.
    re.compile(r"\b(jobs?|work|employment)\s+(?:(?:are|is)\s+)?(in|at|near|available|open)\b", re.I),
    # "What jobs/positions/openings..." — question form.
    re.compile(r"\bwhat\s+(jobs?|positions?|openings?)\b", re.I),
    re.compile(r"\bi\s+need\s+(a\s+)?job\b", re.I),
    # Service verbs — imply need for a service provider.
    re.compile(r"\b(fix|repair|install)\s+(my|the|a|an)\b", re.I),
]

_EVENT_PATTERNS = [
    re.compile(r"\b(happening|going\s+on)\s+(this\s+weekend|tonight|tomorrow|today|next\s+week)\b", re.I),
    re.compile(r"\bevents?\b.*\b(this|next|upcoming)\b", re.I),
    re.compile(r"\b(concerts?|festivals?|shows?|performances?|parades?)\b", re.I),
    re.compile(r"\bcalendar\b", re.I),
    re.compile(r"\bwhat'?s\s+happening\b", re.I),
]

_CURRENT_EDITION_PATTERNS = [
    re.compile(r"\bthis\s+week'?s?\s+(paper|edition|issue|feature|story|news)\b", re.I),
    re.compile(r"\bfeature\s+story\b", re.I),
    re.compile(r"\blatest\s+(news|edition|issue)\b", re.I),
    re.compile(r"\bcurrent\s+(edition|issue|week)\b", re.I),
    re.compile(r"\bwhat'?s\s+in\s+the\s+(paper|edition|issue)\b", re.I),
    re.compile(r"\bheadlines?\s+(this\s+week|today)\b", re.I),
]


def classify(query: str) -> RouteDecision:
    """Run the classifier. Returns a route decision with retrieval lane flags.

    Precedence (first match wins):
        out_of_scope > business_lookup > event_lookup > current_edition > article_qa
    """
    q = (query or "").strip()
    if not q:
        return RouteDecision(
            intent="article_qa", reason="empty query defaults to article_qa",
            use_articles=True, use_ads_directory=False, use_events=False,
            use_sponsored=True, current_edition_only=False, abstain_message=None,
        )

    # 1. Out of scope — short-circuit before any retrieval.
    for pat, reason in _OUT_OF_SCOPE_PATTERNS:
        if pat.search(q):
            return RouteDecision(
                intent="out_of_scope", reason=reason,
                use_articles=False, use_ads_directory=False, use_events=False,
                use_sponsored=False, current_edition_only=False,
                abstain_message=(
                    f"That's not something I can answer from the local news "
                    f"archive — {reason}. Ask me about a person, event, "
                    f"business, or story from the paper and I can help."
                ),
            )

    # 2. Business lookup → directory + ads, skip articles to avoid noise.
    for pat in _BUSINESS_PATTERNS:
        if pat.search(q):
            return RouteDecision(
                intent="business_lookup", reason=f"matched {pat.pattern!r}",
                use_articles=False, use_ads_directory=True, use_events=False,
                use_sponsored=True, current_edition_only=False, abstain_message=None,
            )

    # 3. Event lookup → events primary, articles for context, no ads/directory.
    for pat in _EVENT_PATTERNS:
        if pat.search(q):
            return RouteDecision(
                intent="event_lookup", reason=f"matched {pat.pattern!r}",
                use_articles=True, use_ads_directory=False, use_events=True,
                use_sponsored=True, current_edition_only=False, abstain_message=None,
            )

    # 4. Current edition — broad "what's in this week" queries. Retrieve
    #    articles but restrict to current-edition IDs. This is the quota-
    #    shaped replacement for the old multiplicative boost.
    for pat in _CURRENT_EDITION_PATTERNS:
        if pat.search(q):
            return RouteDecision(
                intent="current_edition", reason=f"matched {pat.pattern!r}",
                use_articles=True, use_ads_directory=False, use_events=False,
                use_sponsored=True, current_edition_only=True, abstain_message=None,
            )

    # 5. Default: article QA.
    return RouteDecision(
        intent="article_qa", reason="default (no intent pattern matched)",
        use_articles=True, use_ads_directory=False, use_events=False,
        use_sponsored=True, current_edition_only=False, abstain_message=None,
    )
