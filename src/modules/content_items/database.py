"""Database operations for content items.

Content items represent individual pieces of content (articles, stories,
notices, etc.) extracted from newspaper editions. This is the normalized
record that drives homepage, article pages, search, and AI retrieval.
"""

import json
import logging

from src.core.database import get_connection

logger = logging.getLogger(__name__)


def init_table() -> None:
    """Initialize the content_items table with full schema."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            edition_id INTEGER,
            publisher_id INTEGER,
            content_type TEXT NOT NULL DEFAULT 'news',
            headline TEXT,
            subheadline TEXT,
            byline TEXT,
            raw_text TEXT,
            cleaned_web_text TEXT,
            section TEXT,
            start_page INTEGER,
            end_page INTEGER,
            jump_pages_json TEXT,
            print_prominence_score REAL DEFAULT 0,
            extraction_confidence REAL DEFAULT 0,
            homepage_eligible INTEGER DEFAULT 0,
            homepage_score REAL DEFAULT 0,
            publish_status TEXT DEFAULT 'draft',
            is_stitched INTEGER DEFAULT 0,
            block_count INTEGER DEFAULT 0,
            column_id INTEGER,
            span_columns INTEGER DEFAULT 1,
            bbox_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (edition_id) REFERENCES editions(id),
            FOREIGN KEY (publisher_id) REFERENCES publishers(id)
        )
    """)

    # Self-healing: add columns that may be missing from legacy schema
    _add_column_if_missing(cursor, "content_items", "headline", "TEXT")
    _add_column_if_missing(cursor, "content_items", "subheadline", "TEXT")
    _add_column_if_missing(cursor, "content_items", "byline", "TEXT")
    _add_column_if_missing(cursor, "content_items", "cleaned_web_text", "TEXT")
    _add_column_if_missing(cursor, "content_items", "section", "TEXT")
    _add_column_if_missing(cursor, "content_items", "start_page", "INTEGER")
    _add_column_if_missing(cursor, "content_items", "end_page", "INTEGER")
    _add_column_if_missing(cursor, "content_items", "jump_pages_json", "TEXT")
    _add_column_if_missing(cursor, "content_items", "print_prominence_score", "REAL DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "extraction_confidence", "REAL DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "homepage_eligible", "INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "homepage_score", "REAL DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "publish_status", "TEXT DEFAULT 'draft'")
    _add_column_if_missing(cursor, "content_items", "is_stitched", "INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "block_count", "INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "content_items", "column_id", "INTEGER")
    _add_column_if_missing(cursor, "content_items", "span_columns", "INTEGER DEFAULT 1")
    _add_column_if_missing(cursor, "content_items", "bbox_json", "TEXT")
    _add_column_if_missing(cursor, "content_items", "edition_date", "TEXT")

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
        "ON content_items(publish_status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_items_homepage "
        "ON content_items(homepage_eligible, homepage_score DESC)"
    )

    conn.commit()
    conn.close()
    logger.info("Content items table initialized")


def _add_column_if_missing(cursor, table: str, column: str, col_type: str) -> None:
    """Add a column to a table if it doesn't exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logger.info(f"Added column {column} to {table}")


# ── CRUD Operations ──


def insert_content_item(
    edition_id: int,
    publisher_id: int,
    content_type: str = "news",
    headline: str = "",
    subheadline: str = "",
    byline: str = "",
    raw_text: str = "",
    cleaned_web_text: str = "",
    section: str = "",
    start_page: int = None,
    end_page: int = None,
    jump_pages: list = None,
    print_prominence_score: float = 0,
    extraction_confidence: float = 0,
    homepage_eligible: bool = False,
    homepage_score: float = 0,
    publish_status: str = "draft",
    is_stitched: bool = False,
    block_count: int = 0,
    column_id: int = None,
    span_columns: int = 1,
    bbox: list = None,
    edition_date: str = None,
) -> int:
    """Insert a content item and return its ID."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO content_items (
            edition_id, publisher_id, content_type, headline, subheadline,
            byline, raw_text, cleaned_web_text, section, start_page, end_page,
            jump_pages_json, print_prominence_score, extraction_confidence,
            homepage_eligible, homepage_score, publish_status, is_stitched,
            block_count, column_id, span_columns, bbox_json, edition_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        edition_id, publisher_id, content_type, headline, subheadline,
        byline, raw_text, cleaned_web_text, section, start_page, end_page,
        json.dumps(jump_pages or []), print_prominence_score, extraction_confidence,
        1 if homepage_eligible else 0, homepage_score, publish_status,
        1 if is_stitched else 0, block_count, column_id, span_columns,
        json.dumps(bbox) if bbox else None, edition_date,
    ))

    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def get_content_items_for_edition(edition_id: int) -> list[dict]:
    """Get all content items for an edition."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM content_items WHERE edition_id = ? ORDER BY start_page, id",
        (edition_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()

    # Parse JSON fields
    for row in rows:
        row["homepage_eligible"] = bool(row.get("homepage_eligible"))
        row["is_stitched"] = bool(row.get("is_stitched"))
        if row.get("jump_pages_json"):
            row["jump_pages"] = json.loads(row["jump_pages_json"])
        else:
            row["jump_pages"] = []
        if row.get("bbox_json"):
            row["bbox"] = json.loads(row["bbox_json"])
        else:
            row["bbox"] = None

    return rows


def get_content_item(item_id: int) -> dict | None:
    """Get a single content item by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM content_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    columns = [desc[0] for desc in cursor.description]
    item = dict(zip(columns, row))
    item["homepage_eligible"] = bool(item.get("homepage_eligible"))
    item["is_stitched"] = bool(item.get("is_stitched"))
    if item.get("jump_pages_json"):
        item["jump_pages"] = json.loads(item["jump_pages_json"])
    else:
        item["jump_pages"] = []
    return item


