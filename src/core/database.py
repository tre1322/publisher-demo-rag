"""Shared database utilities."""

import logging
import sqlite3

from src.core.config import DATA_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_PATH = DATA_DIR / "articles.db"


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory.

    Returns:
        SQLite connection with Row factory enabled.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_all_tables() -> None:
    """Initialize all database tables.

    This imports and initializes tables from all modules.
    Single authoritative runtime init path — each module's init_table()
    handles CREATE TABLE IF NOT EXISTS + ALTER TABLE migrations.
    """
    logger.info(f"Initializing database at {DATABASE_PATH}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Import modules to trigger their table initialization
    from src.modules.advertisements import database as ads_db
    from src.modules.analytics import database as analytics_db
    from src.modules.articles import database as articles_db
    from src.modules.content_items import database as content_items_db
    from src.modules.conversations import database as conversations_db
    from src.modules.editions import database as editions_db
    from src.modules.events import database as events_db
    from src.modules.costs.tracker import init_cost_table
    from src.modules.organizations import database as orgs_db
    from src.modules.publishers import database as publishers_db
    from src.business_frontend import auth as biz_auth
    from src.modules.sponsored import database as sponsored_db

    init_cost_table()
    _init_rss_feeds_table()

    orgs_db.init_table()
    publishers_db.init_table()
    biz_auth.init_tables()
    sponsored_db.init_table()
    articles_db.init_table()
    ads_db.init_table()
    events_db.init_table()
    conversations_db.init_table()
    analytics_db.init_table()
    editions_db.init_table()
    content_items_db.init_table()

    # Seed default publishers (idempotent)
    publishers_db.seed_publishers()

    # Verify critical migration columns exist
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(advertisements)")
    ad_cols = {row[1] for row in cursor.fetchall()}
    cursor.execute("PRAGMA table_info(editions)")
    ed_cols = {row[1] for row in cursor.fetchall()}
    conn.close()

    if "checksum" not in ad_cols:
        logger.error(
            f"CRITICAL: advertisements.checksum MISSING after init_all_tables! "
            f"DB: {DATABASE_PATH}, columns: {sorted(ad_cols)}"
        )
    else:
        logger.info(
            f"All database tables initialized at {DATABASE_PATH} "
            f"(advertisements.checksum: OK, editions.checksum: {'OK' if 'checksum' in ed_cols else 'MISSING'})"
        )


def _init_rss_feeds_table() -> None:
    """Create publisher_rss_feeds table if it doesn't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publisher_rss_feeds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            publisher   TEXT NOT NULL,
            rss_url     TEXT NOT NULL,
            label       TEXT,
            last_synced_at TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(publisher, rss_url)
        )
    """)
    conn.commit()
    conn.close()


def get_rss_feeds(publisher: str | None = None) -> list[dict]:
    """Return all RSS feed configs, optionally filtered by publisher."""
    conn = get_connection()
    if publisher:
        rows = conn.execute(
            "SELECT * FROM publisher_rss_feeds WHERE publisher = ? ORDER BY publisher, label",
            (publisher,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM publisher_rss_feeds ORDER BY publisher, label"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_rss_feed(publisher: str, rss_url: str, label: str = "") -> int:
    """Insert or update an RSS feed config. Returns the row id."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO publisher_rss_feeds (publisher, rss_url, label)
           VALUES (?, ?, ?)
           ON CONFLICT(publisher, rss_url) DO UPDATE SET label=excluded.label""",
        (publisher, rss_url, label or rss_url),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def mark_rss_synced(feed_id: int) -> None:
    """Update last_synced_at timestamp for a feed."""
    conn = get_connection()
    conn.execute(
        "UPDATE publisher_rss_feeds SET last_synced_at = CURRENT_TIMESTAMP WHERE id = ?",
        (feed_id,),
    )
    conn.commit()
    conn.close()


def delete_rss_feed(feed_id: int) -> None:
    """Remove an RSS feed config."""
    conn = get_connection()
    conn.execute("DELETE FROM publisher_rss_feeds WHERE id = ?", (feed_id,))
    conn.commit()
    conn.close()


def get_all_publishers() -> list[str]:
    """Get all unique publisher names across all tables.

    Returns:
        Sorted list of unique publisher names.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT publisher FROM articles WHERE publisher IS NOT NULL
        UNION
        SELECT DISTINCT publisher FROM advertisements WHERE publisher IS NOT NULL
        UNION
        SELECT DISTINCT publisher FROM events WHERE publisher IS NOT NULL
    """)

    publishers = sorted([row[0] for row in cursor.fetchall()])
    conn.close()

    return publishers
