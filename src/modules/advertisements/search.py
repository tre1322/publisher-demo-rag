"""Search functionality for advertisements."""

import logging

from sentence_transformers import SentenceTransformer

from src.core.config import EMBEDDING_MODEL, RETRIEVAL_TOP_K, SIMILARITY_THRESHOLD
from src.core.database import get_connection
from src.core.vector_store import get_ads_collection
from src.modules.advertisements.database import (
    get_all_ad_categories,
    search_advertisements,
)
from src.modules.editions.database import get_current_edition_ids

logger = logging.getLogger(__name__)

# Keep in sync with EDITION_CURRENT_BOOST in src/query_engine.py
# and src/modules/articles/search.py. Current-edition ads rank
# ~1.5x above historical ads on the same topic.
EDITION_CURRENT_BOOST = 1.5


def _search_by_advertiser_name(query: str, limit: int = 10, publisher: str | None = None) -> list[dict]:
    """Search ads by advertiser name match (case-insensitive LIKE)."""
    conn = get_connection()
    cursor = conn.cursor()

    sql = """
        SELECT * FROM advertisements
        WHERE (
            advertiser LIKE ? OR advertiser LIKE ?
            OR product_name LIKE ? OR product_name LIKE ?
        )
        AND status = 'active'
    """
    params: list = [
        f"%{query}%", f"%{query.title()}%",
        f"%{query}%", f"%{query.title()}%",
    ]

    if publisher:
        sql += " AND publisher = ?"
        params.append(publisher)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _get_ad_by_id(ad_id: str) -> dict | None:
    """Fetch a single ad row from SQLite by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM advertisements WHERE ad_id = ?", (ad_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _format_ad_result(ad: dict, score: float = 1.0) -> dict:
    """Format a DB ad row into a search result dict.

    Uses the enriched embedding_text > ocr_text > cleaned_text > raw_text
    > description preference order.
    """
    # Format price info
    price_info = ""
    if ad.get("price"):
        price_info = f"${ad['price']:.2f}"
        if ad.get("original_price") and ad.get("discount_percent"):
            price_info += (
                f" (was ${ad['original_price']:.2f}, "
                f"{ad['discount_percent']:.0f}% off)"
            )

    # Preferred content order
    content = (
        ad.get("embedding_text")
        or ad.get("ocr_text")
        or ad.get("cleaned_text")
        or ad.get("raw_text")
        or ad.get("description")
        or ""
    )
    if len(content) > 500:
        content = content[:500] + "..."

    text_parts = [f"[Sponsored] {ad.get('advertiser', 'Unknown')} advertisement"]
    if ad.get("product_name") and ad["product_name"] != ad.get("advertiser"):
        text_parts[0] += f" - {ad['product_name']}"
    if content:
        text_parts.append(content)
    if price_info:
        text_parts.append(price_info)

    return {
        "text": ": ".join(text_parts[:1]) + "\n" + "\n".join(text_parts[1:]),
        "metadata": {
            "ad_id": ad.get("ad_id", ""),
            "doc_id": ad.get("ad_id", ""),
            "product_name": ad.get("product_name", ""),
            "advertiser": ad.get("advertiser", ""),
            "title": ad.get("advertiser", ""),
            "category": ad.get("category", ""),
            "ad_category": ad.get("ad_category", ""),
            "price": ad.get("price"),
            "discount_percent": ad.get("discount_percent"),
            "url": f"/ad/{ad.get('ad_id', '')}" if ad.get("web_image_path") else ad.get("url", ""),
            "content_type": "advertisement",
            "location": ad.get("location", ""),
            "edition_id": ad.get("edition_id"),
        },
        "score": score,
        "search_type": "advertisement",
    }


class AdvertisementSearch:
    """Search functionality for advertisements."""

    def __init__(self) -> None:
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        try:
            self.collection = get_ads_collection()
        except Exception as e:
            logger.error(f"Failed to init ads collection: {e}")
            self.collection = None

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        on_sale_only: bool = False,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search for advertisements with semantic + name + filter search.

        Combines (in priority order):
        1. Semantic vector search against ads Chroma collection
        2. Advertiser-name matching (boosted)
        3. DB filter search (category, price, on_sale_only)

        Args:
            query: Search query — used for semantic and name matching.
            category: Product category filter.
            max_price: Maximum price filter.
            on_sale_only: Only return items on sale.
            publisher: Filter to this publisher's ads (priority, not hard filter).

        Returns:
            List of matching advertisements.
        """
        logger.info(
            f"Advertisement search: query={query}, category={category}, "
            f"max_price={max_price}, on_sale_only={on_sale_only}, publisher={publisher}"
        )

        results = []
        seen_ids: set[str] = set()

        # 1. Semantic vector search (highest quality matches)
        if query and self.collection is not None:
            try:
                semantic_results = self._semantic_search(query, publisher=publisher)
                logger.info(
                    f"  Ad semantic search: {len(semantic_results)} results "
                    f"from ads collection"
                )
                for r in semantic_results:
                    ad_id = r.get("metadata", {}).get("doc_id", "")
                    if ad_id and ad_id not in seen_ids:
                        seen_ids.add(ad_id)
                        results.append(r)
            except Exception as e:
                logger.error(f"Ad semantic search failed: {e}")

        # 2. Advertiser-name boost
        if query:
            name_matches = _search_by_advertiser_name(query, publisher=publisher)
            if name_matches:
                logger.info(
                    f"  Advertiser-name boost: {len(name_matches)} matches"
                )
            for ad in name_matches:
                ad_id = ad.get("ad_id", "")
                if ad_id not in seen_ids:
                    seen_ids.add(ad_id)
                    results.append(_format_ad_result(ad, score=1.5))

        # 3. Standard DB filter search
        ads = search_advertisements(
            category=category,
            max_price=max_price,
            on_sale_only=on_sale_only,
            active_only=True,
            publisher=publisher,
        )
        for ad in ads:
            ad_id = ad.get("ad_id", "")
            if ad_id not in seen_ids:
                seen_ids.add(ad_id)
                results.append(_format_ad_result(ad, score=1.0))

        # Current-edition boost: multiply the score of ads whose edition_id
        # matches the publisher's current edition. Mirrors the articles-side
        # boost in QueryEngine.retrieve() / ArticleSearch._query_collection().
        current_edition_ids = get_current_edition_ids(publisher)
        if current_edition_ids:
            logger.info(
                f"  Ad boost: current edition ids = {sorted(current_edition_ids)}"
            )
            for r in results:
                ad_edition = str(r.get("metadata", {}).get("edition_id", "") or "")
                if ad_edition and ad_edition in current_edition_ids:
                    before = r.get("score", 0)
                    r["score"] = before * EDITION_CURRENT_BOOST
                    advertiser = r.get("metadata", {}).get("advertiser", "?")[:40]
                    logger.info(
                        f"    -> ad current-edition boost applied "
                        f"(edition_id={ad_edition}, '{advertiser}', "
                        f"{before:.3f} -> {r['score']:.3f})"
                    )

        # Sort by score descending
        results.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(
            f"Advertisement search returned {len(results)} ads "
            f"({len(seen_ids)} unique)"
        )
        return results

    def _semantic_search(
        self,
        query: str,
        top_k: int = RETRIEVAL_TOP_K,
        min_score: float = SIMILARITY_THRESHOLD,
        publisher: str | None = None,
    ) -> list[dict]:
        """Search the ads Chroma collection by semantic similarity."""
        if self.collection is None:
            return []

        query_embedding = self.embedding_model.encode(query).tolist()
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if publisher:
            query_kwargs["where"] = {"publisher": publisher}

        results = self.collection.query(**query_kwargs)

        chunks = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                if score < min_score:
                    continue

                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                ad_id = metadata.get("doc_id", "")

                # Enrich with full DB record if available
                db_ad = _get_ad_by_id(ad_id) if ad_id else None
                if db_ad:
                    result = _format_ad_result(db_ad, score=score + 0.5)  # Boost semantic matches
                else:
                    result = {
                        "text": doc,
                        "metadata": metadata,
                        "score": score + 0.5,
                        "search_type": "advertisement",
                    }

                advertiser = metadata.get("title", "Unknown")[:50]
                logger.info(
                    f"    Ad vector hit: '{advertiser}' score={score:.3f}"
                )
                chunks.append(result)

        return chunks


def get_ad_tools_schema() -> list[dict]:
    """Get the advertisement tools schema with dynamic categories."""
    categories = get_all_ad_categories()
    if categories:
        category_desc = f"Product category. Available: {', '.join(categories)}"
    else:
        category_desc = "Product category"

    return [
        {
            "name": "search_advertisements",
            "description": (
                "Search for product advertisements and deals. Use when user "
                "asks about products, sales, deals, discounts, shopping, or "
                "what a specific business/advertiser is promoting. The query "
                "parameter supports advertiser name matching and semantic search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query — use advertiser/business name for "
                            "specific lookups, or general terms for browsing"
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": category_desc,
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price filter",
                    },
                    "on_sale_only": {
                        "type": "boolean",
                        "description": "Only return items currently on sale",
                    },
                },
            },
        },
    ]


# Keep static schema for backward compatibility
AD_TOOLS_SCHEMA = get_ad_tools_schema()
