"""Database operations for content items (skeleton).

Content items represent individual pieces of content (articles, ads, etc.)
extracted from newspaper editions. This table unifies tracking across
content types for the Popular Network ingestion pipeline.
"""

import logging

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the content_items skeleton table."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edition_id INTEGER,
            publisher_id INTEGER,
            content_type TEXT NOT NULL DEFAULT 'article',
            title TEXT,
            raw_text TEXT,
            cleaned_text TEXT,
            page_number INTEGER,
            status TEXT DEFAULT 'pending',
            extraction_method TEXT,
            source_region_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (edition_id) REFERENCES editions(id),
            FOREIGN KEY (publisher_id) REFERENCES publishers(id)
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_items_edition "
        "ON content_items(edition_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_items_publisher "
        "ON content_items(publisher_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_items_type "
        "ON content_items(content_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_items_status "
        "ON content_items(status)"
    )

    conn.commit()
    conn.close()
    logger.info("Content items table initialized (skeleton)")
