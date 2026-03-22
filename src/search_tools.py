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
    ) -> list[dict]:
        """Search for articles using semantic similarity."""
        return self.articles.semantic_search(query, top_k)

    def metadata_search(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        author: str | None = None,
        location: str | None = None,
        subject: str | None = None,
    ) -> list[dict]:
        """Search for articles by metadata filters."""
        return self.articles.metadata_search(
            date_from=date_from,
            date_to=date_to,
            author=author,
            location=location,
            subject=subject,
        )

    def hybrid_search(
        self,
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        location: str | None = None,
        subject: str | None = None,
    ) -> list[dict]:
        """Combine semantic search with metadata filtering."""
        return self.articles.hybrid_search(
            query=query,
            date_from=date_from,
            date_to=date_to,
            location=location,
            subject=subject,
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
    ) -> list[dict]:
        """Search for advertisements."""
        return self.advertisements.search(
            query=query,
            category=category,
            max_price=max_price,
            on_sale_only=on_sale_only,
        )

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


def get_search_tools_schema() -> list[dict]:
    """Get combined search tools schema with dynamic categories.

    Returns:
        List of all tool definitions with actual category/subject/location values from database.
    """
    return (
        get_article_tools_schema()
        + get_ad_tools_schema()
        + get_event_tools_schema()
        + [DATABASE_INFO_SCHEMA]
    )


# Keep static schema for backward compatibility
SEARCH_TOOLS_SCHEMA = get_search_tools_schema()
