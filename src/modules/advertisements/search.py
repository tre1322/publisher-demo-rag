"""Search functionality for advertisements."""

import logging

from src.modules.advertisements.database import (
    get_all_ad_categories,
    search_advertisements,
)

logger = logging.getLogger(__name__)


class AdvertisementSearch:
    """Search functionality for advertisements."""

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        max_price: float | None = None,
        on_sale_only: bool = False,
    ) -> list[dict]:
        """Search for advertisements.

        Args:
            query: Optional search query for semantic matching (future use).
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

        # Get ads from database
        ads = search_advertisements(
            category=category,
            max_price=max_price,
            on_sale_only=on_sale_only,
            active_only=True,
        )

        # Format ads as results
        results = []
        for ad in ads:
            # Format price info
            price_info = ""
            if ad.get("price"):
                price_info = f"${ad['price']:.2f}"
                if ad.get("original_price") and ad.get("discount_percent"):
                    price_info += f" (was ${ad['original_price']:.2f}, {ad['discount_percent']:.0f}% off)"

            # Use description if available, fall back to cleaned_text/raw_text
            # (uploaded ads store content in raw_text/cleaned_text, not description)
            content = (
                ad.get("description")
                or ad.get("cleaned_text")
                or ad.get("raw_text")
                or ""
            )
            # Truncate very long content for the result text
            if len(content) > 500:
                content = content[:500] + "..."

            text_parts = [f"[Sponsored] {ad['advertiser']} advertisement"]
            if ad.get("product_name") and ad["product_name"] != ad.get("advertiser"):
                text_parts[0] += f" - {ad['product_name']}"
            if content:
                text_parts.append(content)
            if price_info:
                text_parts.append(price_info)

            result = {
                "text": ": ".join(text_parts[:1]) + "\n" + "\n".join(text_parts[1:]),
                "metadata": {
                    "ad_id": ad["ad_id"],
                    "doc_id": ad["ad_id"],
                    "product_name": ad.get("product_name", ""),
                    "advertiser": ad.get("advertiser", ""),
                    "title": ad.get("advertiser", ""),
                    "category": ad.get("category", ""),
                    "price": ad.get("price"),
                    "discount_percent": ad.get("discount_percent"),
                    "url": ad.get("url", ""),
                    "content_type": "advertisement",
                },
                "score": 1.0,
                "search_type": "advertisement",
            }
            results.append(result)

        logger.info(f"Advertisement search returned {len(results)} ads")
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
            "description": "Search for product advertisements and deals. Use when user asks about products, sales, deals, discounts, or shopping.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search query for products",
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