def get_homepage_content(publisher_id: int, limit: int = 20, section: str = "") -> list[dict]:
    """Get homepage-eligible content from the current edition, sorted by score.

    Only returns articles from the most recently uploaded edition(s) marked
    is_current=1 in the editions table. If no current edition exists, falls
    back to all published content for that publisher.

    When a publisher+section combination has editor-curated pins
    (homepage_pins table), those pins replace the auto-computed list
    entirely. "Pinned = exactly the homepage, nothing else."

    Args:
        publisher_id: Filter to this publisher. Pass 0 to get all publishers.
        limit: Max results to return.
        section: Optional content_type filter (e.g. 'news', 'sports').
    """
    # Editor-curated pins override auto-scoring for this publisher+section.
    # Only applies when we have both a specific publisher AND a section;
    # the cross-publisher "Regional Top Stories" column (publisher_id=0)
    # continues to use auto-scoring.
    if publisher_id and section:
        from src.core.database import get_pinned_content_item_ids
        pinned_ids = get_pinned_content_item_ids(publisher_id, section)
        if pinned_ids:
            conn = get_connection()
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(pinned_ids))
            cursor.execute(
                f"SELECT * FROM content_items WHERE id IN ({placeholders})",
                pinned_ids,
            )
            columns = [desc[0] for desc in cursor.description]
            rows_by_id = {
                row[0]: dict(zip(columns, row)) for row in cursor.fetchall()
            }
            conn.close()
            # Preserve slot order (pinned_ids is already slot-ordered)
            ordered = [rows_by_id[i] for i in pinned_ids if i in rows_by_id]
            for row in ordered:
                row["homepage_eligible"] = bool(row.get("homepage_eligible"))
                row["is_stitched"] = bool(row.get("is_stitched"))
                if row.get("jump_pages_json"):
                    row["jump_pages"] = json.loads(row["jump_pages_json"])
                else:
                    row["jump_pages"] = []
            return ordered[:limit]
        # No pins for this publisher+section → return empty list per user spec
        # ("pinned = exactly the homepage, nothing else")
        # BUT only enforce strictness for sections where pinning is supported.
        if section in ("news", "sports"):
            return []
        # Other sections (obituary, opinion, features, etc.) fall through to
        # auto-scoring below — pinning is only wired for news+sports today.
    conn = get_connection()
    cursor = conn.cursor()

    def _fetch(current_only: bool) -> list[dict]:
        where_clauses = [
            "ci.homepage_eligible = 1",
            "ci.publish_status = 'published'",
        ]
        params: list = []

        if current_only:
            where_clauses.append("e.is_current = 1")

        if publisher_id:
            where_clauses.append("ci.publisher_id = ?")
            params.append(publisher_id)

        if section:
            where_clauses.append("ci.content_type = ?")
            params.append(section)

        params.append(limit)

        cursor.execute(f"""
            SELECT ci.*
            FROM content_items ci
            JOIN editions e ON ci.edition_id = e.id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY ci.homepage_score DESC, ci.id DESC
            LIMIT ?
        """, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    # Prefer current edition; fall back to all published if none marked current
    rows = _fetch(current_only=True)
    if not rows:
        rows = _fetch(current_only=False)

    conn.close()

    for row in rows:
        row["homepage_eligible"] = bool(row.get("homepage_eligible"))
        row["is_stitched"] = bool(row.get("is_stitched"))
        if row.get("jump_pages_json"):
            row["jump_pages"] = json.loads(row["jump_pages_json"])
        else:
            row["jump_pages"] = []

    return rows


def delete_content_items_for_edition(edition_id: int) -> int:
    """Delete all content items for an edition (for re-run). Returns count deleted."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM content_items WHERE edition_id = ?", (edition_id,))
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def publish_edition_content(edition_id: int) -> int:
    """Set all content items for an edition to 'published'. Returns count."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE content_items SET publish_status = 'published' WHERE edition_id = ?",
        (edition_id,),
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count
