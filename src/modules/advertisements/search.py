"""Search functionality for advertisements."""

import logging

from src.modules.advertisements.database import (
    get_all_ad_categories,
    search_advertisements,
)
from src.core.database import get_connection

logger = logging.getLogger(__name__)


def _search_by_advertiser_name(query: str, limit: int = 10) -> list[dict]:
    """Search ads by advertiser name match (case-insensitive LIKE).

    Args:
        query: User query that may contain an advertiser name.
        limit: Max results.

    Returns:
        List of matching ad rows as dicts.
    """
    conn = get_connection()
    cursor = conn.cursor()
    # Split query into words and try matching advertiser against each
    # significant word pair or the full query
    cursor.execute(
        """
        SELECT * FROM advertisements
        WHERE (
            advertiser LIKE ? OR advertiser LIKE ?
            OR product_name LIKE ? OR product_name LIKE ?
        )
        AND status = 'active'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            f"%{query}%", f"%{query.title()}%",
            f"%{query}%", f"%{query.title()}%",
            limit,
        ),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


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
            "url": ad.get("url", ""),
            "content_type": "advertisement",
            "location": ad.get("location", ""),
        },
        "score": score,
        "search_type": "advertisement",
    }


class AdvertisementSearch:
    """Search functionality for advertisements."""

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        on_sale_only: bool = False,
    ) -> list[dict]:
        """Search for advertisements with advertiser-name boost.

        Combines:
        1. Advertiser-name matching (boosted, if query provided)
        2. DB filter search (category, price, on_sale_only)

        Args:
            query: Optional search query — used for advertiser-name matching.
            category: Product category filter.
            max_price: Maximum price filter.
            on_sale_only: Only return items on sale.

        Returns:
            List of matching advertisements.
        """
        logger.info(
            f"Advertisement search: query={query}, category={category}, "
            f"max_price={max_price}, on_sale_only={on_sale_only}"
        )

        results = []
        seen_ids: set[str] = set()

        # 1. Advertiser-name boost: if query provided, try direct name match
        if query:
            name_matches = _search_by_advertiser_name(query)
            if name_matches:
                logger.info(
                    f"Advertiser-name boost: {len(name_matches)} matches "
                    f"for '{query}'"
                )
            for ad in name_matches:
                ad_id = ad.get("ad_id", "")
                if ad_id not in seen_ids:
                    seen_ids.add(ad_id)
                    results.append(_format_ad_result(ad, score=1.5))  # Boosted

        # 2. Standard DB filter search
        ads = search_advertisements(
            category=category,
            max_price=max_price,
            on_sale_only=on_sale_only,
            active_only=True,
        )
        for ad in ads:
            ad_id = ad.get("ad_id", "")
            if ad_id not in seen_ids:
                seen_ids.add(ad_id)
                results.append(_format_ad_result(ad, score=1.0))

        # Sort by score descending (boosted matches first)
        results.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(
            f"Advertisement search returned {len(results)} ads "
            f"({len(seen_ids)} unique)"
        )
        return results


def get_ad_tools_schema() -> list[dict]:
    """Get the advertisement tools schema with dynamic categories.

    Returns:
        List of tool definitions with actual category values from database.
    """
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
                "parameter supports advertiser name matching."
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


# Keep static schema for backward compatibility (will have generic description)
AD_TOOLS_SCHEMA = get_ad_tools_schema()
