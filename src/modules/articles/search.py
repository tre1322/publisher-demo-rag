"""Search functionality for articles."""

import logging

from sentence_transformers import SentenceTransformer

from src.core.config import (
    EMBEDDING_MODEL,
    RETRIEVAL_TOP_K,
    SIMILARITY_THRESHOLD,
)
from src.core.vector_store import get_articles_collection, get_legacy_collection
from src.modules.articles.database import (
    get_all_locations,
    get_all_subjects,
    get_article_by_id,
    search_by_metadata,
)

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

        chunks = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                if score < min_score:
                    continue

                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                chunk = {
                    "text": doc,
                    "metadata": metadata,
                    "score": score,
                    "search_type": "semantic",
                }
                chunks.append(chunk)

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
    ]


# Keep static schema for backward compatibility
ARTICLE_TOOLS_SCHEMA = get_article_tools_schema()
