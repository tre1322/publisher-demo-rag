"""Database operations for newspaper editions, page regions, and review actions."""

import json
import logging
from datetime import datetime

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize editions, page_regions, and review_actions tables."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS editions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publication_id INTEGER,
            edition_date TEXT,
            issue_label TEXT,
            source_filename TEXT NOT NULL,
            checksum TEXT,
            page_count INTEGER,
            article_count INTEGER DEFAULT 0,
            ad_count INTEGER DEFAULT 0,
            processing_status TEXT DEFAULT 'pending',
            processing_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrate: add new columns if table already exists with old schema
    for col, coltype in [
        ("publication_id", "INTEGER"),
        ("issue_label", "TEXT"),
        ("checksum", "TEXT"),
        ("processing_notes", "TEXT"),
        ("source_filename", "TEXT"),
        ("publisher_id", "INTEGER"),
        ("pdf_path", "TEXT"),
        ("upload_status", "TEXT DEFAULT 'pending'"),
        ("extraction_status", "TEXT DEFAULT 'not_started'"),
        ("homepage_batch_status", "TEXT DEFAULT 'not_started'"),
        ("is_current", "INTEGER DEFAULT 0"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE editions ADD COLUMN {col} {coltype}")
        except Exception:
            pass

    # Detect old schema with publisher NOT NULL — recreate if empty
    try:
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='editions'")
        schema = cursor.fetchone()
        if schema and schema[0] and "publisher TEXT NOT NULL" in schema[0]:
            cursor.execute("SELECT COUNT(*) FROM editions")
            count = cursor.fetchone()[0]
            if count == 0:
                cursor.execute("DROP TABLE editions")
                cursor.execute("""
                    CREATE TABLE editions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        publication_id INTEGER,
                        edition_date TEXT,
                        issue_label TEXT,
                        source_filename TEXT NOT NULL DEFAULT '',
                        checksum TEXT,
                        page_count INTEGER,
                        article_count INTEGER DEFAULT 0,
                        ad_count INTEGER DEFAULT 0,
                        processing_status TEXT DEFAULT 'pending',
                        processing_notes TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS page_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edition_id INTEGER NOT NULL,
            article_id TEXT,
            page_number INTEGER NOT NULL,
            region_type TEXT NOT NULL,
            bbox_json TEXT,
            raw_text TEXT,
            role TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (edition_id) REFERENCES editions(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS review_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            user_identifier TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_pub ON editions(publication_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_publisher ON editions(publisher_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_date ON editions(edition_date)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_status ON editions(processing_status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_editions_checksum ON editions(checksum)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_regions_edition ON page_regions(edition_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_regions_article ON page_regions(article_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_actions_article ON review_actions(article_id)"
    )

    conn.commit()
    conn.close()
    logger.info("Editions, page_regions, review_actions tables initialized")


def insert_edition(
    source_filename: str,
    publication_id: int | None = None,
    edition_date: str | None = None,
    issue_label: str | None = None,
    checksum: str | None = None,
    page_count: int | None = None,
    publisher_id: int | None = None,
    pdf_path: str | None = None,
    upload_status: str = "pending",
    extraction_status: str = "not_started",
    homepage_batch_status: str = "not_started",
    # Legacy compat
    publisher: str | None = None,
    source_pdf_path: str | None = None,
    publication_name: str | None = None,
) -> int:
    """Insert a new edition record. Returns the new edition ID."""
    conn = get_connection()
    cursor = conn.cursor()

    # Use source_filename or fall back to source_pdf_path for compat
    filename = source_filename or source_pdf_path or ""

    cursor.execute(
        """INSERT INTO editions
        (publication_id, edition_date, issue_label, source_filename, checksum,
         page_count, publisher_id, pdf_path, upload_status, extraction_status,
         homepage_batch_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (publication_id, edition_date, issue_label, filename, checksum,
         page_count, publisher_id, pdf_path, upload_status, extraction_status,
         homepage_batch_status),
    )

    edition_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return edition_id


def get_edition_by_checksum(checksum: str, publication_id: int | None = None) -> dict | None:
    """Check if an edition with this checksum already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    if publication_id:
        cursor.execute(
            "SELECT * FROM editions WHERE checksum = ? AND publication_id = ?",
            (checksum, publication_id),
        )
    else:
        cursor.execute("SELECT * FROM editions WHERE checksum = ?", (checksum,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


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
        updates.append("processing_notes = ?")
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM editions WHERE id = ?", (edition_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_editions(limit: int = 100) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM editions ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_edition_by_pdf_path(source_pdf_path: str) -> dict | None:
    """Legacy compat: check by source_filename."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM editions WHERE source_filename = ?", (source_pdf_path,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def mark_edition_current(edition_id: int, publisher_id: int) -> None:
    """Mark an edition as current, clearing is_current on all other editions for this publisher."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE editions SET is_current = 0 WHERE publisher_id = ?",
        (publisher_id,),
    )
    cursor.execute(
        "UPDATE editions SET is_current = 1 WHERE id = ?",
        (edition_id,),
    )
    conn.commit()
    conn.close()
    logger.info(f"Edition {edition_id} marked as current for publisher {publisher_id}")


def get_edition_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM editions")
    count = cursor.fetchone()[0]
    conn.close()
    return count


# ── Page Regions ──

def insert_page_region(
    edition_id: int,
    page_number: int,
    region_type: str,
    article_id: str | None = None,
    bbox_json: str | None = None,
    raw_text: str | None = None,
    role: str | None = None,
    metadata_json: str | None = None,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO page_regions
        (edition_id, article_id, page_number, region_type, bbox_json, raw_text, role, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (edition_id, article_id, page_number, region_type, bbox_json, raw_text, role, metadata_json),
    )
    region_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return region_id


def get_regions_for_article(article_id: str) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM page_regions WHERE article_id = ? ORDER BY page_number, id",
        (article_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_regions_for_edition(edition_id: int) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM page_regions WHERE edition_id = ? ORDER BY page_number, id",
        (edition_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── Review Actions ──

def insert_review_action(
    article_id: str,
    action_type: str,
    before_json: dict | None = None,
    after_json: dict | None = None,
    user_identifier: str | None = None,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO review_actions
        (article_id, action_type, before_json, after_json, user_identifier)
        VALUES (?, ?, ?, ?, ?)""",
        (
            article_id,
            action_type,
            json.dumps(before_json) if before_json else None,
            json.dumps(after_json) if after_json else None,
            user_identifier,
        ),
    )
    action_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return action_id


def get_review_actions_for_article(article_id: str) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM review_actions WHERE article_id = ? ORDER BY created_at DESC",
        (article_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
