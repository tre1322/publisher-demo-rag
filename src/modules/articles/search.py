"""Search functionality for articles."""

import logging
from datetime import datetime

from sentence_transformers import SentenceTransformer

from src.core.config import (
    EMBEDDING_MODEL,
    RETRIEVAL_TOP_K,
    SIMILARITY_THRESHOLD,
)

# Score boost for chunks from the current edition. Matches EDITION_CURRENT_BOOST
# in src/query_engine.py — keep both in sync.
EDITION_CURRENT_BOOST = 1.5
from src.core.vector_store import get_articles_collection, get_legacy_collection
from src.modules.articles.database import (
    get_all_locations,
    get_all_subjects,
    get_article_by_id,
    search_by_metadata,
)
from src.modules.editions.database import get_current_edition_ids

logger = logging.getLogger(__name__)


class ArticleSearch:
    """Search functionality for articles using semantic and metadata search."""

    def __init__(self) -> None:
        """Initialize article search."""
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)

        try:
            self.collection = get_articles_collection()
            # Also check legacy collection for backward compat
            self.legacy_collection = get_legacy_collection()
            if self.legacy_collection:
                logger.info("ArticleSearch: legacy collection available as fallback")
        except Exception as e:
            logger.error(f"ArticleSearch: failed to init collection: {e}")
            self.collection = None
            self.legacy_collection = None

    def semantic_search(
        self,
        query: str,
        top_k: int = RETRIEVAL_TOP_K,
        min_score: float = SIMILARITY_THRESHOLD,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for documents using semantic similarity.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            min_score: Minimum similarity score.
            publisher: Optional publisher name to filter results.

        Returns:
            List of matching chunks with metadata and scores.
        """
        if self.collection is None:
            logger.warning("No collection available for semantic search")
            return []

        logger.info(f"Article semantic search: '{query}' (top_k={top_k})" +
                     (f" [publisher={publisher}]" if publisher else ""))

        query_embedding = self.embedding_model.encode(query).tolist()

        # Search the articles collection
        chunks = self._query_collection(
            self.collection, "articles", query_embedding, top_k, min_score,
            publisher=publisher,
        )

        # Fallback: also search legacy collection if articles collection is empty
        if not chunks and self.legacy_collection:
            logger.info("Articles collection empty, falling back to legacy collection")
            chunks = self._query_collection(
                self.legacy_collection, "legacy", query_embedding, top_k, min_score,
                publisher=publisher,
            )

        logger.info(f"Article semantic search returned {len(chunks)} chunks")
        return chunks

    def _query_collection(
        self,
        collection: "chromadb.Collection",
        label: str,
        query_embedding: list[float],
        top_k: int,
        min_score: float,
        publisher: str | None = None,
    ) -> list[dict]:
        """Query a single Chroma collection and return formatted chunks."""
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if publisher:
            query_kwargs["where"] = {"publisher": publisher}
            logger.info(f"  Filtering {label} collection to publisher: {publisher}")

        results = collection.query(**query_kwargs)

        # Look up current edition IDs once per query for the boost step below.
        current_edition_ids = get_current_edition_ids(publisher)
        if current_edition_ids:
            logger.info(f"  Current edition ids for boost: {sorted(current_edition_ids)}")

        chunks = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                if score < min_score:
                    continue

                metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                # Freshness boost — matches the logic in QueryEngine.retrieve().
                publish_date = str(
                    metadata.get("edition_date", "") or metadata.get("publish_date", "")
                )
                freshness_boost = 1.0
                if publish_date:
                    try:
                        pd = datetime.strptime(publish_date, "%Y-%m-%d")
                        age_days = (datetime.now() - pd).days
                        if age_days <= 7:
                            freshness_boost = 1.15
                        elif age_days <= 30:
                            freshness_boost = 1.05
                    except (ValueError, TypeError):
                        pass

                # Current-edition boost.
                edition_boost = 1.0
                chunk_edition = str(metadata.get("edition_id", "") or "")
                if chunk_edition and chunk_edition in current_edition_ids:
                    edition_boost = EDITION_CURRENT_BOOST
                    logger.info(
                        f"    -> current-edition boost applied "
                        f"(edition_id={chunk_edition}, x{EDITION_CURRENT_BOOST}, "
                        f"title='{str(metadata.get('title',''))[:40]}')"
                    )

                chunk = {
                    "text": doc,
                    "metadata": metadata,
                    "score": score * freshness_boost * edition_boost,
                    "search_type": "semantic",
                }
                chunks.append(chunk)

        # Re-sort by boosted score so the best-boosted chunks come first.
        chunks.sort(key=lambda c: c["score"], reverse=True)

        logger.info(f"  Collection '{label}': {len(chunks)} results")
        return chunks

    def metadata_search(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        author: str | None = None,
        location: str | None = None,
        subject: str | None = None,
        limit: int = 20,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for articles by metadata filters.

        Args:
            date_from: Start date (YYYY-MM-DD).
            date_to: End date (YYYY-MM-DD).
            author: Author name (partial match).
            location: Location (partial match).
            subject: Subject/topic (partial match).
            limit: Maximum results.
            publisher: Optional publisher filter.

        Returns:
            List of matching articles with metadata.
        """
        logger.info(
            f"Metadata search: date={date_from} to {date_to}, "
            f"author={author}, location={location}, subject={subject}"
            + (f", publisher={publisher}" if publisher else "")
        )

        articles = search_by_metadata(
            date_from=date_from,
            date_to=date_to,
            author=author,
            location=location,
            subject=subject,
            limit=limit,
            publisher=publisher,
        )

        logger.info(f"Metadata search returned {len(articles)} articles")
        return articles

    def hybrid_search(
        self,
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        location: str | None = None,
        subject: str | None = None,
        top_k: int = RETRIEVAL_TOP_K,
        publisher: str | None = None,
    ) -> list[dict]:
        """Combine semantic search with metadata filtering.

        First filters by metadata, then performs semantic search
        on the filtered results.

        Args:
            query: Search query text.
            date_from: Start date filter.
            date_to: End date filter.
            location: Location filter.
            subject: Subject filter.
            top_k: Number of results.

        Returns:
            List of matching chunks.
        """
        logger.info(f"Hybrid search: '{query}' with metadata filters")

        # If no metadata filters are active, fall through to pure semantic search.
        # Otherwise metadata_search(limit=50) imposes an implicit "50 most recent
        # articles by publish_date" cap that silently excludes historical-seeded
        # editions — the intersection with the semantic top-k then drops to zero
        # whenever the best-matching chunks live in older editions.
        if not any([date_from, date_to, location, subject]):
            logger.info("Hybrid search: no metadata filters — delegating to semantic_search")
            return self.semantic_search(query, top_k=top_k, publisher=publisher)

        # Get articles matching metadata
        articles = self.metadata_search(
            date_from=date_from,
            date_to=date_to,
            location=location,
            subject=subject,
            limit=50,
            publisher=publisher,
        )

        if not articles:
            logger.info("No articles match metadata filters")
            return []

        # Get doc_ids of matching articles
        doc_ids = {article["doc_id"] for article in articles}
        logger.info(f"Filtering semantic search to {len(doc_ids)} articles")

        # Perform semantic search (with publisher filter)
        all_chunks = self.semantic_search(query, top_k=top_k * 2, min_score=0.0, publisher=publisher)

        # Filter to only chunks from matching articles
        filtered_chunks = [
            chunk for chunk in all_chunks if chunk["metadata"].get("doc_id") in doc_ids
        ]

        # Re-rank by score
        filtered_chunks.sort(key=lambda x: x["score"], reverse=True)

        result = filtered_chunks[:top_k]
        logger.info(f"Hybrid search returned {len(result)} chunks")
        return result

    def historical_search(
        self,
        query: str,
        top_k: int = 3,
        publisher: str | None = None,
    ) -> list[dict]:
        """Semantic search restricted to past editions (excludes the current edition).

        Use when the user wants background or "has this been covered before?".

        Args:
            query: Search query text.
            top_k: Number of results to return.
            publisher: Optional publisher filter.

        Returns:
            List of chunks from non-current editions only.
        """
        logger.info(
            f"Historical search: '{query}' (top_k={top_k})"
            + (f" [publisher={publisher}]" if publisher else "")
        )

        current_ids = get_current_edition_ids(publisher)
        if not current_ids:
            logger.info("No current edition marked — historical_search behaves like semantic_search")

        # Over-fetch so we have enough left after dropping current-edition chunks.
        raw = self.semantic_search(
            query,
            top_k=max(top_k * 3, 10),
            publisher=publisher,
        )

        filtered = [
            c for c in raw
            if str(c.get("metadata", {}).get("edition_id", "") or "") not in current_ids
        ]

        for c in filtered:
            c["search_type"] = "historical"

        result = filtered[:top_k]
        logger.info(
            f"Historical search returned {len(result)} chunks "
            f"(excluded {len(raw) - len(filtered)} current-edition chunks)"
        )
        return result

    def get_article_details(self, doc_id: str) -> dict | None:
        """Get full article details by document ID.

        Args:
            doc_id: Document identifier.

        Returns:
            Article metadata or None.
        """
        return get_article_by_id(doc_id)

    def get_chunks_for_article(self, doc_id: str) -> list[dict]:
        """Get all chunks for a specific article.

        Args:
            doc_id: Document identifier.

        Returns:
            List of chunks for the article.
        """
        if self.collection is None:
            return []

        # Query by metadata filter
        results = self.collection.get(
            where={"doc_id": doc_id},
            include=["documents", "metadatas"],
        )

        chunks = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"]):
                chunk = {
                    "text": doc,
                    "metadata": results["metadatas"][i] if results["metadatas"] else {},
                    "score": 1.0,
                    "search_type": "direct",
                }
                chunks.append(chunk)

        # Sort by chunk index
        chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
        return chunks


def get_article_tools_schema() -> list[dict]:
    """Get the article tools schema with dynamic subjects and locations.

    Returns:
        List of tool definitions with actual subject/location values from database.
    """
    subjects = get_all_subjects()
    locations = get_all_locations()

    if subjects:
        subject_desc = f"Subject/topic to filter by. Available: {', '.join(subjects)}"
    else:
        subject_desc = "Subject/topic to filter by"

    if locations:
        location_desc = f"Location/region to filter by. Available: {', '.join(locations)}"
    else:
        location_desc = "Location/region to filter by"

    return [
        {
            "name": "semantic_search",
            "description": "Search for articles using natural language. Best for finding content based on meaning and concepts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query describing what to find",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "metadata_search",
            "description": "Search for articles by metadata like date, author, location, or subject. Use when user asks for specific filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                    },
                    "author": {
                        "type": "string",
                        "description": "Author name to search for",
                    },
                    "location": {
                        "type": "string",
                        "description": location_desc,
                    },
                    "subject": {
                        "type": "string",
                        "description": subject_desc,
                    },
                },
            },
        },
        {
            "name": "hybrid_search",
            "description": "Combine semantic search with metadata filters. Use when user wants specific content within filtered criteria.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                    },
                    "location": {
                        "type": "string",
                        "description": location_desc,
                    },
                    "subject": {
                        "type": "string",
                        "description": subject_desc,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "historical_search",
            "description": (
                "Search PAST editions only (excludes the current week). Use when the user "
                "asks about history, past coverage, or 'has this been covered before', or "
                "when you want to enrich a current-edition answer with background context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default 3, max 5)",
                    },
                },
                "required": ["query"],
            },
        },
    ]


# Keep static schema for backward compatibility
ARTICLE_TOOLS_SCHEMA = get_article_tools_schema()
