"""Database operations for analytics tracking."""

import logging
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize analytics tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Content impressions - what was shown to users
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            content_type TEXT NOT NULL,
            content_id TEXT NOT NULL,
            shown_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)

    # URL clicks - what users clicked on
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS url_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            content_type TEXT NOT NULL,
            content_id TEXT NOT NULL,
            url TEXT NOT NULL,
            clicked_at TEXT NOT NULL,
            user_agent TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)

    # Indexes for efficient queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_impressions_conversation
        ON content_impressions(conversation_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_impressions_content
        ON content_impressions(content_type, content_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_clicks_conversation
        ON url_clicks(conversation_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_clicks_content
        ON url_clicks(content_type, content_id)
    """)

    conn.commit()
    conn.close()
    logger.info("Analytics tables initialized")


def log_content_impression(
    content_type: str,
    content_id: str,
    conversation_id: int | None = None,
    message_id: int | None = None,
) -> int:
    """Log that content was shown to a user.

    Args:
        content_type: Type of content ('article', 'event', 'advertisement').
        content_id: ID of the content (doc_id, event_id, or ad_id).
        conversation_id: Optional conversation ID.
        message_id: Optional message ID.

    Returns:
        ID of the inserted impression record.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO content_impressions
        (conversation_id, message_id, content_type, content_id, shown_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            message_id,
            content_type,
            content_id,
            datetime.now().isoformat(),
        ),
    )

    impression_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logger.debug(f"Logged impression: {content_type}/{content_id}")
    return impression_id  # type: ignore[return-value]


def log_url_click(
    content_type: str,
    content_id: str,
    url: str,
    conversation_id: int | None = None,
    user_agent: str | None = None,
) -> int:
    """Log that a user clicked on a URL.

    Args:
        content_type: Type of content ('article', 'event', 'advertisement').
        content_id: ID of the content.
        url: The URL that was clicked.
        conversation_id: Optional conversation ID.
        user_agent: Optional user agent string.

    Returns:
        ID of the inserted click record.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO url_clicks
        (conversation_id, content_type, content_id, url, clicked_at, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            content_type,
            content_id,
            url,
            datetime.now().isoformat(),
            user_agent,
        ),
    )

    click_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"Logged click: {content_type}/{content_id} -> {url[:50]}...")
    return click_id  # type: ignore[return-value]


def get_impression_stats() -> dict:
    """Get statistics about content impressions.

    Returns:
        Dictionary with impression statistics.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Total impressions by type
    cursor.execute("""
        SELECT content_type, COUNT(*) as count
        FROM content_impressions
        GROUP BY content_type
    """)
    by_type = {row["content_type"]: row["count"] for row in cursor.fetchall()}

    # Total unique content shown
    cursor.execute("""
        SELECT COUNT(DISTINCT content_type || ':' || content_id) as unique_content
        FROM content_impressions
    """)
    unique_content = cursor.fetchone()["unique_content"]

    # Top shown content
    cursor.execute("""
        SELECT content_type, content_id, COUNT(*) as impressions
        FROM content_impressions
        GROUP BY content_type, content_id
        ORDER BY impressions DESC
        LIMIT 10
    """)
    top_content = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return {
        "by_type": by_type,
        "unique_content_shown": unique_content,
        "top_content": top_content,
    }


def get_click_stats() -> dict:
    """Get statistics about URL clicks.

    Returns:
        Dictionary with click statistics.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Total clicks by type
    cursor.execute("""
        SELECT content_type, COUNT(*) as count
        FROM url_clicks
        GROUP BY content_type
    """)
    by_type = {row["content_type"]: row["count"] for row in cursor.fetchall()}

    # Total clicks
    cursor.execute("SELECT COUNT(*) as total FROM url_clicks")
    total_clicks = cursor.fetchone()["total"]

    # Top clicked content
    cursor.execute("""
        SELECT content_type, content_id, COUNT(*) as clicks
        FROM url_clicks
        GROUP BY content_type, content_id
        ORDER BY clicks DESC
        LIMIT 10
    """)
    top_clicked = [dict(row) for row in cursor.fetchall()]

    # Click-through rate by type
    cursor.execute("""
        SELECT
            i.content_type,
            COUNT(DISTINCT i.content_id) as shown,
            COUNT(DISTINCT c.content_id) as clicked
        FROM content_impressions i
        LEFT JOIN url_clicks c
            ON i.content_type = c.content_type
            AND i.content_id = c.content_id
        GROUP BY i.content_type
    """)
    ctr_by_type = {}
    for row in cursor.fetchall():
        shown = row["shown"]
        clicked = row["clicked"]
        ctr = (clicked / shown * 100) if shown > 0 else 0
        ctr_by_type[row["content_type"]] = {
            "shown": shown,
            "clicked": clicked,
            "ctr_percent": round(ctr, 1),
        }

    conn.close()

    return {
        "total_clicks": total_clicks,
        "by_type": by_type,
        "top_clicked": top_clicked,
        "ctr_by_type": ctr_by_type,
    }
