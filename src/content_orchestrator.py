"""Content orchestrator — routes queries to the right domain search.

Uses the intent router to decide whether to search articles, ads, or both,
then merges and ranks results into a unified list for the LLM context.
"""

import logging

from src.intent_router import (
    AD_BUSINESS,
    ARTICLE_NEWS,
    MIXED_DISCOVERY,
    classify_intent,
)
from src.search_tools import SearchTools

logger = logging.getLogger(__name__)


class ContentOrchestrator:
    """Routes queries to article and/or ad search based on detected intent."""

    def __init__(self) -> None:
        self.tools = SearchTools()
        logger.info("ContentOrchestrator initialized")

    def search(self, query: str) -> list[dict]:
        """Search across content domains based on query intent.

        Args:
            query: User's search query.

        Returns:
            Merged, ranked list of result dicts.
        """
        intent = classify_intent(query)

        results: list[dict] = []

        if intent == AD_BUSINESS:
            # Ads first, then articles as supplement
            results.extend(self._search_ads(query))
            results.extend(self._search_articles(query, top_k=3))
            results.extend(self._search_events(query))

        elif intent == ARTICLE_NEWS:
            # Articles first, ads as supplement
            results.extend(self._search_articles(query))
            results.extend(self._search_ads(query))
            results.extend(self._search_events(query))

        else:  # MIXED_DISCOVERY
            # Search everything equally
            results.extend(self._search_articles(query))
            results.extend(self._search_ads(query))
            results.extend(self._search_events(query))

        # Deduplicate by text hash
        seen = set()
        unique = []
        for r in results:
            h = hash(r.get("text", ""))
            if h not in seen:
                seen.add(h)
                unique.append(r)

        # Sort by score descending
        unique.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(
            f"Orchestrator: intent={intent}, "
            f"total={len(unique)} results (deduped from {len(results)})"
        )

        # Log top results
        for i, r in enumerate(unique[:5]):
            rtype = r.get("search_type", "unknown")
            title = r.get("metadata", {}).get("title", "")[:50]
            score = r.get("score", 0)
            logger.info(f"  Result {i+1}: [{rtype}] {title} (score={score:.2f})")

        return unique

    def _search_articles(self, query: str, top_k: int = 5) -> list[dict]:
        """Search article domain."""
        try:
            results = self.tools.hybrid_search(query=query)
            logger.info(
                f"  Articles search: {len(results)} results "
                f"(collection: articles)"
            )
            return results
        except Exception as e:
            logger.error(f"Article search failed: {e}")
            return []

    def _search_ads(self, query: str) -> list[dict]:
        """Search advertisement domain."""
        try:
            results = self.tools.search_advertisements(query=query)
            logger.info(
                f"  Ads search: {len(results)} results "
                f"(source: DB + name match)"
            )
            return results
        except Exception as e:
            logger.error(f"Ad search failed: {e}")
            return []

    def _search_events(self, query: str) -> list[dict]:
        """Search events domain."""
        try:
            results = self.tools.search_events(query=query)
            logger.info(f"  Events search: {len(results)} results")
            return results
        except Exception as e:
            logger.error(f"Event search failed: {e}")
            return []
