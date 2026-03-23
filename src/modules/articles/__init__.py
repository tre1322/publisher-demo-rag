"""Articles module for news article storage and search."""

from src.modules.articles.database import (
    clear_articles,
    get_all_locations,
    get_all_subjects,
    get_article_by_id,
    get_article_count,
    get_articles_for_edition,
    get_articles_needing_review,
    get_date_range,
    get_recent_articles,
    init_table,
    insert_article,
    insert_edition_article,
    search_by_metadata,
    update_article,
)
from src.modules.articles.search import (
    ARTICLE_TOOLS_SCHEMA,
    ArticleSearch,
    get_article_tools_schema,
)

__all__ = [
    "ARTICLE_TOOLS_SCHEMA",
    "ArticleSearch",
    "get_article_tools_schema",
    "clear_articles",
    "get_all_locations",
    "get_all_subjects",
    "get_article_by_id",
    "get_article_count",
    "get_articles_for_edition",
    "get_articles_needing_review",
    "get_date_range",
    "get_recent_articles",
    "init_table",
    "insert_article",
    "insert_edition_article",
    "search_by_metadata",
    "update_article",
]
