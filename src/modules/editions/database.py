"""Database operations for newspaper editions."""

import logging
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the editions table."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS editions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publisher TEXT NOT NULL,
            publication_name TEXT,
            edition_date TEXT,
            source_pdf_path TEXT NOT NULL,
            page_count INTEGER,
            article_count INTEGER DEFAULT 0,
            ad_count INTEGER DEFAULT 0,
            processing_status TEXT DEFAULT 'pending',
            processing_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_publisher ON editions(publisher)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_date ON editions(edition_date)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_status ON editions(processing_status)"
    )

    conn.commit()
    conn.close()
    logger.info("Editions table initialized")


def insert_edition(
    publisher: str,
    source_pdf_path: str,
    publication_name: str | None = None,
    edition_date: str | None = None,
    page_count: int | None = None,
) -> int:
    """Insert a new edition record.

    Returns:
        The new edition ID.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO editions
        (publisher, publication_name, edition_date, source_pdf_path, page_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (publisher, publication_name, edition_date, source_pdf_path, page_count),
    )

    edition_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return edition_id


def update_edition_status(
    edition_id: int,
    status: str,
    error: str | None = None,
    article_count: int | None = None,
    ad_count: int | None = None,
    page_count: int | None = None,
) -> None:
    """Update edition processing status and counts."""
    conn = get_connection()
    cursor = conn.cursor()

    updates = ["processing_status = ?", "updated_at = ?"]
    params: list = [status, datetime.now().isoformat()]

    if error is not None:
        updates.append("processing_error = ?")
        params.append(error)

    if article_count is not None:
        updates.append("article_count = ?")
        params.append(article_count)

    if ad_count is not None:
        updates.append("ad_count = ?")
        params.append(ad_count)

    if page_count is not None:
        updates.append("page_count = ?")
        params.append(page_count)

    params.append(edition_id)
    cursor.execute(
        f"UPDATE editions SET {', '.join(updates)} WHERE id = ?",
        params,
    )

    conn.commit()
    conn.close()


def get_edition(edition_id: int) -> dict | None:
    """Get an edition by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM editions WHERE id = ?", (edition_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_editions(limit: int = 100) -> list[dict]:
    """Get all editions, most recent first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM editions ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_edition_by_pdf_path(source_pdf_path: str) -> dict | None:
    """Check if an edition with this PDF path already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM editions WHERE source_pdf_path = ?", (source_pdf_path,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_edition_count() -> int:
    """Get total number of editions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM editions")
    count = cursor.fetchone()[0]
    conn.close()
    return count
