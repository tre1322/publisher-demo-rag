"""Analytics module for tracking content impressions and URL clicks."""

from src.modules.analytics.database import (
    get_click_stats,
    get_impression_stats,
    init_table,
    log_content_impression,
    log_url_click,
)

__all__ = [
    "init_table",
    "log_content_impression",
    "log_url_click",
    "get_impression_stats",
    "get_click_stats",
]
