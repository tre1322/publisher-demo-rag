"""Entity coverage gate — the structural fix for "confabulation via adjacent chunk".

The v1 system would retrieve semantically-close-but-wrong chunks (e.g. a different
wrestler's article when the user asked about Koerner) and let Claude summarize them
as if they answered the question. The v2 fix is a pre-LLM short-circuit:

    1. Extract proper nouns from the user's query.
    2. Pick the longest one (heuristic: usually the surname / specific entity).
    3. Check whether ANY retrieved chunk contains that token (case-insensitive,
       word-boundary match) in its text OR title.
    4. If none do, skip the LLM call entirely and return a canned abstention.

This is Option C from the Phase 1a decision tree. Rationale:
    - A last-name heuristic matches how newspaper archives are actually indexed.
    - Short first names alone (Kyle, Amy, Ted) are too ambiguous to gate on.
    - Requiring ALL proper nouns would break on headline/body chunk splits where
      the first name is in the title metadata and the last name is in a
      different chunk of body text.
    - Requiring ANY proper noun is too loose: "the mayor of Springfield said
      Koerner" counts as Koerner coverage for a Koerner question, which is
      exactly the adjacent-chunk failure we're trying to prevent.

If the query has no proper nouns (e.g. "what's in the paper this week?"), the
gate is a no-op — broad-query handling is the intent router's problem (Phase 2c).
"""

from __future__ import annotations

import re

# Capitalized question words and common English tokens that match the proper-noun
# regex but are not actually names/places. Everything is stored lowercased.
_PROPER_NOUN_STOP = {
    # Interrogatives, demonstratives, modals
    "the", "what", "when", "where", "who", "why", "how", "did", "do", "does",
    "is", "are", "was", "were", "will", "would", "could", "should", "can",
    "any", "all", "some", "this", "that", "these", "those", "there",
    # Common verbs / request openers
    "tell", "show", "find", "give", "help", "please", "news", "article", "articles",
    # News-domain vocabulary the user uses to frame queries (not subjects)
    "edition", "editions", "recent", "latest", "current", "past",
    # Days / months
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    # Publisher display-string tokens — every article is tagged with these,
    # so they can't be the *entity* a user is asking about.
    "cottonwood", "citizen", "pipestone", "county", "star",
    # The platform itself
    "grand", "network",
    # Sentence-starters and connective tokens the LLM emits in clean prose.
    # These match the regex (capitalized, 3+ letters) but are not entities.
    "according", "based", "following", "here", "there", "yes",
    "however", "therefore", "moreover", "although", "while", "since",
    "despite", "unless", "until", "during", "within", "without",
    "before", "after", "above", "below", "through", "around",
    "also", "additionally", "furthermore", "finally", "lastly",
    "first", "second", "third", "fourth", "fifth",
    "yesterday", "today", "tomorrow", "tonight", "weekend", "week", "year",
    "note", "unfortunately", "fortunately", "actually", "really",
    "many", "more", "most", "much", "several", "various",
    "their", "they", "them", "your", "yours", "ours", "mine",
    "answer", "question", "information", "source", "sources",
    "both", "either", "neither",
}

# Proper-noun candidate: capitalized word, at least 3 letters. Excludes
# all-caps acronyms (those are often boilerplate like "WAS", "BSC", "EMAIL").
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")


def extract_proper_nouns(query: str) -> list[str]:
    """Return proper-noun candidates from `query` in their original order.

    Preserves casing. Filters via _PROPER_NOUN_STOP (lowercased comparison).
    """
    if not query:
        return []
    hits = _PROPER_NOUN_RE.findall(query)
    return [h for h in hits if h.lower() not in _PROPER_NOUN_STOP]


def longest_proper_noun(query: str) -> str | None:
    """Pick the 'gate token' for entity-coverage checking (Option C).

    Strategy: longest proper noun wins; on length ties, the *last* one wins —
    surnames come after first names in English, and this matches how you'd
    look someone up in an archive.
    """
    nouns = extract_proper_nouns(query)
    if not nouns:
        return None
    # Stable sort: longest first, then last-occurrence first (by reversing the
    # enumeration before the sort so later items win ties).
    indexed = list(enumerate(nouns))
    indexed.sort(key=lambda ix: (-len(ix[1]), -ix[0]))
    return indexed[0][1]


