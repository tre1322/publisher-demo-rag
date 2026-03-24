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
    from src.modules.organizations import database as orgs_db
    from src.modules.publishers import database as publishers_db

    orgs_db.init_table()
    publishers_db.init_table()
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
