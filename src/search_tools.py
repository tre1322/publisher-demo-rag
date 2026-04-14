"""Search tools aggregator - combines all module search functionality."""

import logging

from src.core.database import get_all_publishers
from src.modules.advertisements import get_advertisement_count
from src.modules.advertisements.search import (
    AdvertisementSearch,
    get_ad_tools_schema,
)
from src.modules.articles import get_article_count
from src.modules.articles.search import ArticleSearch, get_article_tools_schema
from src.modules.events import get_event_count
from src.modules.events.search import EventSearch, get_event_tools_schema

logger = logging.getLogger(__name__)


class SearchTools:
    """Aggregates search tools from all modules."""

    def __init__(self) -> None:
        """Initialize all search tools."""
        self.articles = ArticleSearch()
        self.advertisements = AdvertisementSearch()
        self.events = EventSearch()

    # Article search methods
    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for articles using semantic similarity."""
        return self.articles.semantic_search(query, top_k, publisher=publisher)

    def metadata_search(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        author: str | None = None,
        location: str | None = None,
        subject: str | None = None,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for articles by metadata filters."""
        return self.articles.metadata_search(
            date_from=date_from,
            date_to=date_to,
            author=author,
            location=location,
            subject=subject,
            publisher=publisher,
        )

    def hybrid_search(
        self,
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        location: str | None = None,
        subject: str | None = None,
        publisher: str | None = None,
    ) -> list[dict]:
        """Combine semantic search with metadata filtering."""
        return self.articles.hybrid_search(
            query=query,
            date_from=date_from,
            date_to=date_to,
            location=location,
            subject=subject,
            publisher=publisher,
        )

    def historical_search(
        self,
        query: str,
        top_k: int = 3,
        publisher: str | None = None,
    ) -> list[dict]:
        """Semantic search restricted to past editions (excludes the current one)."""
        return self.articles.historical_search(
            query=query,
            top_k=top_k,
            publisher=publisher,
        )

    def get_chunks_for_article(self, doc_id: str) -> list[dict]:
        """Get all chunks for a specific article."""
        return self.articles.get_chunks_for_article(doc_id)

    # Advertisement search methods
    def search_advertisements(
        self,
        query: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        on_sale_only: bool = False,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for advertisements."""
        return self.advertisements.search(
            query=query,
            category=category,
            max_price=max_price,
            on_sale_only=on_sale_only,
            publisher=publisher,
        )

    def search_directory(
        self,
        query: str | None = None,
        category: str | None = None,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search the business directory for local businesses.

        Returns businesses from the organizations table, scored lower
        than active ads so advertisers get priority.
        """
        from src.core.database import get_connection

        conn = get_connection()
        conn.row_factory = lambda c, r: dict(zip([col[0] for col in c.description], r))
        cursor = conn.cursor()

        sql = "SELECT * FROM organizations WHERE 1=1"
        params: list = []

        if publisher:
            sql += " AND publisher = ?"
            params.append(publisher)
        if category:
            sql += " AND category LIKE ?"
            params.append(f"%{category}%")
        if query:
            # Extract meaningful words from the query (skip common stop words)
            stop_words = {
                "i",
                "me",
                "my",
                "we",
                "our",
                "you",
                "your",
                "a",
                "an",
                "the",
                "is",
                "are",
                "was",
                "were",
                "be",
                "been",
                "being",
                "have",
                "has",
                "had",
                "do",
                "does",
                "did",
                "will",
                "would",
                "could",
                "should",
                "can",
                "may",
                "might",
                "shall",
                "need",
                "want",
                "like",
                "get",
                "got",
                "some",
                "any",
                "where",
                "what",
                "which",
                "who",
                "how",
                "that",
                "this",
                "those",
                "these",
                "there",
                "here",
                "to",
                "for",
                "of",
                "in",
                "on",
                "at",
                "by",
                "with",
                "from",
                "about",
                "into",
                "and",
                "or",
                "but",
                "not",
                "no",
                "so",
                "if",
                "then",
                "than",
                "very",
                "just",
                "also",
                "too",
                "up",
                "out",
                "it",
                "its",
            }
            import re as _re

            words = [
                w
                for w in _re.findall(r"[a-zA-Z]+", query.lower())
                if w not in stop_words and len(w) > 2
            ]
            if words:
                # Match ANY keyword against name, description, services, or keywords
                word_clauses = []
                for word in words:
                    word_clauses.append(
                        "(name LIKE ? OR description LIKE ? OR services LIKE ? OR keywords LIKE ?)"
                    )
                    params.extend([f"%{word}%"] * 4)
                sql += " AND (" + " OR ".join(word_clauses) + ")"

        sql += " ORDER BY last_advertised_at DESC NULLS LAST LIMIT 10"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        results = []
        for org in rows:
            name = org.get("name", "")
            city = org.get("city", "")
            state = org.get("state", "")
            phone = org.get("phone", "")
            website = org.get("website", "")
            desc = org.get("description", "")
            services = org.get("services", "")

            text_parts = [f"Local Business: {name}"]
            if desc:
                text_parts.append(desc)
            if services:
                text_parts.append(f"Services: {services}")
            if city:
                text_parts.append(f"Location: {city}, {state}")
            if phone:
                text_parts.append(f"Phone: {phone}")
            if website:
                text_parts.append(f"Website: {website}")

            results.append(
                {
                    "text": "\n".join(text_parts),
                    "metadata": {
                        "doc_id": f"dir_{org.get('id', '')}",
                        "title": name,
                        "advertiser": name,
                        "category": org.get("category", ""),
                        "location": f"{city}, {state}" if city else "",
                        "url": f"/business/{org.get('id', '')}",
                        "content_type": "directory",
                    },
                    "score": 0.5,  # Lower than active ads (which get 1.0+)
                    "search_type": "directory",
                }
            )

        return results

    # Sponsored answer search (Main Street OS)
    def search_sponsored_answers(
        self,
        query: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """Search sponsored answers by keyword and/or category.

        Uses keyword-first matching (Trevor's pilot choice): any query
        keyword appearing in the sponsored answer's text, category, or
        org name will surface it. Exact category match is also honored.

        Results prefixed with [SPONSORED Answer from ...] so the existing
        disclosure logic in prompts.py handles legal compliance. Each
        returned answer increments its impression counter.
        """
        from src.modules.sponsored.database import (
            find_matching_sponsored,
            increment_impression,
        )

        if not query and not category:
            return []

        results = []
        for s in find_matching_sponsored(query=query, category=category):
            if not increment_impression(s["id"]):
                continue
            phone = s.get("org_phone") or ""
            address = s.get("org_address") or ""
            contact = ""
            if phone:
                contact += f" | Phone: {phone}"
            if address:
                contact += f" | {address}"
            text = (
                f"[SPONSORED Answer from {s['org_name']}]\n"
                f"Category: {s['category']}\n"
                f"{s['answer_text']}{contact}"
            )
            results.append(
                {
                    "text": text,
                    "metadata": {
                        "doc_id": f"sponsored_{s['id']}",
                        "title": f"Sponsored: {s['org_name']}",
                        "advertiser": s["org_name"],
                        "category": s["category"],
                        "content_type": "sponsored_answer",
                        "url": "",
                    },
                    "score": 0.85,
                    "search_type": "advertisement",
                }
            )
        return results

    # Event search methods
    def search_events(
        self,
        query: str | None = None,
        category: str | None = None,
        location: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        max_price: float | None = None,
        free_only: bool = False,
    ) -> list[dict]:
        """Search for local events."""
        return self.events.search(
            query=query,
            category=category,
            location=location,
            date_from=date_from,
            date_to=date_to,
            max_price=max_price,
            free_only=free_only,
        )

    # Database info method
    def get_database_info(self) -> dict:
        """Get metadata about the database including publishers and counts.

        Returns:
            Dictionary with publishers list and content counts.
        """
        return {
            "publishers": get_all_publishers(),
            "article_count": get_article_count(),
            "advertisement_count": get_advertisement_count(),
            "event_count": get_event_count(),
        }


DATABASE_INFO_SCHEMA = {
    "name": "get_database_info",
    "description": "Get information about the database including which publishers/newspapers are available and content counts. Use when user asks about sources, publishers, where information comes from, or what content is available.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


DIRECTORY_SEARCH_SCHEMA = {
    "name": "search_directory",
    "description": "Search the local business directory for businesses by name, services, or category. Returns businesses that have advertised in the newspaper. Use this for questions about local services, stores, restaurants, etc.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — business name, service type, or product",
            },
            "category": {
                "type": "string",
                "description": "Business category filter (e.g., retail, dining, healthcare, automotive)",
            },
        },
    },
}


SPONSORED_ANSWERS_SCHEMA = {
    "name": "search_sponsored_answers",
    "description": (
        "Search for sponsored answers from local businesses. Use when the user "
        "asks about a specific service category (restaurant, retail, automotive, "
        "health, professional, home, agriculture, entertainment, lodging, "
        "financial, education) to surface businesses that have paid to provide "
        "direct answers. These are disclosed as sponsored content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What the user is looking for (used for context only)",
            },
            "category": {
                "type": "string",
                "description": (
                    "Business category. Available: restaurant, retail, automotive, "
                    "health, professional, home, agriculture, entertainment, "
                    "lodging, financial, education, other"
                ),
            },
        },
    },
}


def get_search_tools_schema() -> list[dict]:
    """Get combined search tools schema with dynamic categories.

    Returns:
        List of all tool definitions with actual category/subject/location values from database.
    """
    return (
        get_article_tools_schema()
        + get_ad_tools_schema()
        + [DIRECTORY_SEARCH_SCHEMA]
        + get_event_tools_schema()
        + [SPONSORED_ANSWERS_SCHEMA]
        + [DATABASE_INFO_SCHEMA]
    )


# Keep static schema for backward compatibility
SEARCH_TOOLS_SCHEMA = get_search_tools_schema()
