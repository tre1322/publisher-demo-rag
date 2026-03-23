"""Advertisements module for product ads and deals."""

from src.modules.advertisements.database import (
    clear_advertisements,
    get_advertisement_by_id,
    get_advertisement_count,
    get_all_ad_categories,
    get_random_advertisements,
    init_table,
    insert_advertisement,
    insert_edition_advertisement,
    search_advertisements,
)
from src.modules.advertisements.search import (
    AD_TOOLS_SCHEMA,
    AdvertisementSearch,
    get_ad_tools_schema,
)

__all__ = [
    "AD_TOOLS_SCHEMA",
    "AdvertisementSearch",
    "get_ad_tools_schema",
    "clear_advertisements",
    "get_advertisement_by_id",
    "get_advertisement_count",
    "get_all_ad_categories",
    "get_random_advertisements",
    "init_table",
    "insert_advertisement",
    "insert_edition_advertisement",
    "search_advertisements",
]