def _chunk_haystack(chunk: dict) -> str:
    """Combined text blob to search — chunk body plus all metadata fields a
    name might reasonably live in (title/author/advertiser/business/location)."""
    md = chunk.get("metadata", {}) or {}
    parts = [
        str(chunk.get("text", "") or ""),
        str(md.get("title", "") or ""),
        str(md.get("author", "") or ""),
        str(md.get("advertiser", "") or ""),
        str(md.get("product_name", "") or ""),
        str(md.get("location", "") or ""),
    ]
    return " ".join(parts)


def _contains_token(haystack: str, token: str) -> bool:
    """Case-insensitive whole-word match. Handles apostrophes and hyphens around
    the token as word boundaries (re.escape keeps the token literal)."""
    if not haystack or not token:
        return False
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    return pattern.search(haystack) is not None


def has_entity_coverage(
    query: str, chunks: list[dict]
) -> tuple[bool, str | None]:
    """Return (ok, missing_token).

    - ok=True, missing=None     — gate does not apply (no proper nouns) OR
                                   the gate token appears in at least one chunk.
    - ok=False, missing="Koerner" — gate fires; caller should abstain
                                    without calling the LLM.
    """
    token = longest_proper_noun(query)
    if token is None:
        return True, None  # Gate is a no-op for broad queries.
    if not chunks:
        return False, token
    for c in chunks:
        if _contains_token(_chunk_haystack(c), token):
            return True, None
    return False, token


def validate_response_grounding(
    response: str, chunks: list[dict]
) -> dict:
    """Post-generation sanity check: does every proper noun in the response
    appear in at least one retrieved chunk?

    Phase 2b runs this in *observability mode* — it reports findings but does
    not modify the response. Rationale: stripping sentences based on proper-
    noun mismatch has high false-positive risk (a legitimately-reasoned
    inference like 'the tournament was held in Minneapolis' would be
    false-flagged if the chunks only mention 'the state tournament').

    Returns:
        {
            "ok": bool,                  # True iff no unverifiable nouns found
            "response_nouns": [...],     # proper nouns in the response
            "chunk_nouns": [...],        # proper nouns across all chunks
            "unverified": [...],         # nouns in response NOT in any chunk
        }

    Callers may choose to:
        - log and continue (current default)
        - append a disclaimer
        - strip offending sentences
        - re-prompt the LLM
    """
    response_nouns = extract_proper_nouns(response)
    chunk_blob_lower = " ".join(_chunk_haystack(c) for c in chunks).lower()
    unverified = []
    for noun in response_nouns:
        if noun.lower() in chunk_blob_lower:
            continue
        unverified.append(noun)
    chunk_nouns: list[str] = []
    seen = set()
    for c in chunks:
        for n in extract_proper_nouns(_chunk_haystack(c)):
            if n.lower() not in seen:
                seen.add(n.lower())
                chunk_nouns.append(n)
    return {
        "ok": len(unverified) == 0,
        "response_nouns": response_nouns,
        "chunk_nouns": chunk_nouns,
        "unverified": unverified,
    }


def abstention_message(missing_token: str, publisher_name: str | None) -> str:
    """Canned response when the gate fires. Varied naturally."""
    # Cheap rotation by hashing — deterministic per token, so the same query
    # always gets the same phrasing within a single session.
    variants = [
        f"I don't have any articles mentioning {missing_token} in the {publisher_name or 'archive'} editions I have access to. Want me to check the Grand Network?",
        f"Nothing in the {publisher_name or 'archive'} I can see covers {missing_token}. Would you like me to search the wider Grand Network?",
        f"The {publisher_name or 'archive'} editions I have don't mention {missing_token}. I can check other papers in the Grand Network if you'd like.",
    ]
    idx = abs(hash(missing_token)) % len(variants)
    return variants[idx]
